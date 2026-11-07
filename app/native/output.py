from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QBuffer, QIODevice

from .state import FrameStore


def jpeg_frame(frames: FrameStore) -> bytes:
    with frames.jpeg_lock:
        image, sequence = frames.latest()
        if image is None:
            return b""
        if frames.jpeg_sequence == sequence:
            return frames.jpeg
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPEG", 86)
        frames.jpeg = bytes(buffer.data())
        frames.jpeg_sequence = sequence
        return frames.jpeg


class VideoOutput:
    def __init__(self, frames: FrameStore, port: int = 18849):
        self.frames = frames
        self.stream_fps = 25
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                return

            def do_GET(self):
                self.connection.settimeout(3)
                if self.path == "/status":
                    with frames.condition:
                        image = frames.image
                        width, height = (image.width(), image.height()) if image is not None else (0, 0)
                        body = json.dumps({"renderer": "pyside6", "frames": frames.sequence,
                                           "width": width, "height": height,
                                           "orientation": ("portrait" if height > width else "landscape") if width else None,
                                           "stream_fps": owner.stream_fps,
                                           "fps": round(frames.fps, 1), "render_ms": round(frames.render_ms, 2),
                                           "error": frames.error}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/snapshot.jpg":
                    body = jpeg_frame(frames)
                    self.send_response(200 if body else 503)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/live.mjpeg":
                    self.send_response(200)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    deadline = time.monotonic()
                    try:
                        while not owner.stopping.is_set():
                            if owner.stopping.wait(max(0, deadline - time.monotonic())):
                                break
                            with frames.condition:
                                frames.condition.wait_for(lambda: frames.image is not None or frames.stopped, timeout=2)
                                if frames.stopped:
                                    break
                            body = jpeg_frame(frames)
                            if body:
                                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                                 str(len(body)).encode() + b"\r\n\r\n" + body + b"\r\n")
                            deadline = max(deadline + 1 / owner.stream_fps, time.monotonic())
                    except (OSError, TimeoutError):
                        pass
                else:
                    self.send_error(404)

        self.stopping = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, name="native-output", daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/live.mjpeg"

    def close(self):
        self.stopping.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
