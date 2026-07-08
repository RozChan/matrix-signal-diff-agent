@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "LOG_FILE=%~dp0start_demo.log"

echo ========================================
echo 启动 matrix-signal-diff-agent Demo
echo ========================================
echo 当前目录：%cd%
echo 日志文件：%LOG_FILE%
echo.

echo [%date% %time%] start_demo.bat started > "%LOG_FILE%"
echo Current dir: %cd% >> "%LOG_FILE%"

where python >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo 未找到 python，请确认 Python 已安装并加入 PATH。
    echo 未找到 python，请确认 Python 已安装并加入 PATH。>> "%LOG_FILE%"
    goto fail
)

python --version
python --version >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo Python 无法正常运行，请检查安装。
    echo Python 无法正常运行，请检查安装。>> "%LOG_FILE%"
    goto fail
)

python -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo 未检测到 Streamlit。
    echo 请先双击运行 install_dependencies.bat 安装依赖。
    echo 未检测到 Streamlit。>> "%LOG_FILE%"
    goto fail
)

echo 正在启动 Streamlit，请稍候...
echo python -m streamlit run app.py >> "%LOG_FILE%"
python -m streamlit run app.py
set "EXIT_CODE=%ERRORLEVEL%"
echo Streamlit exited with code %EXIT_CODE% >> "%LOG_FILE%"
if not "%EXIT_CODE%"=="0" (
    echo Streamlit 启动失败，退出码：%EXIT_CODE%
    goto fail
)

goto done

:fail
echo.
echo 如果窗口仍然闪退，请打开 cmd，cd 到项目目录后手动运行：
echo start_demo.bat
echo 或查看日志文件：%LOG_FILE%

goto done

:done
echo.
pause
endlocal
