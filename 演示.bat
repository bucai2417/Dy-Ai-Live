@echo off
setlocal
chcp 65001 >nul
title 大肥鱼 · 模拟弹幕演示
cd /d "%~dp0"
echo 以「模拟弹幕」模式启动，无需直播间即可演示效果
echo.
set PYTHONUTF8=1
call "%~dp0启动.bat" --demo %*
exit /b %errorlevel%
