@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
echo 启动 Streamlit 内网审核服务：http://0.0.0.0:8501
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
pause
endlocal
