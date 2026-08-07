@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_CMD=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_CMD=.venv\Scripts\python.exe"
echo 启动邮件监测服务...
echo 监听配置从 .env 读取；30分钟轮询应设置 MAIL_POLL_INTERVAL_SECONDS=1800。
"%PYTHON_CMD%" tools\run_mail_watcher.py
if errorlevel 1 (
  echo 邮件监测启动失败，请检查 .env 中的 MAIL_WATCHER_ENABLED、IMAP账号和服务器配置。
)
pause
endlocal
