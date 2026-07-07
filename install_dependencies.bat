@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "LOG_FILE=%~dp0install_dependencies.log"

echo ========================================
echo 安装 matrix-signal-diff-agent 依赖
echo ========================================
echo 当前目录：%cd%
echo 日志文件：%LOG_FILE%
echo.

echo [%date% %time%] install_dependencies.bat started > "%LOG_FILE%"
echo Current dir: %cd% >> "%LOG_FILE%"

echo 检查 Python 环境...
where python >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo 未找到 python，请确认 Python 已安装并加入 PATH。
    echo 未找到 python，请确认 Python 已安装并加入 PATH。>> "%LOG_FILE%"
    goto fail
)

python --version
echo python --version checked >> "%LOG_FILE%"
if errorlevel 1 (
    echo Python 无法正常运行，请检查安装。
    echo Python 无法正常运行，请检查安装。>> "%LOG_FILE%"
    goto fail
)

echo.
echo 检查 pip...
python -m pip --version
echo python -m pip --version checked >> "%LOG_FILE%"
if errorlevel 1 (
    echo pip 不可用，尝试安装 pip...
    echo pip 不可用，尝试安装 pip...>> "%LOG_FILE%"
    python -m ensurepip --upgrade
    echo python -m ensurepip --upgrade executed >> "%LOG_FILE%"
)

echo.
echo 开始安装 requirements.txt 依赖...
python -m pip install -r requirements.txt
echo python -m pip install -r requirements.txt executed >> "%LOG_FILE%"

if errorlevel 1 (
    echo.
    echo 默认源安装失败，尝试使用清华镜像源...
    echo 默认源安装失败，尝试使用清华镜像源...>> "%LOG_FILE%"
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple executed >> "%LOG_FILE%"
)

if errorlevel 1 (
    echo.
    echo 依赖安装失败。
    echo 请检查公司网络、代理或 pip 源设置。
    echo 也可以手动执行：
    echo python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo 依赖安装失败。>> "%LOG_FILE%"
    goto fail
)

echo.
echo 依赖安装完成。
echo 现在可以双击 start_demo.bat 启动工具。
echo [%date% %time%] install_dependencies.bat succeeded >> "%LOG_FILE%"
goto done

:fail
echo.
echo 如果窗口仍然闪退，请打开 cmd，cd 到项目目录后手动运行：
echo install_dependencies.bat
echo 或查看日志文件：%LOG_FILE%
echo [%date% %time%] install_dependencies.bat failed >> "%LOG_FILE%"

goto done

:done
echo.
pause
endlocal
