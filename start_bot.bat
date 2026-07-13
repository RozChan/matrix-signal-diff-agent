@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
echo 启动飞书机器人服务...
echo 如未配置 FEISHU_BOT_ENABLED=true 或 LARK_CLI_PATH，程序会给出中文提示并退出。
python bot_service.py
if errorlevel 1 (
  echo 飞书机器人启动失败，请检查 .env 或环境变量中的 FEISHU_BOT_ENABLED 和 LARK_CLI_PATH。
)
pause
endlocal
