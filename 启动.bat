@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title 大肥鱼 AI 直播
cd /d "%~dp0" || goto fail

echo ==========================================
echo   大肥鱼 AI 直播  启动器
echo ==========================================
echo.

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [提示] 已从 .env.example 创建 .env，请按需修改配置
    echo.
)

echo [1/2] 检查 Python 和依赖...
python --version
if errorlevel 1 goto no_python
python -c "import app.main,uvicorn,edge_tts,websockets,numpy,imageio_ffmpeg; from PySide6.QtMultimedia import QAudioBufferOutput"
if errorlevel 1 (
    echo [安装] 首次运行，正在安装依赖...
    python -m pip install -r requirements-desktop.txt
    if errorlevel 1 goto fail
)

echo [2/2] 启动服务...
python -m app --desktop %*
if errorlevel 1 goto fail
pause
exit /b 0

:fail
echo 启动失败，请检查上方错误、网络及 Python 环境。
pause
exit /b 1

:no_python
echo 找不到可运行的 Python，请安装 Python 3.10 或更新版本，并勾选 Add python.exe to PATH。
pause
exit /b 1
