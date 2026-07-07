@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo 未检测到 Streamlit。
    echo 请先双击运行 install_dependencies.bat 安装依赖。
    pause
    exit /b 1
)

python -m streamlit run app.py

pause
