"""PySide6 原生直播工作台，独立线程渲染，不使用浏览器。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 18849


def renderer_status(port: int = DEFAULT_PORT) -> dict:
    path = ROOT / "data" / f"native-renderer-{port}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["running"] = time.time() - float(data.get("updated", 0)) < 5
        return data
    except (OSError, ValueError, TypeError):
        return {"running": False, "backend": "pyside6"}


def main(argv=None) -> int:
    from .config import settings
    from .native.display import display_path, resolve_size

    parser = argparse.ArgumentParser(description="深蓝原生直播工作台")
    parser.add_argument("--url", default=f"http://127.0.0.1:{settings.port}")
    parser.add_argument("--width", type=int, help="输出宽度；未指定尺寸时使用上次选择")
    parser.add_argument("--height", type=int, help="输出高度；与宽度一起指定可覆盖上次选择")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--output-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--mute", action="store_true")
    options = parser.parse_args(argv)
    try:
        options.width, options.height = resolve_size(options.width, options.height, display_path(options.output_port))
    except ValueError as error:
        parser.error(str(error))
    if not (320 <= options.width <= 1920 and 320 <= options.height <= 1920 and 10 <= options.fps <= 60):
        parser.error("画面尺寸应在 320～1920，帧率应在 10～60")
    if not 1024 <= options.output_port <= 65535:
        parser.error("视频输出端口应在 1024～65535")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO)
    try:
        from PySide6.QtCore import QLockFile
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication, QMessageBox
        from .native.window import StudioWindow
    except ImportError as error:
        print(f"桌面依赖未安装：{error}。请执行 python -m pip install -r requirements-desktop.txt")
        return 1
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setApplicationName("深蓝直播工作台")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    (ROOT / "data").mkdir(exist_ok=True)
    lock = QLockFile(str(ROOT / "data" / f"native-renderer-{options.output_port}.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, "工作台已打开", "同一视频输出端口的工作台已经运行，请使用已有窗口。")
        return 0
    try:
        base_url = options.url.removesuffix("/stage").rstrip("/")
        window = StudioWindow(base_url, options.width, options.height, options.fps, options.output_port, options.mute)
        window.show()
        return app.exec()
    except Exception as error:
        logging.exception("原生工作台启动失败")
        QMessageBox.critical(None, "启动失败", str(error))
        return 1
    finally:
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
