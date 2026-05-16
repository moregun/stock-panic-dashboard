#!/usr/bin/env python3
"""
A股恐慌看板监控脚本
监测沪深300恐慌级别，触发条件时飞书告警，并更新可视化看板

触发条件（必须同时满足）：
  1. 沪深300当日跌幅达标
  2. 当日成交量 > 近20日均量 x 1.5倍（放量下跌）
  3. 全A股下跌个股占比 > 80%（普跌）

恐慌分级：
  一级恐慌：跌幅 2.0% ~ 2.9%
  二级恐慌：跌幅 3.0% ~ 3.9% 且 沪深300 PE-TTM <= 13
  三级恐慌：跌幅 >= 4.0%
"""

import tushare as ts
import pandas as pd
import json
import os
import sys
import io
import time
import requests
import re
from datetime import datetime, timedelta

# 修复 Windows 编码（暂时注释，调试用）
# if sys.platform == "win32":
#     sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
#     sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH  = os.path.join(SCRIPT_DIR, "state.json")
HISTORY_PATH = os.path.join(SCRIPT_DIR, "history.json")
HTML_PATH    = os.path.join(SCRIPT_DIR, "panic_dashboard.html")

# 全局
pro = None
safe_request_count = 0
cfg = {}


def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}")
    sys.stdout.flush()


# ── 配置加载 ─────────────────────────────────────────────────────

def load_config():
    global cfg, pro
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    ts.set_token(cfg["tushare_token"])
    pro = ts.pro_api()
    log(f"配置已加载，指数：{cfg['index_code']}")
    return cfg


# ── 限流请求 ─────────────────────────────────────────────────────

def safe_request(func, *args, **kwargs):
    global safe_request_count
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            safe_request_count += 1
            if safe_request_count % 150 == 0:
                log(f"已调用 {safe_request_count} 次API，休息2秒...")
                time.sleep(2)
            result = func(*args, **kwargs)
            if result is not None and not result.empty:
                return result
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                log(f"  API重试({attempt + 1}/{MAX_RETRIES}): {e}")
                time.sleep(1)
                continue
            else:
                log(f"  API失败: {e}")
        time.sleep(0.3)
    return pd.DataFrame()


# ── 交易日判断 ──────────────────────────────────────────────────

def get_latest_trade_date(max_lookback=10):
    """
    获取最近的有效交易日（向前回溯 max_lookback 天）
    返回: 'YYYYMMDD' 或 None
    """
    today = datetime.now()
    for i in range(max_lookback):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        d_str = d.strftime("%Y%m%d")
        df = safe_request(pro.index_daily, ts_code=cfg["index_code"], trade_date=d_str)
        if not df.empty:
            if float(df.iloc[0]["close"]) > 0:
                return d_str
    return None


# ── 数据获取 ─────────────────────────────────────────────────────

def get_index_data(trade_date):
    """
    获取沪深300当日数据 + PE-TTM
    返回: dict {close, pre_close, change_pct, vol, pe_ttm} 或 None
    """
    log(f"获取沪深300数据：{trade_date}...")
    df = safe_request(pro.index_daily, ts_code=cfg["index_code"], trade_date=trade_date)
    if df.empty:
        log("  沪深300数据为空")
        return None

    row = df.iloc[0]
    close = float(row["close"])
    pre_close = None
    if "pre_close" in df.columns and pd.notna(row["pre_close"]):
        pre_close = float(row["pre_close"])
    vol = float(row["vol"]) if "vol" in df.columns else 0

    # 计算涨跌幅
    if pre_close is not None and pre_close > 0:
        change_pct = (close - pre_close) / pre_close * 100
    elif "pct_chg" in df.columns and pd.notna(row.get("pct_chg")):
        change_pct = float(row["pct_chg"])
    else:
        change_pct = None

    # 获取 PE-TTM
    pe_ttm = None
    basic_df = safe_request(pro.index_dailybasic, ts_code=cfg["index_code"], trade_date=trade_date)
    if not basic_df.empty and "pe_ttm" in basic_df.columns:
        pe_val = basic_df.iloc[0]["pe_ttm"]
        if pd.notna(pe_val):
            pe_ttm = float(pe_val)

    log(f"  收盘：{close:.2f}，跌幅：{change_pct:.2f}%，成交量：{vol:.0f}（手），PE-TTM：{pe_ttm}")
    return {
        "close": close,
        "pre_close": pre_close,
        "change_pct": change_pct,
        "volume": vol,
        "pe_ttm": pe_ttm,
        "trade_date": trade_date,
    }


def get_volume_ratio(trade_date, window=20):
    """
    计算量比 = 今日成交量 / 近window日均量
    使用缓存机制避免每次回溯21天
    返回: (volume_ratio, today_vol, avg_vol) 或 (None, today_vol, 0)
    """
    log(f"计算量比（最近{window}日均量）...")
    cache_path = os.path.join(SCRIPT_DIR, "volume_cache.json")

    # 尝试从缓存读取
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    # 检查缓存是否今天已计算过
    cache_date = cache.get("date", "")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if cache_date == today_str and "ratio" in cache:
        log(f"  使用缓存量比：{cache['ratio']:.2f}x")
        return cache["ratio"], cache.get("today_vol", 0), cache.get("avg_vol", 0)

    # 需要重新计算：收集最近 window+1 个交易日
    dates = []
    d = datetime.strptime(trade_date, "%Y%m%d")
    collected = 0
    iter_count = 0
    while collected < window + 1:
        iter_count += 1
        if iter_count > 100:
            log("  回溯天数过多，中止")
            break
        d_str = d.strftime("%Y%m%d")
        df = safe_request(pro.index_daily, ts_code=cfg["index_code"], trade_date=d_str)
        if not df.empty and float(df.iloc[0]["close"]) > 0:
            dates.append(d_str)
            collected += 1
        d -= timedelta(days=1)

    if not dates:
        log("  量比计算失败：无有效数据")
        return None, 0, 0

    today_vol = None
    hist_vols = []
    for i, ds in enumerate(dates):
        df = safe_request(pro.index_daily, ts_code=cfg["index_code"], trade_date=ds)
        if df.empty:
            continue
        v = float(df.iloc[0]["vol"]) if "vol" in df.columns else 0
        if i == 0:
            today_vol = v
        else:
            hist_vols.append(v)

    if not today_vol or not hist_vols:
        log("  量比计算失败：数据不足")
        return None, today_vol, 0

    avg_vol = sum(hist_vols) / len(hist_vols)
    ratio = today_vol / avg_vol if avg_vol > 0 else None
    log(f"  今日量：{today_vol:.0f}，{window}日均量：{avg_vol:.0f}，量比：{ratio:.2f}x")

    # 写入缓存
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({
                "date": today_str,
                "ratio": ratio,
                "today_vol": today_vol,
                "avg_vol": avg_vol,
            }, f, ensure_ascii=False)
    except Exception:
        pass

    return ratio, today_vol, avg_vol


def get_market_drop_ratio(trade_date):
    """
    分页获取全A股当日数据，计算下跌个股占比
    返回: (drop_ratio%, total, drop_count) 或 (None, 0, 0)
    """
    log(f"获取全A股数据（分页）：{trade_date}...")
    all_stocks = []
    offset = 0
    limit = 5000

    while True:
        df = safe_request(pro.daily, trade_date=trade_date, offset=offset, limit=limit)
        if df.empty:
            break
        all_stocks.append(df)
        offset += limit
        if len(df) < limit:
            break

    if not all_stocks:
        log("  全A股数据为空（非交易日？）")
        return None, 0, 0

    merged = pd.concat(all_stocks, ignore_index=True)
    total = len(merged)
    drop_count = 0
    for _, row in merged.iterrows():
        close = float(row.get("close", 0))
        pre_close = float(row.get("pre_close", 0))
        if pre_close > 0 and close < pre_close:
            drop_count += 1

    ratio = (drop_count / total * 100) if total > 0 else 0
    log(f"  全A股 {total} 只，下跌 {drop_count} 只，占比 {ratio:.1f}%")
    return ratio, total, drop_count


# ── 恐慌判断 ─────────────────────────────────────────────────────

def check_panic_conditions(index_data, volume_ratio, market_drop_ratio, cfg):
    """
    检查三个条件是否同时满足
    返回: (all_met: bool, details: dict)
    """
    t = cfg["thresholds"]
    change_pct = index_data["change_pct"]
    details = {
        "cond1_met": False,
        "cond1_value": change_pct,
        "cond2_met": False,
        "cond2_value": volume_ratio,
        "cond3_met": False,
        "cond3_value": market_drop_ratio,
    }

    # 条件1：跌幅达标（跌超 2.5%，即 change_pct < -2.5）
    if change_pct is not None and change_pct <= -t["drop_level1_min"]:
        details["cond1_met"] = True

    # 条件2：放量（量比 >= 1.5x）
    if volume_ratio is not None and volume_ratio >= t["volume_ratio_threshold"]:
        details["cond2_met"] = True

    # 条件3：普跌（下跌占比 >= 80%）
    if market_drop_ratio is not None and market_drop_ratio >= t["market_drop_ratio_threshold"]:
        details["cond3_met"] = True

    all_met = details["cond1_met"] and details["cond2_met"] and details["cond3_met"]
    return all_met, details


def classify_panic_level(change_pct, pe_ttm, cfg):
    """
    恐慌分级
    返回: (level, level_name)  level: 0=未触发, 1/2/3
    """
    t = cfg["thresholds"]
    abs_drop = abs(change_pct)

    if abs_drop >= t["drop_level3_min"]:
        return 3, "三级恐慌"
    elif abs_drop >= t["drop_level2_min"] and pe_ttm is not None and pe_ttm <= t["level2_pe_ttm_max"]:
        return 2, "二级恐慌"
    elif abs_drop >= t["drop_level1_min"]:
        return 1, "一级恐慌"
    else:
        return 0, "未触发"


# ── 状态管理（防重复告警）────────────────────────────────────────

def load_state():
    if not os.path.exists(STATE_PATH):
        return {"last_alert_date": "", "alerted_levels": {"1": False, "2": False, "3": False}, "last_check_date": ""}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_alert(state, level, today_str):
    """
    判断今日该级别是否已告警
    若 last_alert_date != today_str，自动重置 alerted_levels
    返回: (should_alert: bool, state)
    """
    if state.get("last_alert_date") != today_str:
        state["alerted_levels"] = {"1": False, "2": False, "3": False}
        state["last_alert_date"] = today_str

    key = str(level)
    if state["alerted_levels"].get(key, False):
        return False, state

    state["alerted_levels"][key] = True
    state["last_check_date"] = today_str
    return True, state


# ── 飞书通知 ─────────────────────────────────────────────────────

def send_feishu(index_data, volume_ratio, market_drop_ratio, level, level_name, cfg):
    """发送飞书告警通知"""
    webhook = cfg.get("feishu_webhook", "")
    if not webhook:
        log("  未配置飞书 webhook，跳过通知")
        return

    change_pct = index_data["change_pct"]
    pe_ttm = index_data.get("pe_ttm")
    t = cfg["thresholds"]

    cond1 = f"✅ 沪深300跌 {abs(change_pct):.2f}%（>={t['drop_level1_min']}%）"
    cond2 = f"✅ 放量下跌（量比 {volume_ratio:.2f}x，>={t['volume_ratio_threshold']}x）"
    cond3 = f"✅ 全A股 {market_drop_ratio:.1f}% 个股下跌（>={t['market_drop_ratio_threshold']}%）"

    lines = [
        f"🚨 A股恐慌告警",
        f"级别：{level_name}（跌幅 {abs(change_pct):.2f}%）",
        "______________________________",
        "触发条件：",
        cond1,
        cond2,
        cond3,
    ]
    if pe_ttm is not None:
        lines.append("______________________________")
        lines.append(f"沪深300 PE-TTM：{pe_ttm:.2f}")
    lines.append("______________________________")
    lines.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")

    try:
        content_parts = []
        for line in lines:
            if line.startswith("___"):
                content_parts.append([{"tag": "text", "text": "─" * 30}])
            else:
                content_parts.append([{"tag": "text", "text": line}])

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": f"🚨 A股{level_name}",
                        "content": content_parts,
                    }
                }
            }
        }

        resp = requests.post(webhook, json=payload, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            log("  [飞书] 通知发送成功")
        else:
            log(f"  [飞书] 通知发送失败: {result}")
    except Exception as e:
        log(f"  [飞书] 通知异常: {e}")


# ── 历史记录 ──────────────────────────────────────────────────────

def append_history(history, index_data, volume_ratio, market_drop_ratio, level, level_name):
    """追加一条恐慌事件到历史"""
    entry = {
        "date": index_data["trade_date"],
        "level": level,
        "level_name": level_name,
        "drop_pct": round(index_data["change_pct"], 2),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        "market_drop_ratio": round(market_drop_ratio, 1) if market_drop_ratio else None,
        "pe_ttm": round(index_data.get("pe_ttm"), 2) if index_data.get("pe_ttm") else None,
        "signal_close": round(index_data["close"], 2),
        "return_5d": None,
        "return_20d": None,
        "return_60d": None,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    history.append(entry)
    if len(history) > 200:
        history = history[-200:]
    return history


def update_signal_returns(history):
    """计算并更新历史信号的后续收益（5日/20日/60日）"""
    if not history:
        return history

    # 找出需要更新的信号（有收盘价但某收益字段缺失）
    need_update = []
    for entry in history:
        if "signal_close" not in entry or not entry["signal_close"]:
            continue
        if (entry.get("return_5d") is None or
            entry.get("return_20d") is None or
            entry.get("return_60d") is None):
            need_update.append(entry)

    if not need_update:
        return history

    # 获取从最早信号日到当前的所有日线
    earliest = min(e["date"] for e in need_update)
    today = datetime.now().strftime("%Y%m%d")

    log(f"  更新 {len(need_update)} 条信号的后续收益（{earliest} ~ {today}）...")
    df = safe_request(pro.index_daily, ts_code=cfg["index_code"], start_date=earliest, end_date=today)
    if df.empty:
        return history

    # 按日期排序
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 建立日期到索引的映射
    date_to_idx = {}
    for i, row in df.iterrows():
        date_to_idx[str(row["trade_date"])] = i

    updated_count = 0
    for entry in need_update:
        signal_date = entry["date"]
        signal_close = entry.get("signal_close")
        if not signal_close or signal_date not in date_to_idx:
            continue

        idx = date_to_idx[signal_date]
        updated = False

        if entry.get("return_5d") is None and idx + 5 < len(df):
            future_close = float(df.iloc[idx + 5]["close"])
            entry["return_5d"] = round((future_close - signal_close) / signal_close * 100, 2)
            updated = True

        if entry.get("return_20d") is None and idx + 20 < len(df):
            future_close = float(df.iloc[idx + 20]["close"])
            entry["return_20d"] = round((future_close - signal_close) / signal_close * 100, 2)
            updated = True

        if entry.get("return_60d") is None and idx + 60 < len(df):
            future_close = float(df.iloc[idx + 60]["close"])
            entry["return_60d"] = round((future_close - signal_close) / signal_close * 100, 2)
            updated = True

        if updated:
            updated_count += 1

    log(f"  已更新 {updated_count} 条信号的收益数据")
    return history


def backfill_history():
    """
    回溯过去两年的恐慌信号，结果合并写入 history.json。
    仅补充尚未记录的信号（按日期去重）。
    """
    global cfg, pro
    load_config()

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")
    # 需要多取20天来计算最初的量比
    earlier_date = (datetime.now() - timedelta(days=750)).strftime("%Y%m%d")

    log("=" * 60)
    log(f"  开始回溯历史恐慌信号：{start_date} ~ {end_date}")
    log("=" * 60)

    # 1. 获取沪深300历史数据（含额外20天用于量比计算）
    log("获取沪深300历史数据...")
    df = safe_request(pro.index_daily, ts_code=cfg["index_code"],
                      start_date=earlier_date, end_date=end_date)
    if df.empty:
        log("无历史数据")
        return

    df = df.sort_values("trade_date").reset_index(drop=True)
    log(f"  获取到 {len(df)} 条日线数据")

    # 2. 计算每日量比（20日均值）
    log("计算每日量比...")
    volume_ratios = {}
    for i in range(20, len(df)):
        row = df.iloc[i]
        trade_date = str(row["trade_date"])
        vol = float(row["vol"])
        hist_vols = [float(df.iloc[i - j]["vol"]) for j in range(1, 21)]
        avg_vol = sum(hist_vols) / len(hist_vols)
        ratio = vol / avg_vol if avg_vol > 0 else 0
        volume_ratios[trade_date] = round(ratio, 2)

    log(f"  量比计算完成，共 {len(volume_ratios)} 个交易日")

    # 3. 加载已有历史，用于去重
    existing = load_history()
    existing_dates = {e.get("date") for e in existing if e.get("date")}
    log(f"  已有历史记录 {len(existing_dates)} 条，将跳过重复日期")

    t = cfg["thresholds"]
    new_entries = []
    candidate_count = 0

    for i in range(20, len(df)):
        row = df.iloc[i]
        trade_date = str(row["trade_date"])

        if trade_date in existing_dates:
            continue

        close = float(row["close"])
        pre_close = float(df.iloc[i - 1]["close"])
        change_pct = (close - pre_close) / pre_close * 100

        # 条件1：跌幅 >= 2.5%
        if change_pct > -t["drop_level1_min"]:
            continue

        # 条件2：量比 >= 1.5x
        vol_ratio = volume_ratios.get(trade_date)
        if vol_ratio is None or vol_ratio < t["volume_ratio_threshold"]:
            continue

        candidate_count += 1
        log(f"  候选 #{candidate_count}：{trade_date} 跌幅{change_pct:.2f}% 量比{vol_ratio:.2f}x，获取全A股数据...")

        # 条件3：获取全A股下跌占比
        market_drop_ratio, _, _ = get_market_drop_ratio(trade_date)

        if market_drop_ratio is None or market_drop_ratio < t["market_drop_ratio_threshold"]:
            log(f"    ❌ 条件3未满足（下跌占比 {market_drop_ratio:.1f}%）")
            time.sleep(0.5)
            continue

        # 三个条件都满足
        log(f"    ✅ 三个条件满足，记录恐慌信号")

        # 获取PE-TTM
        pe_ttm = None
        basic_df = safe_request(pro.index_dailybasic, ts_code=cfg["index_code"], trade_date=trade_date)
        if not basic_df.empty and "pe_ttm" in basic_df.columns:
            pe_val = basic_df.iloc[0]["pe_ttm"]
            if pd.notna(pe_val):
                pe_ttm = float(pe_val)

        # 分级
        level, level_name = classify_panic_level(change_pct, pe_ttm, cfg)

        entry = {
            "date": trade_date,
            "level": level,
            "level_name": level_name,
            "drop_pct": round(change_pct, 2),
            "volume_ratio": round(vol_ratio, 2),
            "market_drop_ratio": round(market_drop_ratio, 1),
            "pe_ttm": round(pe_ttm, 2) if pe_ttm else None,
            "signal_close": round(close, 2),
            "return_5d": None,
            "return_20d": None,
            "return_60d": None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backfilled": True,
        }
        new_entries.append(entry)
        existing_dates.add(trade_date)
        time.sleep(0.5)

    if not new_entries:
        log("未发现新的历史恐慌信号")
        log("=" * 60)
        return

    log(f"回溯完成，新发现 {len(new_entries)} 个恐慌信号")

    # 4. 合并并去重（按日期）
    merged = existing[:]
    merged.extend(new_entries)
    # 按日期去重，保留最新的
    seen = {}
    for e in merged:
        d = e.get("date", "")
        if d not in seen:
            seen[d] = e
    merged = list(seen.values())
    merged.sort(key=lambda x: x.get("date", ""))

    # 5. 计算后续收益
    log("计算后续收益...")
    merged = update_signal_returns(merged)

    # 6. 保存
    save_history(merged)
    log(f"历史信号已保存（共 {len(merged)} 条）")
    log("=" * 60)


def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ── 更新 HTML 看板 ─────────────────────────────────────────────────

def update_dashboard_html(index_data, volume_ratio, market_drop_ratio, level, history):
    """用正则替换更新 HTML 中的 SNAP 数据对象"""
    if not os.path.exists(HTML_PATH):
        log("  HTML看板文件不存在，跳过更新")
        return

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # 构建 SNAP 对象
    snap = {
        "indexDrop": round(index_data["change_pct"], 2) if index_data["change_pct"] else 0,
        "volumeRatio": round(volume_ratio, 2) if volume_ratio else 0,
        "dropRatio": round(market_drop_ratio, 1) if market_drop_ratio else 0,
        "peTtm": round(index_data.get("pe_ttm"), 2) if index_data.get("pe_ttm") else None,
        "panicLevel": level,
        "indexClose": round(index_data["close"], 2),
        "updateTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tradeDate": index_data.get("trade_date", ""),
    }

    # 告警历史（最近20条，包含后续收益）
    alert_list = []
    for h in (history[-20:] if history else []):
        alert_list.append({
            "date": h.get("date"),
            "level": h.get("level"),
            "drop_pct": h.get("drop_pct"),
            "volume_ratio": h.get("volume_ratio"),
            "market_drop_ratio": h.get("market_drop_ratio"),
            "pe_ttm": h.get("pe_ttm"),
            "return_5d": h.get("return_5d"),
            "return_20d": h.get("return_20d"),
            "return_60d": h.get("return_60d"),
        })
    snap["alertHistory"] = alert_list

    # 用简单的字符串查找替换 SNAP 对象
    snap_js = json.dumps(snap, ensure_ascii=False, indent=2)
    snap_block = "const SNAP = " + snap_js + ";"

    # 查找并替换
    marker_start = "const SNAP = "
    idx_start = html.find(marker_start)
    if idx_start >= 0:
        # 找到对应的 };
        idx_semi = html.find("};", idx_start)
        if idx_semi >= 0:
            new_html = html[:idx_start] + snap_block + html[idx_semi + 2:]
        else:
            # 找不到 }; 直接追加
            new_html = html.replace("</script>", snap_block + "\n  </script>")
    else:
        # 找不到 const SNAP，在第一个 </script> 前插入
        new_html = html.replace("</script>", snap_block + "\n  </script>", 1)

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)

    log("  [HTML] 看板已更新")


# ── 主流程 ────────────────────────────────────────────────────────

def main():
    global cfg, pro

    # 检查命令行参数
    if "--backfill" in sys.argv:
        backfill_history()
        return

    log("=" * 60)
    log("  A股恐慌看板监控脚本启动")
    log(f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    # 1. 加载配置
    load_config()

    # 2. 获取最新交易日
    trade_date = get_latest_trade_date()
    if not trade_date:
        log("未找到有效交易日，退出")
        return
    log(f"最新交易日：{trade_date}")

    # 3. 获取沪深300数据
    index_data = get_index_data(trade_date)
    if not index_data:
        log("沪深300数据获取失败，退出")
        return

    # 4. 计算量比
    volume_ratio, _, _ = get_volume_ratio(trade_date)

    # 5. 计算全A股下跌占比
    market_drop_ratio, _, _ = get_market_drop_ratio(trade_date)

    # 6. 检查恐慌条件
    all_met, details = check_panic_conditions(index_data, volume_ratio, market_drop_ratio, cfg)

    log("-" * 60)
    c1 = details["cond1_value"]
    c2 = details["cond2_value"]
    c3 = details["cond3_value"]
    log(f"条件1（跌幅）：{c1:.2f}% -> {'✅' if details['cond1_met'] else '❌'}")
    if c2 is not None:
        log(f"条件2（量比）：{c2:.2f}x -> {'✅' if details['cond2_met'] else '❌'}")
    else:
        log("条件2（量比）：数据不足 -> ❌")
    if c3 is not None:
        log(f"条件3（普跌）：{c3:.1f}% -> {'✅' if details['cond3_met'] else '❌'}")
    else:
        log("条件3（普跌）：数据不足 -> ❌")
    log("-" * 60)

    if not all_met:
        log("三个条件未同时满足，不触发告警")
        history = load_history()
        history = update_signal_returns(history)
        save_history(history)
        update_dashboard_html(index_data, volume_ratio, market_drop_ratio, 0, history)
        log("完成（未触发）")
        return

    # 7. 分级
    level, level_name = classify_panic_level(index_data["change_pct"], index_data.get("pe_ttm"), cfg)
    log(f"★★ 触发{level_name}！级别：{level}")

    # 8. 防重复告警检查
    state = load_state()
    today_str = datetime.now().strftime("%Y-%m-%d")
    should, state = should_alert(state, level, today_str)

    if not should:
        log(f"今日该级别（{level}）已告警过，跳过")
    else:
        # 9. 发送飞书通知
        log("发送飞书告警通知...")
        send_feishu(index_data, volume_ratio, market_drop_ratio, level, level_name, cfg)

        # 10. 记录历史
        history = load_history()
        history = append_history(history, index_data, volume_ratio, market_drop_ratio, level, level_name)
        history = update_signal_returns(history)
        save_history(history)
        log(f"历史记录已更新（共 {len(history)} 条）")

    # 11. 更新状态
    save_state(state)

    # 12. 更新 HTML 看板
    history = load_history()
    history = update_signal_returns(history)
    save_history(history)
    log("更新可视化看板...")
    update_dashboard_html(index_data, volume_ratio, market_drop_ratio, level, history)

    log("=" * 60)
    log("  完成")
    log("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("FATAL ERROR:")
        traceback.print_exc()
        sys.exit(1)
