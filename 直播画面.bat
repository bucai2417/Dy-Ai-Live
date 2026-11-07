@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title 深蓝 - 原生直播画面
cd /d "%~dp0"
call "%~dp0启动.bat" %*
exit /b %errorlevel%
