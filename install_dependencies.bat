@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo 安装 matrix-signal-diff-agent 依赖
echo ========================================
echo 当前目录：%cd%
echo.

echo 检查 Python 环境...
python --version
if errorlevel 1 (
    echo 未找到 python，请确认 Python 已安装并加入 PATH。
    pause
    exit /b 1
)

echo.
echo 检查 pip...
python -m pip --version
if errorlevel 1 (
    echo pip 不可用，尝试安装 pip...
    python -m ensurepip --upgrade
)

echo.
echo 开始安装 requirements.txt 依赖...
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo 默认源安装失败，尝试使用清华镜像源...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)

if errorlevel 1 (
    echo.
    echo 依赖安装失败。
    echo 请检查公司网络、代理或 pip 源设置。
    echo 也可以手动执行：
    echo python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo.
echo 依赖安装完成。
echo 现在可以双击 start_demo.bat 启动工具。
pause
