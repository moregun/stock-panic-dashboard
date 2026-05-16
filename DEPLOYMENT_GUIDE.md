# A股恐慌看板 - 部署指南

## 项目简介

A股恐慌看板是一个实时监控A股市场恐慌情绪的系统，基于三大条件判断恐慌级别：
1. 沪深300跌幅
2. 量能比（当日成交量/20日均值）
3. 全市场下跌比例

## 部署到 GitHub Pages

### 第一步：创建 GitHub 仓库

1. 登录 GitHub，点击右上角 "+" → "New repository"
2. 仓库名：`a-share-panic-dashboard`
3. 选择 "Public"（GitHub Pages 需要公开仓库）
4. 点击 "Create repository"

### 第二步：上传代码到 GitHub

在本地项目目录执行：

```bash
cd E:\WorkBuddy\A股恐慌看板-3
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/你的用户名/a-share-panic-dashboard.git
git push -u origin main
```

### 第三步：配置 GitHub Secrets

你的 Tushare Token 和飞书 Webhook 需要保密，不能写在代码里。

1. 进入你的 GitHub 仓库
2. 点击 "Settings" → "Secrets and variables" → "Actions"
3. 点击 "New repository secret"
4. 添加以下两个 Secret：

| Name | Value |
|------|-------|
| `TUSHARE_TOKEN` | 你的 Tushare API Token |
| `FEISHU_WEBHOOK` | 你的飞书机器人 Webhook 地址 |

### 第四步：启用 GitHub Pages

1. 进入仓库 "Settings" → "Pages"
2. 在 "Build and deployment" → "Branch" 中选择 `gh-pages` 分支
3. 点击 "Save"

等待几分钟，你的看板就会发布到：
`https://你的用户名.github.io/a-share-panic-dashboard/panic_dashboard_ghpages.html`

### 第五步：测试 GitHub Actions

1. 进入仓库 "Actions" 标签页
2. 选择 "Update Panic Dashboard" workflow
3. 点击 "Run workflow" → "Run workflow"（手动触发一次）
4. 观察运行日志，确保没有错误

## 工作流程说明

```
┌─────────────────────────────────────────────────────────┐
│  GitHub Actions (每天 15:30 自动运行)                  │
├─────────────────────────────────────────────────────────┤
│  1. 拉取代码                                          │
│  2. 安装 Python 依赖 (tushare, pandas, requests)      │
│  3. 运行 panic_monitor.py                             │
│     - 从 Tushare 获取最新数据                          │
│     - 检查恐慌条件                                      │
│     - 更新 state.json, history.json                    │
│     - 发送飞书通知（如果触发恐慌）                       │
│  4. 提交并推送更新的文件到仓库                          │
│  5. 部署到 GitHub Pages (gh-pages 分支)               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  用户访问 GitHub Pages                                  │
├─────────────────────────────────────────────────────────┤
│  1. 浏览器打开 panic_dashboard_ghpages.html           │
│  2. JavaScript 自动加载 state.json 和 history.json     │
│  3. 渲染看板数据                                      │
│  4. 每5分钟自动刷新数据                                │
└─────────────────────────────────────────────────────────┘
```

## 部署到 Cloudflare Pages

如果你想使用 Cloudflare Pages（全球CDN加速），步骤如下：

### 方法一：纯静态托管（推荐）

1. 注册 Cloudflare 账号
2. 进入 Cloudflare Dashboard → "Pages"
3. 点击 "Create a project" → "Connect to Git"
4. 选择你的 GitHub 仓库
5. 构建设置：
   - Build command: （留空，因为是纯静态网站）
   - Build output directory: `/`
6. 点击 "Save and Deploy"

**注意**：Cloudflare Pages 只托管静态文件，你仍需要 GitHub Actions 来运行 Python 脚本更新数据。

### 方法二：使用 Cloudflare Workers（高级）

如果你想在 Cloudflare 上运行后端逻辑，需要：
1. 将 Python 代码重写为 JavaScript/TypeScript
2. 使用 Cloudflare Workers 定时运行
3. 将结果存储到 Cloudflare KV

这个方法比较复杂，不建议除非你有特殊需求。

## 本地测试

在部署之前，你可以在本地测试：

```bash
# 修改 panic_dashboard_ghpages.html 中的数据加载路径
# 将 fetch('state.json') 改为 fetch('http://localhost:8000/state.json')

# 启动本地服务器
cd E:\WorkBuddy\A股恐慌看板-3
python -m http.server 8000

# 浏览器打开
# http://localhost:8000/panic_dashboard_ghpages.html
```

## 常见问题

### Q: GitHub Actions 运行失败怎么办？

A: 检查：
1. Secrets 是否正确配置（TUSHARE_TOKEN, FEISHU_WEBHOOK）
2. Tushare Token 是否有效（是否有积分）
3. 查看 Actions 日志中的详细错误信息

### Q: 看板数据不更新？

A: 
1. 检查 GitHub Actions 是否正常运行
2. 检查 `state.json` 和 `history.json` 是否被正确更新
3. 浏览器可能有缓存，尝试强制刷新（Ctrl+F5）

### Q: 可以部署到自己的服务器吗？

A: 可以！你只需要：
1. 将代码上传到服务器
2. 设置 cron job 定时运行 `panic_monitor.py`
3. 使用 Nginx/Apache 托管 HTML 文件

示例 cron 配置（每天 15:30 运行）：
```bash
30 15 * * 1-5 cd /path/to/project && python panic_monitor.py
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `panic_monitor.py` | 主监控脚本，获取数据并更新 JSON |
| `panic_dashboard_ghpages.html` | GitHub Pages 版本看板（从外部加载数据） |
| `panic_dashboard.html` | 本地版本看板（数据嵌入 HTML） |
| `state.json` | 当前状态数据（自动更新） |
| `history.json` | 历史恐慌信号（自动更新） |
| `.github/workflows/update-dashboard.yml` | GitHub Actions 配置 |

## 下一步

部署完成后，你可以：
1. 分享看板链接给朋友
2. 在手机浏览器中访问（响应式设计）
3. 根据需要调整恐慌阈值（修改 `config.json`）
4. 添加更多监控指标

---

**祝部署顺利！** 如果有任何问题，可以查看 GitHub Actions 的运行日志来排查错误。
