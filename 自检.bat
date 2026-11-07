@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

echo ============================================================
echo   大肥鱼 AI 直播 · 一键自检
echo ============================================================
echo.

python -c "import pytest,numpy,imageio_ffmpeg; from PySide6.QtMultimedia import QAudioBufferOutput"
if errorlevel 1 (
    echo 缺少测试依赖，请先执行 python -m pip install -r requirements-dev.txt
    goto fail
)
echo [1/3] 协议、配置与直播运行回归检查
python -m pytest tests -q
if errorlevel 1 goto fail
echo.

echo [2/3] 弹幕助手桥接协议（15 种消息格式）
python -m tests.test_bridge_protocol
if errorlevel 1 goto fail
echo.

echo [3/3] 原生画面依赖与可选弹幕采集浏览器
python -c "from app.ingest.douyin_cdp import find_browser; b=find_browser(); print('  浏览器:', b or '未找到（抖音真实接入不可用，可用 bridge/mock）')"
if errorlevel 1 goto fail
python -c "import app.main,edge_tts,fastapi,websockets;print('  依赖: OK')"
if errorlevel 1 goto fail
echo.

echo ============================================================
echo   自检完成，全部通过
echo ============================================================
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   自检失败，请看上面标红的项目
echo ============================================================
pause
exit /b 1
