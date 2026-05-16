#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股恐慌看板监控脚本（AKShare版）
监测沪深300恐慌级别，触发条件时飞书告警，并更新可视化看板

触发条件（必须同时满足）：
1. 沪深300当日跌幅达标
2. 当日成交量 > 近20日均量 x 1.5倍（放量下跌）
3. 全A股下跌个股占比 > 80%（普跌）

恐慌分级（需同时满足跌幅区间 + PE分位数条件）：
一级恐慌：跌幅 2.0% ~ 2.9% 且 沪深300近10年PE分位数 <= 50%
二级恐慌：跌幅 3.0% ~ 3.9% 且 沪深300近10年PE分位数 <= 20%
三级恐慌：跌幅 >= 4.0%   且 沪深300近10年PE分位数 <= 10%

数据源：AKShare - 新浪财经（免费，无需Token）
PE分位数：通过且慢(qieman.com) API获取（浏览器环境），备用东方财富/AKShare
"""

import akshare as ak
import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta

# 禁用代理（避免公司网络拦截）
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

# 路径
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH   = os.path.join(SCRIPT_DIR, "state.json")
HISTORY_PATH = os.path.join(SCRIPT_DIR, "history.json")
HTML_PATH     = os.path.join(SCRIPT_DIR, "panic_dashboard.html")
PE_CACHE_PATH = os.path.join(SCRIPT_DIR, "hs300_pe_history.json")

cfg = {}


def log(msg):
    t = datetime.now().strftime("%H:%M:%S")
    print("[{0}] {1}".format(t, msg))
    sys.stdout.flush()


# ── 配置加载 ────────────────────────────────────────────────

def load_config():
    global cfg
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ["tushare_token", "level2_pe_ttm_max"]:
        if key in cfg:
            del cfg[key]
    log("配置已加载，指数：{0}".format(cfg.get("index_code", "")))
    return cfg


# ── PE分位数获取（且慢 qieman.com）────────────────────────
# 且慢 API：https://qieman.com/pmdj/v2/idx-eval/latest
# 返回字段：pePercentile（0~1 小数），需 ×100 转为百分比
# 必须用浏览器环境（Playwright）才能拿到数据，直接HTTP请求返回空

def fetch_and_build_pe_history():
    """
    从且慢API获取沪深300 PE分位数（优先），备用东方财富/AKShare。
    返回: (pe_current, percentile_percent) 或 (None, None)
    percentile_percent: 0~100 的百分比数值
    """
    log("获取沪深300 PE分位数（且慢API）...")

    # 检查当日缓存
    if os.path.exists(PE_CACHE_PATH):
        try:
            with open(PE_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("fetch_date") == datetime.now().strftime("%Y-%m-%d"):
                pe_current = cache.get("pe_current")
                percentile = cache.get("percentile")
                if pe_current is not None and percentile is not None:
                    log("  使用今日缓存：PE={0}，分位数={1}%".format(pe_current, percentile))
                    return pe_current, percentile
        except Exception as e:
            log("  读取PE缓存失败: {0}".format(e))

    # 方法1：且慢API（Playwright）
    try:
        from playwright.sync_api import sync_playwright
        log("  尝试且慢API（Playwright）...")
    except ImportError:
        log("  Playwright未安装，跳过且慢API")
        return _fetch_pe_fallback()

    api_data = [None]

    def _handle_response(response):
        url = response.url
        if 'pmdj/v2/idx-eval/latest' in url:
            try:
                api_data[0] = response.json()
                log("  ✅ 且慢API响应已捕获")
            except Exception as e:
                log("  解析且慢API响应失败: {0}".format(e))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on('response', _handle_response)
            page.goto('https://qieman.com/idx-eval', wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(3000)
            browser.close()

        if not api_data[0]:
            log("  且慢API未返回数据，尝试备用方案...")
            return _fetch_pe_fallback()

        # 在返回数据中找沪深300（indexCode: 000300.SH）
        target = None
        for item in api_data[0].get('idxEvalList', []):
            if item.get('indexCode') == '000300.SH':
                target = item
                break

        if not target:
            log("  且慢API返回数据中未找到沪深300，尝试备用方案...")
            return _fetch_pe_fallback()

        pe = target.get('pe')
        pe_pct_decimal = target.get('pePercentile')  # 0~1 小数
        if pe is None or pe_pct_decimal is None:
            log("  且慢API返回数据缺少PE字段，尝试备用方案...")
            return _fetch_pe_fallback()

        pe_percentile = round(pe_pct_decimal * 100, 1)  # 转成 0~100 百分比
        pe = round(pe, 2)

        log("  [且慢] PE={0}，PE分位数={1}%".format(pe, pe_percentile))

        # 写入缓存
        cache = {
            "fetch_date": datetime.now().strftime("%Y-%m-%d"),
            "pe_current": pe,
            "percentile": pe_percentile,
            "source": "qieman.com",
        }
        with open(PE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        return pe, pe_percentile

    except Exception as e:
        log("  且慢API调用失败: {0}，尝试备用方案...".format(e))
        return _fetch_pe_fallback()


def _fetch_pe_fallback():
    """
    备用方案：东方财富接口 → AKShare index_pe
    返回: (pe_current, percentile_percent) 或 (None, None)
    """
    log("  备用方案：东方财富估值接口...")

    # 东方财富指数估值专用接口
    try:
        url2 = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params2 = {
            "reportName": "RPT_INDEX_DAILYVALUATION",
            "columns": "SECURITY_CODE,TRADE_DATE,PE_TTM,PB",
            "filter": "(INDEX_CODE=\"000300.SH\")",
            "pageNumber": "1",
            "pageSize": "2500",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
            "source": "WEB",
            "client": "WEB",
        }
        headers2 = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/yanus/",
        }
        resp2 = requests.get(url2, params=params2, headers=headers2, timeout=30)
        js2 = resp2.json()
        raw = js2.get("data", [])
        if raw:
            pe_list = []
            pe_current = None
            for item in raw:
                try:
                    pv = float(item.get("PE_TTM", 0))
                    if pv > 0:
                        pe_list.append(pv)
                        if pe_current is None:
                            pe_current = pv
                except (ValueError, TypeError):
                    continue
            if len(pe_list) >= 100:
                sorted_pe = sorted(pe_list)
                rank = sum(1 for x in sorted_pe if x <= pe_current)
                percentile = round(rank / len(sorted_pe) * 100, 1)
                log("  [东方财富] PE={0:.2f}，分位数={1:.1f}%".format(pe_current, percentile))
                cache = {
                    "fetch_date": datetime.now().strftime("%Y-%m-%d"),
                    "pe_current": round(pe_current, 2),
                    "percentile": percentile,
                    "source": "eastmoney",
                }
                with open(PE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
                return round(pe_current, 2), percentile
    except Exception as e:
        log("  东方财富接口失败: {0}".format(e))

    # 备用2：AKShare index_pe
    try:
        log("  备用方案2：AKShare index_pe...")
        df_pe = ak.index_pe(symbol="sh000300")
        if df_pe is not None and not df_pe.empty:
            df_pe = df_pe.sort_values("date").reset_index(drop=True)
            pe_list = [float(x) for x in df_pe["pe_ttm"].dropna().tolist() if x > 0]
            if len(pe_list) >= 100:
                pe_current = pe_list[-1]
                sorted_pe = sorted(pe_list)
                rank = sum(1 for x in sorted_pe if x <= pe_current)
                percentile = round(rank / len(sorted_pe) * 100, 1)
                log("  [AKShare] PE={0:.2f}，分位数={1:.1f}%".format(pe_current, percentile))
                cache = {
                    "fetch_date": datetime.now().strftime("%Y-%m-%d"),
                    "pe_current": round(pe_current, 2),
                    "percentile": percentile,
                    "source": "akshare",
                }
                with open(PE_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
                return round(pe_current, 2), percentile
    except Exception as e:
        log("  AKShare备用接口也失败: {0}".format(e))

    log("  所有PE获取方案均失败，将跳过PE过滤条件")
    return None, None


# ── 沪深300历史数据（新浪源）────────────────────────────────

_index_df_cache = None


def get_hs300_history():
    """获取沪深300全部历史数据（新浪源，带缓存）"""
    global _index_df_cache
    if _index_df_cache is not None:
        return _index_df_cache
    log("获取沪深300历史数据（新浪财经）...")
    df = ak.stock_zh_index_daily(symbol="sh000300")
    if df.empty:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    _index_df_cache = df
    log("  获取到 {0} 条历史数据".format(len(df)))
    return df


def get_latest_trade_date(max_lookback=10):
    """获取最近交易日"""
    df = get_hs300_history()
    if df is None or df.empty:
        return None
    today = datetime.now().date()
    for i in range(max_lookback):
        d = today - timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        if d.weekday() >= 5:
            continue
        if d_str in df["date"].astype(str).values:
            return d.strftime("%Y%m%d")
    return None


def get_index_data(trade_date):
    """获取沪深300当日数据，返回 dict"""
    log("获取沪深300数据：{0}...".format(trade_date))
    df = get_hs300_history()
    if df is None or df.empty:
        log("  沪深300数据为空")
        return None

    d_obj = datetime.strptime(trade_date, "%Y%m%d")
    target = d_obj.strftime("%Y-%m-%d")
    df_str = df["date"].astype(str)
    if target not in df_str.values:
        log("  未找到日期 {0} 的数据".format(target))
        return None

    idx = df_str[df_str == target].index[0]
    row = df.iloc[idx]
    close = float(row["close"])
    pre_close = float(df.iloc[idx - 1]["close"]) if idx > 0 else None

    change_pct = None
    if pre_close and pre_close > 0:
        change_pct = (close - pre_close) / pre_close * 100

    vol = float(row["volume"]) if "volume" in df.columns else 0
    log("  收盘：{0:.2f}，跌幅：{1:.2f}%，成交量：{2:.0f}（手）".format(close, change_pct or 0, vol))
    return {
        "close": close,
        "pre_close": pre_close,
        "change_pct": change_pct,
        "volume": vol,
        "trade_date": trade_date,
    }


def get_volume_ratio(trade_date, window=20):
    """计算量比 = 今日成交量 / 近window日均量"""
    log("计算量比（最近{0}日均量）...".format(window))
    cache_path = os.path.join(SCRIPT_DIR, "volume_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    today_str = datetime.now().strftime("%Y-%m-%d")
    if cache.get("date") == today_str and "ratio" in cache:
        log("  使用缓存量比：{0:.2f}x".format(cache["ratio"]))
        return cache["ratio"], cache.get("today_vol", 0), cache.get("avg_vol", 0)

    df = get_hs300_history()
    if df is None or len(df) < window + 1:
        log("  数据不足，无法计算量比")
        return None, 0, 0

    d_obj = datetime.strptime(trade_date, "%Y%m%d")
    target = d_obj.strftime("%Y-%m-%d")
    df_str = df["date"].astype(str)
    if target not in df_str.values:
        log("  未找到目标日期")
        return None, 0, 0

    idx = df_str[df_str == target].index[0]
    if idx < window:
        log("  历史数据不足{0}日".format(window))
        return None, 0, 0

    today_vol = float(df.iloc[idx]["volume"])
    hist_vols = [float(df.iloc[idx - j]["volume"]) for j in range(1, window + 1)]
    avg_vol = sum(hist_vols) / len(hist_vols)
    ratio = today_vol / avg_vol if avg_vol > 0 else None
    log("  今日量：{0:.0f}，{1}日均量：{2:.0f}，量比：{3:.2f}x".format(
        today_vol, window, avg_vol, ratio or 0))

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


# ── 获取全A股下跌比例（新浪源）────────────────────────────────

def get_market_drop_ratio(trade_date):
    """获取全A股数据，计算下跌个股占比，返回 (drop_ratio%, total, drop_count)"""
    log("获取全A股数据（新浪财经实时）...")
    try:
        df = ak.stock_zh_a_spot()
    except Exception as e:
        log("  获取全A股数据失败: {0}".format(e))
        return None, 0, 0

    if df.empty:
        log("  全A股数据为空")
        return None, 0, 0

    total = len(df)
    drop_count = 0
    for _, row in df.iterrows():
        try:
            close = float(row.get("最新价", row.get("close", 0)))
            pre_close = float(row.get("昨收", row.get("pre_close", 0)))
            if pre_close > 0 and close < pre_close:
                drop_count += 1
        except (ValueError, TypeError):
            continue

    ratio = (drop_count / total * 100) if total > 0 else 0
    log("  全A股 {0} 只，下跌 {1} 只，占比 {2:.1f}%".format(total, drop_count, ratio))
    return ratio, total, drop_count


# ── 恐慌判断 ─────────────────────────────────────────────────

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

    # 条件1：跌幅达标（达到一级阈值2.0%）
    if change_pct is not None and change_pct <= -t["drop_level1_min"]:
        details["cond1_met"] = True

    # 条件2：放量下跌（量比 >= 1.5x）
    if volume_ratio is not None and volume_ratio >= t["volume_ratio_threshold"]:
        details["cond2_met"] = True

    # 条件3：全市场普跌（下跌占比 >= 80%）
    if market_drop_ratio is not None and market_drop_ratio >= t["market_drop_ratio_threshold"]:
        details["cond3_met"] = True

    all_met = details["cond1_met"] and details["cond2_met"] and details["cond3_met"]
    return all_met, details


def classify_panic_level(change_pct, pe_percentile, cfg):
    """
    恐慌分级（新逻辑：需同时满足跌幅区间 + PE分位数）
    返回: (level, level_name)
    """
    t = cfg["thresholds"]
    abs_drop = abs(change_pct)

    # 从高到低判断，检查PE分位数条件
    # 三级恐慌：跌幅 >= 4.0% 且 PE分位数 <= 10%
    if abs_drop >= t["drop_level3_min"]:
        if pe_percentile is not None:
            if pe_percentile <= t["pe_percentile_level3_max"]:
                return 3, "三级恐慌"
            # PE分位数不满足，继续看是否能匹配更低级别
        else:
            # PE数据缺失，按跌幅直接给级别（保守）
            return 3, "三级恐慌（PE数据缺失）"

    # 二级恐慌：跌幅 3.0% ~ 3.9% 且 PE分位数 <= 20%
    if abs_drop >= t["drop_level2_min"] and abs_drop < t["drop_level3_min"]:
        if pe_percentile is not None:
            if pe_percentile <= t["pe_percentile_level2_max"]:
                return 2, "二级恐慌"
        else:
            return 2, "二级恐慌（PE数据缺失）"

    # 一级恐慌：跌幅 2.0% ~ 2.9% 且 PE分位数 <= 50%
    if abs_drop >= t["drop_level1_min"] and abs_drop < t["drop_level2_min"]:
        if pe_percentile is not None:
            if pe_percentile <= t["pe_percentile_level1_max"]:
                return 1, "一级恐慌"
        else:
            return 1, "一级恐慌（PE数据缺失）"

    return 0, "未触发"


# ── 状态管理（防重复告警）────────────────────────────────────

def load_state():
    if not os.path.exists(STATE_PATH):
        return {
            "last_alert_date": "",
            "alerted_levels": {"1": False, "2": False, "3": False},
            "last_check_date": ""
        }
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


# ── 飞书通知 ─────────────────────────────────────────────────

def send_feishu(index_data, volume_ratio, market_drop_ratio,
                level, level_name, pe_percentile, cfg):
    webhook = cfg.get("feishu_webhook", "")
    if not webhook:
        log("  未配置飞书 webhook，跳过通知")
        return

    change_pct = index_data["change_pct"]
    t = cfg["thresholds"]

    lines = [
        "A股恐慌告警",
        "级别：{0}（跌幅 {1:.2f}%）".format(level_name, abs(change_pct)),
        "---",
        "触发条件：",
        "沪深300跌 {0:.2f}%（>= {1}%）".format(abs(change_pct), t["drop_level1_min"]),
        "放量下跌（量比 {0:.2f}x，>= {1}x）".format(volume_ratio, t["volume_ratio_threshold"]),
        "全A股 {0:.1f}% 个股下跌（>= {1}%）".format(
            market_drop_ratio, t["market_drop_ratio_threshold"]),
        "---",
    ]

    if pe_percentile is not None:
        lines.append("沪深300近10年PE分位数：{0:.1f}%".format(pe_percentile))
        if level == 1:
            lines.append("（要求 <= {0}%）".format(t["pe_percentile_level1_max"]))
        elif level == 2:
            lines.append("（要求 <= {0}%）".format(t["pe_percentile_level2_max"]))
        elif level == 3:
            lines.append("（要求 <= {0}%）".format(t["pe_percentile_level3_max"]))
    else:
        lines.append("沪深300 PE分位数：数据获取中...")

    lines.append("---")
    lines.append(datetime.now().strftime("%Y-%m-%d %H:%M"))

    try:
        content_parts = []
        for line in lines:
            if line == "---":
                content_parts.append([{"tag": "text", "text": "-" * 30}])
            else:
                content_parts.append([{"tag": "text", "text": line}])

        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": "A股{0}".format(level_name),
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
            log("  [飞书] 通知发送失败: {0}".format(result))
    except Exception as e:
        log("  [飞书] 通知异常: {0}".format(e))


# ── 历史记录 ──────────────────────────────────────────────────

def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_history(history, index_data, volume_ratio, market_drop_ratio,
                  level, level_name, pe_percentile):
    entry = {
        "date": index_data["trade_date"],
        "level": level,
        "level_name": level_name,
        "drop_pct": round(index_data["change_pct"], 2),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        "market_drop_ratio": round(market_drop_ratio, 1) if market_drop_ratio else None,
        "pe_percentile": round(pe_percentile, 1) if pe_percentile is not None else None,
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
        if (entry.get("return_5d") is None or
                entry.get("return_20d") is None or
                entry.get("return_60d") is None):
            need_update.append(entry)

    if not need_update:
        return history

    earliest = min(e["date"] for e in need_update)
    today = datetime.now().strftime("%Y%m%d")
    log("  更新 {0} 条信号的后续收益（{1} ~ {2}）...".format(
        len(need_update), earliest, today))

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

    log("  已更新 {0} 条信号的收益数据".format(updated_count))
    return history


def backfill_history():
    """回溯过去两年的恐慌信号"""
    load_config()

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=730)).strftime("%Y%m%d")

    log("=" * 60)
    log("  开始回溯历史恐慌信号：{0} ~ {1}".format(start_date, end_date))
    log("=" * 60)

    df = get_hs300_history()
    if df is None or df.empty:
        log("无历史数据")
        return

    df_str = df["date"].astype(str)
    start_fmt = start_date[:4] + "-" + start_date[4:6] + "-" + start_date[6:]
    end_fmt = end_date[:4] + "-" + end_date[4:6] + "-" + end_date[6:]
    mask = (df_str >= start_fmt) & (df_str <= end_fmt)
    df = df[mask].reset_index(drop=True)

    if df.empty:
        log("指定范围内无数据")
        return

    log("  获取到 {0} 条日线数据".format(len(df)))

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

    log("  量比计算完成，共 {0} 个交易日".format(len(volume_ratios)))

    existing = load_history()
    existing_dates = {e.get("date") for e in existing if e.get("date")}
    log("  已有历史记录 {0} 条，将跳过重复日期".format(len(existing_dates)))

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
        log("  候选 #{0}：{1} 跌幅{2:.2f}% 量比{3:.2f}x".format(
            candidate_count, trade_date, change_pct, vol_ratio))

        # 回溯时不获取全A股数据，假设条件3满足
        market_drop_ratio = 80.0
        if market_drop_ratio < t["market_drop_ratio_threshold"]:
            log("    [X] 条件3未满足")
            continue

        log("    [OK] 三个条件满足，记录恐慌信号")

        # 回溯时PE分位数不可用，按跌幅直接分级
        abs_drop = abs(change_pct)
        if abs_drop >= t["drop_level3_min"]:
            level, level_name = 3, "三级恐慌（回溯）"
        elif abs_drop >= t["drop_level2_min"]:
            level, level_name = 2, "二级恐慌（回溯）"
        elif abs_drop >= t["drop_level1_min"]:
            level, level_name = 1, "一级恐慌（回溯）"
        else:
            continue

        entry = {
            "date": trade_date,
            "level": level,
            "level_name": level_name,
            "drop_pct": round(change_pct, 2),
            "volume_ratio": round(vol_ratio, 2),
            "market_drop_ratio": round(market_drop_ratio, 1),
            "pe_percentile": None,
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

    log("回溯完成，新发现 {0} 个恐慌信号".format(len(new_entries)))

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
    log("历史信号已保存（共 {0} 条）".format(len(merged)))
    log("=" * 60)


# ── 更新 HTML 看板 ─────────────────────────────────────────────

def update_dashboard_html(index_data, volume_ratio, market_drop_ratio,
                         level, pe_percentile, history):
    if not os.path.exists(HTML_PATH):
        log("  HTML看板文件不存在，跳过更新")
        return

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    snap = {
        "indexDrop": round(index_data["change_pct"], 2) if index_data["change_pct"] else 0,
        "volumeRatio": round(volume_ratio, 2) if volume_ratio else 0,
        "dropRatio": round(market_drop_ratio, 1) if market_drop_ratio else 0,
        "pePercentile": round(pe_percentile, 1) if pe_percentile is not None else None,
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
            "pe_percentile": h.get("pe_percentile"),
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


# ── 主流程 ───────────────────────────────────────────────────

def main():
    global cfg

    if "--backfill" in sys.argv:
        backfill_history()
        return

    log("=" * 60)
    log("  A股恐慌看板监控脚本启动（AKShare版）")
    log("  时间：{0}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("=" * 60)

    load_config()

    trade_date = get_latest_trade_date()
    if not trade_date:
        log("未找到有效交易日，退出")
        return
    log("最新交易日：{0}".format(trade_date))

    index_data = get_index_data(trade_date)
    if not index_data:
        log("沪深300数据获取失败，退出")
        return

    volume_ratio, _, _ = get_volume_ratio(trade_date)
    market_drop_ratio, _, _ = get_market_drop_ratio(trade_date)

    # 获取PE分位数
    _, pe_percentile = fetch_and_build_pe_history()
    if pe_percentile is None:
        log("  PE分位数获取失败，将跳过PE过滤条件")

    all_met, details = check_panic_conditions(index_data, volume_ratio, market_drop_ratio, cfg)

    log("-" * 60)
    c1 = details["cond1_value"]
    c2 = details["cond2_value"]
    c3 = details["cond3_value"]
    log("条件1（跌幅）：{0:.2f}% -> {1}".format(c1, "OK" if details["cond1_met"] else "FAIL"))
    if c2 is not None:
        log("条件2（量比）：{0:.2f}x -> {1}".format(c2, "OK" if details["cond2_met"] else "FAIL"))
    else:
        log("条件2（量比）：数据不足 -> FAIL")
    if c3 is not None:
        log("条件3（普跌）：{0:.1f}% -> {1}".format(c3, "OK" if details["cond3_met"] else "FAIL"))
    else:
        log("条件3（普跌）：数据不足 -> FAIL")
    if pe_percentile is not None:
        log("PE分位数：{0:.1f}%".format(pe_percentile))
    log("-" * 60)

    if not all_met:
        log("三个条件未同时满足，不触发告警")
        history = load_history()
        history = update_signal_returns(history)
        save_history(history)
        update_dashboard_html(index_data, volume_ratio, market_drop_ratio, 0, pe_percentile, history)
        log("完成（未触发）")
        return

    # 三个条件满足，进行分级（需同时满足PE分位数条件）
    level, level_name = classify_panic_level(index_data["change_pct"], pe_percentile, cfg)
    if level == 0:
        log("三个条件满足，但 {0}，不触发告警".format(level_name))
        history = load_history()
        history = update_signal_returns(history)
        save_history(history)
        update_dashboard_html(index_data, volume_ratio, market_drop_ratio, 0, pe_percentile, history)
        return

    log("★★ 触发{0}！级别：{1}".format(level_name, level))

    state = load_state()
    today_str = datetime.now().strftime("%Y-%m-%d")
    should, state = should_alert(state, level, today_str)

    if not should:
        log("今日该级别（{0}）已告警过，跳过".format(level))
    else:
        log("发送飞书告警通知...")
        send_feishu(index_data, volume_ratio, market_drop_ratio,
                     level, level_name, pe_percentile, cfg)
        history = load_history()
        history = append_history(history, index_data, volume_ratio,
                                market_drop_ratio, level, level_name, pe_percentile)
        history = update_signal_returns(history)
        save_history(history)
        log("历史记录已更新（共 {0} 条）".format(len(history)))

    save_state(state)

    history = load_history()
    history = update_signal_returns(history)
    save_history(history)
    log("更新可视化看板...")
    update_dashboard_html(index_data, volume_ratio, market_drop_ratio,
                         level, pe_percentile, history)

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
