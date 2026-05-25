@echo off
title HLS Video Downloader
cd /d "%~dp0.."
echo.
echo   ============================
echo   📥 HLS Video Downloader
echo   ============================
echo.
echo   正在启动...

:: 启动后台 Worker
start "下载Worker" /min python -u web_downloader\worker.py

:: 等待 Worker 初始化
timeout /t 2 /nobreak >nul

:: 启动 Web 服务
start http://localhost:5001
python -u web_downloader\server.py
