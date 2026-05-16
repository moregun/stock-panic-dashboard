# 📉 A股恐慌看板

实时监控A股市场恐慌情绪，基于三大核心条件判断恐慌级别，并给出对应资金配置建议。

## 🌐 在线看板

**GitHub Pages：** [https://moregun.github.io/stock-panic-dashboard/](https://moregun.github.io/stock-panic-dashboard/)

---

## 🎯 恐慌分级与操作建议

| 级别 | 跌幅区间 | PE分位条件 | 资金占用 | 配置方案 |
|------|-----------|-------------|----------|------------|
| 🟡 一级恐慌 | 2.0%~2.9% | ≤50% | 10%~15% | 100% 沪深300ETF |
| 🟠 二级恐慌 | 3.0%~3.9% | ≤20% | 20%~25% | 70% 沪深300 + 30% 中证500 |
| 🔴 三级恐慌 | ≥4.0% | ≤10% | 30%~35% | 60% 沪深300 + 40% 中证500 |

---

## 🎯 核心功能

### 1. 三大触发条件（必须同时满足）

- **条件1：沪深300跌幅** ≥ 2.0%（一级）/ ≥ 3.0%（二级）/ ≥ 4.0%（三级）
- **条件2：放量下跌** = 当日成交量 / 20日平均成交量 ≥ 1.5x
- **条件3：全市场普跌** 下跌个股占比 ≥ 80%

### 2. PE分位数过滤

使用 AKShare 自行计算沪深300近10年PE-TTM分位数（0~1范围），仅在估值合理时触发告警，避免高位假信号。

### 3. 历史信号回测

每次恐慌信号触发后，自动跟踪后续收益（5日/20日/60日收益率）。

---

## 🚀 本地运行

### 1. 克隆仓库

```bash
git clone https://github.com/moregun/stock-panic-dashboard.git
cd stock-panic-dashboard
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置飞书通知

复制配置模板并填写你的飞书 Webhook：

```bash
cp config.json.example config.json
```

编辑 `config.json`：
```json
{
  "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/你的token",
  "index_code": "000300.SH",
  "thresholds": { ... }
}
```

### 4. 运行监控脚本

```bash
python panic_monitor.py
```

### 5. 查看看板

打开 `panic_dashboard.html` 或 `index.html`。

---

## ☁️ 部署到 GitHub Actions（自动化）

### 第1步：Fork 或上传代码到 GitHub

确保以下文件已提交：
```
panic_monitor.py
requirements.txt
.github/workflows/update-dashboard.yml
.gitignore
```

### 第2步：配置 GitHub Secret

进入仓库 **Settings → Secrets and variables → Actions**，添加：

| Name | Value |
|------|-------|
| `FEISHU_WEBHOOK` | `https://open.feishu.cn/open-apis/bot/v2/hook/你的token` |

### 第3步：启用 Actions 写入权限

**Settings → Actions → General → Workflow permissions** → 选择 **"Read and write permissions"** → Save

### 第4步：手动触发测试

**Actions → Update Panic Dashboard → Run workflow** → 选择 `main` 分支 → **Run workflow**

### 自动化说明

- 每个交易日 **15:30（北京时间）** 自动运行
- 触发告警时自动推送飞书通知（含操作建议）
- 自动更新 `dashboard_data.json` 并推送到仓库
- GitHub Pages 自动部署最新看板

---

## 📊 数据源

| 数据 | 来源 | 说明 |
|------|------|------|
| 沪深300行情 | AKShare（新浪财经） | 免费，无需Token |
| 全A股实时行情 | AKShare（新浪财经） | 免费，无需Token |
| PE-TTM历史数据 | AKShare（乐咕乐股） | 近10年数据，自行计算分位数 |

---

## ⚙️ 配置文件说明

### `config.json` 参数

```json
{
  "feishu_webhook": "飞书机器人Webhook地址",
  "index_code": "000300.SH",
  "thresholds": {
    "drop_level1_min": 2.0,      // 一级恐慌最小跌幅
    "drop_level2_min": 3.0,      // 二级恐慌最小跌幅
    "drop_level3_min": 4.0,      // 三级恐慌最小跌幅
    "volume_ratio_threshold": 1.5,  // 量比阈值
    "market_drop_ratio_threshold": 80.0,  // 全市场下跌比例阈值
    "pe_percentile_level1_max": 0.5,    // 一级PE分位数上限（0~1）
    "pe_percentile_level2_max": 0.2,    // 二级PE分位数上限
    "pe_percentile_level3_max": 0.1     // 三级PE分位数上限
  },
  "action_plan": {
    "level1": {"fund_usage": "10%~15%", "allocation": "100% 沪深300ETF"},
    "level2": {"fund_usage": "20%~25%", "allocation": "70% 沪深300 + 30% 中证500"},
    "level3": {"fund_usage": "30%~35%", "allocation": "60% 沪深300 + 40% 中证500"}
  }
}
```

---

## 📁 文件结构

```
stock-panic-dashboard/
├── panic_monitor.py                # 主监控脚本
├── panic_dashboard.html           # 本地版看板（数据嵌入）
├── panic_dashboard_ghpages.html  # GitHub Pages版看板
├── index.html                    # 看板入口
├── dashboard_data.json           # 看板数据（自动生成）
├── history.json                  # 历史恐慌信号
├── state.json                    # 脚本运行状态（防重复告警）
├── config.json.example           # 配置模板（不含敏感信息）
├── requirements.txt              # Python依赖
├── .gitignore                   # 保护config.json等敏感文件
├── .github/
│   └── workflows/
│       └── update-dashboard.yml  # GitHub Actions配置
└── README.md
```

---

## 🔧 技术栈

- **后端**：Python 3.10 + AKShare（免费，无需注册）
- **前端**：纯 HTML + CSS + JavaScript（无框架）
- **数据源**：AKShare（新浪财经 / 乐咕乐股）
- **部署**：GitHub Actions + GitHub Pages
- **通知**：飞书机器人 Webhook

---

## 📂 回测历史数据

```bash
python panic_monitor.py --backfill
```

回溯过去两年的恐慌信号（由于历史全A股数据限制，条件3按满足处理）。

---

## ⚠️ 注意事项

1. **飞书 Webhook 保护**
   - `config.json` 已加入 `.gitignore`，不会提交到GitHub
   - 部署时通过 GitHub Secrets 传入，无需明文存储

2. **GitHub Actions 限额**
   - 免费账户每月 2000 分钟
   - 本工作流每次运行约 1~2 分钟，足够使用

3. **市场数据延迟**
   - AKShare 数据可能存在 15 分钟延迟
   - 建议在收盘后（15:30）运行

---

## 📝 更新日志

### v1.1.0 (2026-05-16)

- ✅ PE分位数改用 AKShare 自行计算（移除 Tushare/且慢/东方财富依赖）
- ✅ PE分位数范围统一为 0~1
- ✅ 新增恐慌级别操作建议（资金配置方案）
- ✅ 飞书告警附加操作建议
- ✅ 完善 GitHub Actions 部署配置
- ✅ 保护敏感配置（`config.json` 加入 `.gitignore`）

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

**⭐ 如果这个项目对你有帮助，请给个 Star！**

GitHub 仓库： [https://github.com/moregun/stock-panic-dashboard](https://github.com/moregun/stock-panic-dashboard)
