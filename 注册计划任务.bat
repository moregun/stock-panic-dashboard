echo off
cls
echo ===============================================
echo  A股恐慌看板 - 计划任务注册
echo ===============================================
echo.

REM 检查管理员权限
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo [错误] 需要管理员权限！
    echo.
    echo 请右键本文件，选择"以管理员身份运行"。
    echo.
    pause
    exit /b 1
)

echo [1/2] 注册计划任务：每日 16:00 运行恐慌监控...
schtasks /create /tn "A股恐慌看板-每日16点" ^
  /tr "E:\WorkBuddy\A股恐慌看板-3\run_panic_check.bat" ^
  /sc daily /st 16:00 /f

if %errorLevel% EQU 0 (
    echo.
    echo [成功] 计划任务已注册！
    echo.
    echo 任务名称：A股恐慌看板-每日16点
    echo 运行时间：每日 16:00
    echo 运行程序：run_panic_check.bat
    echo.
    echo 非交易日脚本会自动跳过，不会报错。
) else (
    echo.
    echo [失败] 注册失败，请手动在"任务计划程序"中创建。
)

echo.
pause
