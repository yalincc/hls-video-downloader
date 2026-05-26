@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo   视频下载器
echo ================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未安装 Python，请先安装: https://www.python.org/downloads/
    pause
    exit /b 1
)

pip show requests >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在安装依赖...
    pip install -r requirements.txt
    echo.
)

echo 浏览器将自动打开，如果没有请访问: http://127.0.0.1:5001
echo 按 Ctrl+C 停止
echo.

python run.py
pause
