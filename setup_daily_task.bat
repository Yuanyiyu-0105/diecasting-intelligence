@echo off
REM ============================================================
REM  为「压铸情报系统」创建每日自动采集任务
REM
REM  需求书验收硬指标：原始数据（连续 7 天）
REM  本脚本建两个任务：
REM    1. DieCasting-Intel      每天 08:00 定时跑
REM    2. DieCasting-Intel-Boot 每次开机登录后补跑（防止 08:00 关机错过）
REM
REM  用法：双击运行
REM ============================================================

set "PY=D:\diecasting-intelligence\.venv\Scripts\python.exe"
set "SCRIPT=D:\diecasting-intelligence\run_pipeline.py"
set "CWD=D:\diecasting-intelligence"

echo ============================================================
echo  正在创建每日采集计划任务...
echo.

if not exist "%PY%" (
    echo [错误] 找不到 %PY%
    echo        请确认虚拟环境路径正确
    pause
    exit /b 1
)

echo [1/2] 创建每日 08:00 任务...
schtasks /create /tn "DieCasting-Intel" ^
  /tr "\"%PY%\" \"%SCRIPT%\"" ^
  /sc daily /st 08:00 ^
  /ru "%USERNAME%" /f
if errorlevel 1 (
    echo     [失败] 每日任务创建失败
) else (
    echo     [完成]
)

echo.
echo [2/2] 创建开机补跑任务（防止关机错过 08:00）...
schtasks /create /tn "DieCasting-Intel-Boot" ^
  /tr "\"%PY%\" \"%SCRIPT%\"" ^
  /sc onlogon ^
  /ru "%USERNAME%" /f
if errorlevel 1 (
    echo     [失败] 补跑任务创建失败
) else (
    echo     [完成]
)

echo.
echo ============================================================
echo  完成。验证方式：
echo.
echo     schtasks /query /tn "DieCasting-Intel"
echo.
echo  手动立即跑一次（补今天的归档）：
echo     cd /d %CWD%
echo     .venv\Scripts\python.exe run_pipeline.py
echo.
echo  注意：电脑在 08:00 必须开着，否则靠开机补跑任务兜底。
echo ============================================================
pause
