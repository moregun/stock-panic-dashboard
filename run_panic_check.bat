@echo off
REM A股恐慌看板 - 交易日 16:00 自动运行
cd /d "E:\WorkBuddy\A股恐慌看板-3"
python panic_monitor.py >> panic_monitor.log 2>&1
