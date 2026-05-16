# 📉 A股恐慌看板

实时监控A股市场恐慌情绪，基于三大核心条件判断恐慌级别，并提供历史信号回测数据。

## 🌐 在线演示

**GitHub Pages 看板：** [https://moregun.github.io/stock-panic-dashboard/](https://moregun.github.io/stock-panic-dashboard/)

---

## 🎯 核心功能

### 1. 三级恐慌分级

| 级别 | 跌幅范围 | PE-TTM条件 | 说明 |
|------|-----------|-------------|------|
| 🟡 一级恐慌 | ≥ 2.5% | - | 轻度恐慌 |
| 🟠 二级恐慌 | ≥ 3.0% | ≤ 13 | 显著恐慌 |
| 🔴 三级恐慌 | ≥ 4.0% | - | 极致恐慌 |

### 2. 三大触发条件（必须同时满足）

- **条件1：沪深300跌幅** ≥ 2.5%（一级）/ ≥ 3.0%（二级）/ ≥ 4.0%（三级）
- **条件2：量能比** = 当日成交量 / 20日平均成交量 ≥ 1.2x（放量下跌）
- **条件3：全市场下跌比例** ≥ 75%（普跌）

### 3. 历史信号回测

每次恐慌信号触发后，自动跟踪后续收益：
- 5日收益率
- 20日收益率
- 60日收益率

---

## 🚀 快速开始

### 本地运行

1. **克隆仓库**
   ```bash
   git clone https://github.com/moregun/stock-panic-dashboard.git
   cd stock-panic-dashboard
   ```

2. **安装依赖**
   ```bash
   pip install tushare pandas requests
   ```

3. **配置 Token**
   
   修改 `config.json`：
   ```json
   {
     "tushare_token": "你的Tushare Token",
     "feishu_webhook": "你的飞书Webhook地址",
     ...
   }
   ```

4. **运行监控脚本**
   ```bash
   python panic_monitor.py
   ```

5. **查看看板**
   
   打开 `panic_dashboard.html`（本地版本，数据嵌入HTML）

---

## ⚙️ 配置说明

### config.json 参数

```json
{
  "tushare_token": "YOUR_TUSHARE_TOKEN",
  "index_code": "000300.SH",
  "feishu_webhook": "YOUR_FEISHU_WEBHOOK",
  "thresholds": {
    "drop_level1_min": 2.5,
    "drop_level1_max": 2.9,
    "drop_level2_min": 3.0,
    "drop_level2_max": 3.9,
    "drop_level3_min": 4.0,
    "volume_ratio_threshold": 1.2,
    "market_drop_ratio_threshold": 75.0,
    "level2_pe_ttm_max": 13.0
  }
}
```

### 调整阈值

根据你的需求修改 `config.json` 中的 `thresholds` 部分：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `drop_level1_min` | 一级恐慌最小跌幅 | 2.5% |
| `volume_ratio_threshold` | 量能比阈值 | 1.2x |
| `market_drop_ratio_threshold` | 全市场下跌比例阈值 | 75% |

---

## 🌍 部署到 GitHub Pages

### 方法一：使用 GitHub Actions 自动部署（推荐）

1. **Fork 或上传代码到 GitHub**

2. **配置 Secrets**
   
   进入仓库 Settings → Secrets → Actions，添加：
   - `TUSHARE_TOKEN`: 你的 Tushare API Token
   - `FEISHU_WEBHOOK`: 你的飞书机器人 Webhook 地址

3. **启用 GitHub Pages**
   
   Settings → Pages → Branch 选择 `main` 和 `/ (root)` → Save

4. **启用 Actions 写入权限**
   
   Settings → Actions → General → Workflow permissions → 选择 "Read and write permissions" → Save

5. **手动触发一次 Actions**
   
   Actions → Update Panic Dashboard → Run workflow

部署完成后，访问：`https://你的用户名.github.io/仓库名/`

### 方法二：手动部署

1. 运行 `python panic_monitor.py` 生成数据
2. 将以下文件上传到 GitHub：
   - `index.html`（看板页面）
   - `dashboard_data.json`（数据文件）
3. 启用 GitHub Pages

---

## 📅 自动更新

GitHub Actions 已配置为**每个交易日 15:30（北京时间）**自动运行：

- 从 Tushare 获取最新数据
- 检查恐慌条件
- 更新 `dashboard_data.json`
- 推送到仓库
- GitHub Pages 自动部署最新版本

你也可以在 Actions 页面手动点击 "Run workflow" 立即更新。

---

## 📂 文件结构

```
a-share-panic-dashboard/
├── panic_monitor.py          # 主监控脚本
├── panic_dashboard.html      # 本地版看板（数据嵌入）
├── panic_dashboard_ghpages.html  # GitHub Pages版看板
├── index.html               # 看板入口（重定向到ghpages版）
├── dashboard_data.json      # 看板数据（自动生成）
├── history.json             # 历史恐慌信号
├── state.json               # 当前状态
├── config.json              # 配置文件
├── .github/
│   └── workflows/
│       └── update-dashboard.yml  # GitHub Actions配置
└── README.md               # 本文件
```

---

## 🔧 技术栈

- **后端**：Python 3.10 + AKShare（免费，无需注册）
- **前端**：纯 HTML + CSS + JavaScript（无框架）
- **数据源**：AKShare（新浪财经接口）
- **部署**：GitHub Actions + GitHub Pages
- **通知**：飞书机器人 Webhook

---

## 📊 数据说明

### 数据更新频率

- **每个交易日 15:30** 自动更新（收盘后）
- **手动触发**：随时在 Actions 页面点击 "Run workflow"

### 数据文件

| 文件 | 说明 |
|------|------|
| `dashboard_data.json` | 当前看板数据（供GitHub Pages使用） |
| `history.json` | 历史恐慌信号记录 |
| `state.json` | 脚本运行状态 |

---

## 🔍 使用示例

### 检查指定日期的恐慌级别

```bash
python -c "
from datetime import datetime, timedelta
import pandas as pd
import tushare as ts

# 配置
ts.set_token('YOUR_TOKEN')
pro = ts.pro_api()

# 检查 2026-03-23
date = '20260323'
df = pro.index_daily(ts_code='000300.SH', trade_date=date)
print(df[['trade_date', 'close', 'pct_chg']])
"
```

### 回 fill 历史数据

```bash
python panic_monitor.py --backfill
```

---

## ⚠️ 注意事项

1. **Tushare Token 积分**
   - 免费用户每分钟限流 200次
   - 建议注册 Tushare 并获取 Token

2. **飞书 Webhook**
   - 在飞书群聊中添加机器人
   - 获取 Webhook 地址并配置到 `config.json`

3. **GitHub Actions 限额**
   - 免费账户每月 2000 分钟
   - 本工作流每次运行约 30秒，足够使用

---

## 📝 更新日志

### v1.0.0 (2026-05-16)

- ✅ 实现三级恐慌分级
- ✅ 三大触发条件判断
- ✅ 历史信号回测（5日/20日/60日收益）
- ✅ GitHub Actions 自动更新
- ✅ GitHub Pages 在线看板
- ✅ 飞书机器人告警

---

## 📄 License

MIT License - 可自由使用、修改和分发

---

## 🙏 致谢

- **Tushare**：提供A股市场数据 API
- **GitHub Actions**：自动化部署
- **GitHub Pages**：免费静态托管

---

## 📧 联系方式

如有问题或建议，欢迎提交 Issue 或 Pull Request！

**GitHub 仓库：** [https://github.com/moregun/stock-panic-dashboard](https://github.com/moregun/stock-panic-dashboard)

---

**⭐ 如果这个项目对你有帮助，请给个 Star！**
