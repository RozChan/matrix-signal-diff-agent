@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
start "matrix-signal-review-server" cmd /k start_server.bat
start "matrix-signal-feishu-bot" cmd /k start_bot.bat
if exist ".env" (
  findstr /I /R /C:"^MAIL_WATCHER_ENABLED[ ]*=[ ]*true" ".env" >nul
  if not errorlevel 1 start "matrix-signal-mail-watcher" cmd /k start_mail_watcher.bat
)
endlocal
