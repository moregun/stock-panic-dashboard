# A股恐慌看板 — 实现方案（已确认）

## 项目路径
`E:\WorkBuddy\A股恐慌看板-3\`

---

## 文件结构

```
A股恐慌看板-3/
├── panic_monitor.py          # 核心监控脚本
├── panic_dashboard.html      # 可视化看板页面
├── config.json              # 配置文件
├── state.json               # 运行状态（防止重复告警）
└── history.json             # 恐慌事件历史记录
```

---

## 1. config.json（已确认配置）

```json
{
  "tushare_token": "a03b4ce4058701f296b53ca458df8d6c662d1492a39d73fc4ec767d3",
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/5549e99d-0354-4318-b11f-2d9ddaace77f",
  "index_code": "000300.SH",
  "thresholds": {
    "drop_level1_min": 2.5,
    "drop_level1_max": 2.9,
    "drop_level2_min": 3.0,
    "drop_level2_max": 3.9,
    "drop_level3_min": 4.0,
    "volume_ratio_threshold": 1.5,
    "market_drop_ratio_threshold": 80.0,
    "level2_pe_ttm_max": 13.0
  }
}
```

---

## 2. panic_monitor.py — 监控脚本

### 数据源（Tushare Pro）

| 数据 | Tushare API | 说明 |
|------|------------|------|
| 沪深300 日线 | `pro.index_daily(ts_code='000300.SH')` | 收盘价、涨跌幅、成交量 |
| 全A股日线 | `pro.daily(trade_date=...)` | 全市场个股涨跌，需分页 |
| 沪深300 PE-TTM | `pro.index_dailybasic(ts_code='000300.SH')` | 估值数据 |

### 核心逻辑

```
对于每个交易日：
  1. 获取沪深300今日数据 → 跌幅 = (昨收 - 今收) / 昨收
  2. 获取近20日成交量 → 均量 → 量比 = 今日量 / 均量
  3. 获取全A股今日数据 → 下跌个股数 / 总个股数
  4. 三个条件全部满足 → 进入分级判断
  5. 分级：
     - 跌幅 ≥ 4.0% → 三级恐慌
     - 跌幅 3.0%~3.9% 且 PE-TTM ≤ 13 → 二级恐慌
     - 跌幅 2.5%~2.9% → 一级恐慌
  6. 检查 state.json 今日是否已对该级别告警 → 未告警则发飞书 + 更新state
```

### 防止重复告警

`state.json` 格式：
```json
{
  "last_alert_date": "2026-05-15",
  "alerted_levels": { "1": false, "2": false, "3": false },
  "last_check_date": "2026-05-15"
}
```
每日首次运行自动重置 `alerted_levels`；同一级别当日只告警一次。

### 飞书通知格式

```
🚨 A股恐慌告警
级别：二级恐慌（跌幅 3.2%）
______________________________
触发条件：
✅ 沪深300跌 3.2%（≥2.5%）
✅ 放量下跌（量比 1.8x，≥1.5x）
✅ 全A股 84% 个股下跌（≥80%）
______________________________
沪深300 PE-TTM：12.8
______________________________
2026-05-15 16:00
```

---

## 3. panic_dashboard.html — 可视化看板

### 设计风格
- 跟随 `dividend_etf_dashboard.html` 暗色主题
- 单文件、零依赖、嵌入式数据
- Chart.js 4.4.0 + chartjs-plugin-annotation

### 页面区块

1. **顶部 KPI 卡片（3列）**
   - 当前恐慌级别（未触发/一级/二级/三级，带颜色标识）
   - 沪深300 今日涨跌幅
   - 全A股下跌个股占比

2. **三大条件状态面板（3列）**
   - 条件1：跌幅进度条 + 三色阈值区间标注
   - 条件2：量比仪表 + 1.5x 阈值线
   - 条件3：下跌占比进度条 + 80% 阈值线

3. **历史图表**
   - 主图：沪深300 日跌幅折线（最近60日）
   - 色带标注恐慌区间（黄/橙/红对应一至三级）
   - 量比副图

4. **告警历史表格**
   - 日期 / 级别 / 跌幅 / 量比 / 下跌占比 / PE-TTM

### 数据嵌入方式

Python 脚本通过正则替换更新 HTML 中的 `SNAP` 对象：
```javascript
const SNAP = {
  indexDrop: -2.8,
  volumeRatio: 1.72,
  dropRatio: 84.2,
  peTtm: 12.8,
  panicLevel: 1,
  updateTime: '2026-05-15 16:00',
  history: [{date:'2026-05-15',drop:-2.8,volumeRatio:1.72,dropRatio:84.2,level:1},...],
  alertHistory: [{date:'2026-05-15',level:1,drop:-2.8},...],
};
```

---

## 4. 实现步骤（按顺序执行）

### Step 1: 创建 `config.json`
- 写入确认的 token 和 webhook

### Step 2: 创建 `state.json` 和 `history.json` 初始文件
- `state.json`: 初始状态，无告警记录
- `history.json`: 空数组，等待写入恐慌事件

### Step 3: 实现 `panic_monitor.py`
- `safe_request()`: 带重试和限流的 Tushare API 调用
- `load_config()`: 读取 config.json
- `get_index_data()`: 获取沪深300日线 + PE-TTM
- `get_volume_ratio()`: 计算量比（今日量/20日均量）
- `get_market_drop_ratio()`: 分页获取全A股，计算下跌占比
- `check_panic_conditions()`: 判断三个条件是否同时满足
- `classify_panic_level()`: 恐慌分级
- `should_alert()`: 读取 state.json，判断是否已告警
- `send_feishu()`: 发送飞书通知
- `update_dashboard_html()`: 正则替换更新 HTML 中的 SNAP 数据
- `main()`: 主流程

### Step 4: 实现 `panic_dashboard.html`
- 暗色主题（跟随 dividend_etf_dashboard 风格）
- Chart.js 4.4.0 CDN
- 响应式布局
- 嵌入 SNAP 数据对象

### Step 5: 测试运行
- 手动执行 `panic_monitor.py` 验证数据获取和告警逻辑
- 检查飞书通知是否发送成功
- 检查 HTML 是否正确更新

### Step 6: 设置自动化
- 创建自动化任务：每个交易日 16:00 运行

---

## 5. 关键细节

- **交易日判断**：若 Tushare 返回空数据（非交易日），脚本自动退出
- **日期回退**：若今日数据未更新，自动取最近交易日（参考 lt_screen.py 模式）
- **Tushare 限流**：`safe_request` 模式（重试 + 0.3s 延迟 + 每150次休息2秒）
- **全A股分页**：`pro.daily(trade_date=..., offset=..., limit=5000)` 分页获取约5000+条
- **state.json 重置**：每次运行先检查 `last_alert_date`，若不是今日则重置 `alerted_levels`
