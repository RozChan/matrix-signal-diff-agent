@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
python tools\run_streamlit.py
pause
endlocal
