@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
start "matrix-signal-review-server" cmd /k start_server.bat
start "matrix-signal-feishu-bot" cmd /k start_bot.bat
endlocal
