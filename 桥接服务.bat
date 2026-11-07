@echo off
setlocal
chcp 65001 >nul
title 大肥鱼 · 弹幕桥接服务
cd /d "%~dp0"
echo ==========================================
echo   弹幕桥接服务（HTTP 回调到主程序）
echo ==========================================
echo.
echo 弹幕助手回调地址请填：  http://127.0.0.1:5002/danmu
echo 自测：浏览器打开 http://127.0.0.1:5002/danmu
echo.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -m app.bridge_server --port 5002
if errorlevel 1 goto fail
pause
exit /b 0

:fail
echo 桥接服务启动失败，请检查上方错误、Python 依赖或 5002 端口是否被占用。
pause
exit /b 1
