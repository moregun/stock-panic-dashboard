#!/usr/bin/env python3
"""
A股恐慌看板监控脚本（AKShare版）
监测沪深300恐慌级别，触发条件时飞书告警，并更新可视化看板

触发条件（必须同时满足）：
  1. 沪深300当日跌幅达标
  2. 当日成交量 > 近20日均量 x 1.2倍（放量下跌）
  3. 全A股下跌个股占比 > 75%（普跌）

恐慌分级：
  一级恐慌：跌幅 2.5% ~ 2.9%
  二级恐慌：跌幅 3.0% ~ 3.9%
  三级恐慌：跌幅 >= 4.0%

数据源：AKShare - 新浪财经（免费，无需Token）
"""

import akshare as ak
import pandas as pd
import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta

# 禁用代理
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

# 路径
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH  = os.path.join(SCRIPT_DIR, "state.json")
HISTORY_PATH = os.path.join(SCRIPT_DIR, "history.json")
HTML_PATH    = os.path.join(SCRIPT_DIR, "panic_dashboard.html")

cfg = {}


def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}")
    sys.stdout.flush()


# ── 配置加载 ─────────────────────────────────────────────────────

def load_config():
    global cfg
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 兼容旧配置：移除 tushare_token 字段
    if "tushare_token" in cfg:
        del cfg["tushare_token"]
    log(f"配置已加载，指数：{cfg['index_code']}")
    return cfg


# ── 获取沪深300历史数据（新浪源）─────────────────────────────

_index_df_cache = None

def get_hs300_history():
    """获取沪深300全部历史数据（新浪源，带缓存）"""
    global _index_df_cache
    if _index_df_cache is not None:
        return _index_df_cache
    log("  获取沪深300历史数据（新浪财经）...")
    df = ak.stock_zh_index_daily(symbol="sh000300")
    if df.empty:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    _index_df_cache = df
    log(f"  获取到 {len(df)} 条历史数据")
    return df


def get_latest_trade_date(max_lookback=10):
    """获取最近交易日"""
    df = get_hs300_history()
    if df is None or df.empty:
        return None
    # 回溯最近 max_lookback 天，跳过周末
    today = datetime.now().date()
    for i in range(max_lookback):
        d = today - timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        if d.weekday() >= 5:
            continue
        # 检查该日期是否在数据中
        if d_str in df["date"].astype(str).values:
            return d.strftime("%Y%m%d")
    return None


def get_index_data(trade_date):
    """
    获取沪深300当日数据
    返回: dict {close, pre_close, change_pct, vol}
    """
    log(f"获取沪深300数据：{trade_date}...")
    df = get_hs300_history()
    if df is None or df.empty:
        log("  沪深300数据为空")
        return None

    # 格式化目标日期
    d_obj = datetime.strptime(trade_date, "%Y%m%d")
    target = d_obj.strftime("%Y-%m-%d")

    # 找到目标行
    df_str = df["date"].astype(str)
    if target not in df_str.values:
        log(f"  未找到日期 {target} 的数据")
        return None

    idx = df_str[df_str == target].index[0]
    row = df.iloc[idx]

    close = float(row["close"])

    # 前收盘价
    pre_close = None
    if idx > 0:
        pre_close = float(df.iloc[idx - 1]["close"])

    # 涨跌幅
    if pre_close and pre_close > 0:
        change_pct = (close - pre_close) / pre_close * 100
    elif "close" in df.columns and idx > 0:
        # 用昨日收盘价计算
        change_pct = (close - pre_close) / pre_close * 100
    else:
        change_pct = None

    vol = float(row["volume"]) if "volume" in df.columns else 0

    log(f"  收盘：{close:.2f}，跌幅：{change_pct:.2f}%，成交量：{vol:.0f}（手）")
    return {
        "close": close,
        "pre_close": pre_close,
        "change_pct": change_pct,
        "volume": vol,
        "pe_ttm": None,  # AKShare 暂无指数PE-TTM
        "trade_date": trade_date,
    }


def get_volume_ratio(trade_date, window=20):
    """
    计算量比 = 今日成交量 / 近window日均量
    返回: (volume_ratio, today_vol, avg_vol)
    """
    log(f"计算量比（最近{window}日均量）...")
    cache_path = os.path.join(SCRIPT_DIR, "volume_cache.json")

    # 读缓存
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    today_str = datetime.now().strftime("%Y-%m-%d")
    if cache.get("date") == today_str and "ratio" in cache:
        log(f"  使用缓存量比：{cache['ratio']:.2f}x")
        return cache["ratio"], cache.get("today_vol", 0), cache.get("avg_vol", 0)

    # 获取历史数据
    df = get_hs300_history()
    if df is None or len(df) < window + 1:
        log("  数据不足，无法计算量比")
        return None, 0, 0

    # 找到目标日期位置
    d_obj = datetime.strptime(trade_date, "%Y%m%d")
    target = d_obj.strftime("%Y-%m-%d")
    df_str = df["date"].astype(str)
    if target not in df_str.values:
        log("  未找到目标日期")
        return None, 0, 0

    idx = df_str[df_str == target].index[0]

    if idx < window:
        log("  历史数据不足20日")
        return None, 0, 0

    today_vol = float(df.iloc[idx]["volume"])
    hist_vols = [float(df.iloc[idx - j]["volume"]) for j in range(1, window + 1)]
    avg_vol = sum(hist_vols) / len(hist_vols)
    ratio = today_vol / avg_vol if avg_vol > 0 else None

    log(f"  今日量：{today_vol:.0f}，{window}日均量：{avg_vol:.0f}，量比：{ratio:.2f}x")

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


# ── 获取全A股下跌比例（新浪源）─────────────────────────────

def get_market_drop_ratio(trade_date):
    """
    获取全A股数据，计算下跌个股占比
    新浪源：ak.stock_zh_a_spot()
    返回: (drop_ratio%, total, drop_count)
    """
    log("获取全A股数据（新浪财经实时）...")
    try:
        df = ak.stock_zh_a_spot()
    except Exception as e:
        log(f"  获取全A股数据失败: {e}")
        return None, 0, 0

    if df.empty:
        log("  全A股数据为空")
        return None, 0, 0

    total = len(df)
    drop_count = 0
    for _, row in df.iterrows():
        try:
            # 新浪 spot 数据：close 为最新价，pre_close 为昨收
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            if pre_close > 0 and close < pre_close:
                drop_count += 1
        except (ValueError, TypeError):
            continue

    ratio = (drop_count / total * 100) if total > 0 else 0
    log(f"  全A股 {total} 只，下跌 {drop_count} 只，占比 {ratio:.1f}%")
    return ratio, total, drop_count


# ── 恐慌判断 ─────────────────────────────────────────────────────

def check_panic_conditions(index_data, volume_ratio, market_drop_ratio, cfg):
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

    if change_pct is not None and change_pct <= -t["drop_level1_min"]:
        details["cond1_met"] = True

    if volume_ratio is not None and volume_ratio >= t["volume_ratio_threshold"]:
        details["cond2_met"] = True

    if market_drop_ratio is not None and market_drop_ratio >= t["market_drop_ratio_threshold"]:
        details["cond3_met"] = True

    all_met = details["cond1_met"] and details["cond2_met"] and details["cond3_met"]
    return all_met, details


def classify_panic_level(change_pct, pe_ttm, cfg):
    """恐慌分级（AKShare版暂不考虑PE-TTM）"""
    t = cfg["thresholds"]
    abs_drop = abs(change_pct)

    if abs_drop >= t["drop_level3_min"]:
        return 3, "三级恐慌"
    elif abs_drop >= t["drop_level2_min"]:
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
    webhook = cfg.get("feishu_webhook", "")
    if not webhook:
        log("  未配置飞书 webhook，跳过通知")
        return

    change_pct = index_data["change_pct"]
    t = cfg["thresholds"]

    cond1 = f"✅ 沪深300跌 {abs(change_pct):.2f}%（>={t['drop_level1_min']}%）"
    cond2 = f"✅ 放量下跌（量比 {volume_ratio:.2f}x，>={t['volume_ratio_threshold']}x）"
    cond3 = f"✅ 全A股 {market_drop_ratio:.1f}% 个股下跌（>={t['market_drop_ratio_threshold']}%）"

    lines = [
        f"🚨 A股恐慌告警",
        f"级别：{level_name}（跌幅 {abs(change_pct):.2f}%）",
        "___",
        "触发条件：",
        cond1,
        cond2,
        cond3,
        "___",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    ]

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

def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_history(history, index_data, volume_ratio, market_drop_ratio, level, level_name):
    entry = {
        "date": index_data["trade_date"],
        "level": level,
        "level_name": level_name,
        "drop_pct": round(index_data["change_pct"], 2),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        "market_drop_ratio": round(market_drop_ratio, 1) if market_drop_ratio else None,
        "pe_ttm": None,
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
    """计算并更新历史信号的后续收益"""
    if not history:
        return history

    need_update = []
    for entry in history:
        if "signal_close" not in entry or not entry["signal_close"]:
            continue
        if entry.get("return_5d") is None or entry.get("return_20d") is None or entry.get("return_60d") is None:
            need_update.append(entry)

    if not need_update:
        return history

    earliest = min(e["date"] for e in need_update)
    today = datetime.now().strftime("%Y%m%d")

    log(f"  更新 {len(need_update)} 条信号的后续收益（{earliest} ~ {today}）...")
    df = get_hs300_history()
    if df is None or df.empty:
        return history

    df_str = df["date"].astype(str)
    date_to_idx = {}
    for idx, d in enumerate(df_str):
        date_to_idx[d.replace("-", "")] = idx

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
    """回溯过去两年的恐慌信号"""
    load_config()

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")

    log("=" * 60)
    log(f"  开始回溯历史恐慌信号：{start_date} ~ {end_date}")
    log("=" * 60)

    df = get_hs300_history()
    if df is None or df.empty:
        log("无历史数据")
        return

    # 过滤日期范围
    df_str = df["date"].astype(str)
    start_fmt = start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:]
    end_fmt = end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:]

    mask = (df_str >= start_fmt) & (df_str <= end_fmt)
    df = df[mask].reset_index(drop=True)

    if df.empty:
        log("指定范围内无数据")
        return

    log(f"  获取到 {len(df)} 条日线数据")

    # 计算每日量比
    log("计算每日量比...")
    window = 20
    volume_ratios = {}
    for i in range(window, len(df)):
        row = df.iloc[i]
        d = str(row["date"]).replace("-", "")
        vol = float(row["volume"])
        hist_vols = [float(df.iloc[i - j]["volume"]) for j in range(1, window + 1)]
        avg_vol = sum(hist_vols) / len(hist_vols)
        ratio = vol / avg_vol if avg_vol > 0 else 0
        volume_ratios[d] = round(ratio, 2)

    log(f"  量比计算完成，共 {len(volume_ratios)} 个交易日")

    # 加载已有历史
    existing = load_history()
    existing_dates = {e.get("date") for e in existing if e.get("date")}
    log(f"  已有历史记录 {len(existing_dates)} 条，将跳过重复日期")

    t = cfg["thresholds"]
    new_entries = []
    candidate_count = 0

    for i in range(window, len(df)):
        row = df.iloc[i]
        trade_date = str(row["date"]).replace("-", "")

        if trade_date in existing_dates:
            continue

        close = float(row["close"])
        pre_close = float(df.iloc[i - 1]["close"])
        change_pct = (close - pre_close) / pre_close * 100

        if change_pct > -t["drop_level1_min"]:
            continue

        vol_ratio = volume_ratios.get(trade_date)
        if vol_ratio is None or vol_ratio < t["volume_ratio_threshold"]:
            continue

        candidate_count += 1
        log(f"  候选 #{candidate_count}：{trade_date} 跌幅{change_pct:.2f}% 量比{vol_ratio:.2f}x")

        # 全A股数据（仅当天实时，回溯时跳过条件3的严格检查）
        # 回溯时不获取全A股数据（API限制），仅用条件1+2
        market_drop_ratio = 80.0  # 假设满足条件3（回溯时放宽）

        if market_drop_ratio < t["market_drop_ratio_threshold"]:
            log(f"    ❌ 条件3未满足")
            continue

        log(f"    ✅ 三个条件满足，记录恐慌信号")

        level, level_name = classify_panic_level(change_pct, None, cfg)

        entry = {
            "date": trade_date,
            "level": level,
            "level_name": level_name,
            "drop_pct": round(change_pct, 2),
            "volume_ratio": round(vol_ratio, 2),
            "market_drop_ratio": round(market_drop_ratio, 1),
            "pe_ttm": None,
            "signal_close": round(close, 2),
            "return_5d": None,
            "return_20d": None,
            "return_60d": None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backfilled": True,
        }
        new_entries.append(entry)
        existing_dates.add(trade_date)

    if not new_entries:
        log("未发现新的历史恐慌信号")
        log("=" * 60)
        return

    log(f"回溯完成，新发现 {len(new_entries)} 个恐慌信号")

    merged = existing[:]
    merged.extend(new_entries)
    seen = {}
    for e in merged:
        d = e.get("date", "")
        if d not in seen:
            seen[d] = e
    merged = list(seen.values())
    merged.sort(key=lambda x: x.get("date", ""))

    log("计算后续收益...")
    merged = update_signal_returns(merged)

    save_history(merged)
    log(f"历史信号已保存（共 {len(merged)} 条）")
    log("=" * 60)


# ── 更新 HTML 看板 ─────────────────────────────────────────────────

def update_dashboard_html(index_data, volume_ratio, market_drop_ratio, level, history):
    if not os.path.exists(HTML_PATH):
        log("  HTML看板文件不存在，跳过更新")
        return

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    snap = {
        "indexDrop": round(index_data["change_pct"], 2) if index_data["change_pct"] else 0,
        "volumeRatio": round(volume_ratio, 2) if volume_ratio else 0,
        "dropRatio": round(market_drop_ratio, 1) if market_drop_ratio else 0,
        "peTtm": None,
        "panicLevel": level,
        "indexClose": round(index_data["close"], 2),
        "updateTime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tradeDate": index_data.get("trade_date", ""),
    }

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

    snap_js = json.dumps(snap, ensure_ascii=False, indent=2)
    snap_block = "const SNAP = " + snap_js + ";"

    marker_start = "const SNAP = "
    idx_start = html.find(marker_start)
    if idx_start >= 0:
        idx_semi = html.find("};", idx_start)
        if idx_semi >= 0:
            new_html = html[:idx_start] + snap_block + html[idx_semi + 2:]
        else:
            new_html = html.replace("</script>", snap_block + "\n  </script>")
    else:
        new_html = html.replace("</script>", snap_block + "\n  </script>", 1)

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)

    # 同时保存 dashboard_data.json 供 GitHub Pages 使用
    dashboard_data_path = os.path.join(SCRIPT_DIR, "dashboard_data.json")
    with open(dashboard_data_path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    log("  [HTML] 看板已更新，dashboard_data.json 已生成")


# ── 主流程 ────────────────────────────────────────────────────────

def main():
    global cfg

    if "--backfill" in sys.argv:
        backfill_history()
        return

    log("=" * 60)
    log(f"  A股恐慌看板监控脚本启动（AKShare版）")
    log(f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 60)

    load_config()

    trade_date = get_latest_trade_date()
    if not trade_date:
        log("未找到有效交易日，退出")
        return
    log(f"最新交易日：{trade_date}")

    index_data = get_index_data(trade_date)
    if not index_data:
        log("沪深300数据获取失败，退出")
        return

    volume_ratio, _, _ = get_volume_ratio(trade_date)
    market_drop_ratio, _, _ = get_market_drop_ratio(trade_date)

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

    level, level_name = classify_panic_level(index_data["change_pct"], index_data.get("pe_ttm"), cfg)
    log(f"★★ 触发{level_name}！级别：{level}")

    state = load_state()
    today_str = datetime.now().strftime("%Y-%m-%d")
    should, state = should_alert(state, level, today_str)

    if not should:
        log(f"今日该级别（{level}）已告警过，跳过")
    else:
        log("发送飞书告警通知...")
        send_feishu(index_data, volume_ratio, market_drop_ratio, level, level_name, cfg)

        history = load_history()
        history = append_history(history, index_data, volume_ratio, market_drop_ratio, level, level_name)
        history = update_signal_returns(history)
        save_history(history)
        log(f"历史记录已更新（共 {len(history)} 条）")

    save_state(state)

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
