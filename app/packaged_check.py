from __future__ import annotations

import json
import os
import socket
import threading
import time


def run(report_path):
    os.environ["QT_QPA_PLATFORM"] = "windows" if os.name == "nt" else "offscreen"
    os.environ["INGEST"] = "mock"
    os.environ["TTS_ENGINE"] = "none"
    os.environ["LLM_API_KEY"] = ""
    import uvicorn
    from PySide6.QtCore import QTimer, Qt, qVersion
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from . import __version__
    from .paths import ROOT, RESOURCE_ROOT
    from .native.avatar import LayeredAvatar
    from .native.window import StudioWindow
    from .__main__ import service_ready

    report_path.parent.mkdir(parents=True, exist_ok=True)
    application = QApplication([])
    application.setFont(QFont("Microsoft YaHei UI", 10))
    font_count = len(QFontDatabase.families())
    if not font_count:
        raise RuntimeError("原生平台未加载任何系统字体")
    LayeredAvatar()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    output_listener = socket.socket()
    output_listener.bind(("127.0.0.1", 0))
    output_port = output_listener.getsockname()[1]
    output_listener.close()
    server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 25
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            server.should_exit = True
            raise RuntimeError("打包后的后台未能启动")
        time.sleep(0.05)
    base_url = f"http://127.0.0.1:{port}"
    window = StudioWindow(base_url, 720, 1280, 30, output_port, True)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    result = {"ok": False}
    deadline = time.monotonic() + 30

    def inspect():
        image, sequence = window.frames.latest()
        if window.state.connected and image is not None and sequence > 2:
            screenshot = report_path.with_suffix(".png")
            result.update(ok=service_ready(base_url) and image.save(str(screenshot)),
                          version=__version__, qt=qVersion(), websocket=True,
                          runtime_root=str(ROOT), resource_root=str(RESOURCE_ROOT),
                          frame_size=[image.width(), image.height()], frames=sequence, font_families=font_count)
            window.close()
            application.quit()
        elif time.monotonic() > deadline:
            result["error"] = "原生窗口未收到后台消息或渲染画面"
            window.close()
            application.quit()
        else:
            QTimer.singleShot(100, inspect)

    QTimer.singleShot(100, inspect)
    try:
        application.exec()
    finally:
        window.close()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1
