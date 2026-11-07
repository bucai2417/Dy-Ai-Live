from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from itertools import combinations
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.native import display
from app.native.client import LiveClient
from app.native.layout import stage_layout
from app.native.output import VideoOutput
from app.native.scene import RenderWorker, Scene
from app.native.state import FrameStore, StageState
from app.native.stunts import stunt_age
from app.native.window import StudioWindow
from app.like_goal import STUNTS


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def wait_until(qt, predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("画面未及时切换")


@pytest.mark.parametrize("raw", ['{"width":0,"height":720}', '{"width":true,"height":720}',
                                '{"width":1280.5,"height":720}', '[]', 'null', '{', '{}'])
def test_invalid_saved_size_falls_back(tmp_path, raw):
    target = tmp_path / "中文尺寸.json"
    target.write_text(raw, encoding="utf-8")
    assert display.load_size(target) == (720, 1280)


def test_saved_size_and_explicit_launch_override(tmp_path):
    target = tmp_path / "中文目录" / "画面.json"
    assert display.load_size(target) == (720, 1280)
    display.save_size(target, 1920, 1080)
    assert display.resolve_size(None, None, target) == (1920, 1080)
    assert display.resolve_size(720, 1280, target) == (720, 1280)
    assert display.resolve_size(640, None, target) == (640, 1280)
    assert not target.with_suffix(".tmp").exists()
    assert display.orientation_size(1920, 1080, "portrait") == (1080, 1920)
    assert display.orientation_size(1080, 1920, "landscape") == (1920, 1080)
    assert display.orientation_size(720, 720, "portrait") == (720, 1280)
    with pytest.raises(ValueError):
        display.save_size(target, 0, 720)
    assert display.load_size(target) == (1920, 1080)


@pytest.mark.parametrize("show_tokens,show_stats", [(True, True), (True, False), (False, True), (False, False)])
@pytest.mark.parametrize("portrait", [True, False])
def test_content_slots_fit_and_bubbles_cover_screen(show_tokens, show_stats, portrait):
    layout = stage_layout(portrait, show_tokens, show_stats)
    rects = [layout.effects, layout.messages, layout.ranking, layout.music]
    if show_tokens:
        rects.append(layout.tokens)
    if show_stats:
        rects.extend(layout.stats)
    canvas = QRectF(0, 0, layout.width, layout.height)
    for bounds in rects:
        assert canvas.contains(QRectF(*bounds))
    for first, second in combinations(rects, 2):
        assert not QRectF(*first).intersects(QRectF(*second)), (first, second)
    assert QRectF(*layout.effects).contains(QRectF(*layout.subtitle))
    assert canvas.contains(QRectF(*layout.bubbles))
    assert QRectF(*layout.bubbles).contains(QRectF(*layout.effects))
    assert layout.effects[2] * layout.effects[3] > layout.width * layout.height * .54


def test_scene_resize_preserves_assets_and_bubble_identity(qt):
    state = StageState()
    state.apply({"type": "event", "event": {"kind": "gift", "userName": "中文观众", "giftName": "墨镜"}})
    scene = Scene(360, 640)
    scene.render(state.snapshot(), 1, time.monotonic())
    avatar, gifts = scene.avatar, scene.gifts
    bubble = next(iter(gifts.bubbles.values()))
    scene.resize(640, 360)
    image = scene.render(state.snapshot(), 1, time.monotonic())
    assert (image.width(), image.height()) == (640, 360)
    assert scene.avatar is avatar and scene.gifts is gifts
    assert next(iter(gifts.bubbles.values())) is bubble
    left, top, width, height = scene.layout.bubbles
    assert left <= bubble.x <= left + width and top <= bubble.y <= top + height


def read_stream_image(response):
    assert response.readline() == b"--frame\r\n"
    assert response.readline() == b"Content-Type: image/jpeg\r\n"
    length = int(response.readline().split(b":")[1])
    assert response.readline() == b"\r\n"
    image = QImage.fromData(response.read(length))
    assert not image.isNull()
    assert response.read(2) == b"\r\n"
    return image


def test_same_mjpeg_connection_survives_repeated_resize(qt):
    state, frames = StageState(), FrameStore()
    worker = RenderWorker(state, frames, 360, 640)
    output = VideoOutput(frames, 0)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    worker.start()
    try:
        wait_until(qt, lambda: frames.sequence > 0)
        with opener.open(output.url, timeout=4) as response:
            first = read_stream_image(response)
            assert (first.width(), first.height()) == (360, 640)
            before = frames.sequence
            for size in ((640, 360), (360, 640), (1280, 720)):
                worker.request_size(*size)
                for attempt in range(80):
                    image = read_stream_image(response)
                    if (image.width(), image.height()) == size:
                        break
                else:
                    pytest.fail("同一 MJPEG 连接未收到新尺寸")
            assert frames.sequence > before and worker.is_alive() and not frames.stopped
            with opener.open(output.url.replace("live.mjpeg", "status"), timeout=3) as status_response:
                status = json.load(status_response)
            assert (status["width"], status["height"], status["orientation"]) == (1280, 720, "landscape")
        for index in range(30):
            worker.request_size(*(360, 640) if index % 2 else (640, 360))
        worker.request_size(640, 360)
        wait_until(qt, lambda: worker.size == (640, 360))
        with pytest.raises(ValueError):
            worker.request_size(0, 720)
        assert worker.is_alive() and not frames.error
    finally:
        worker.stop()
        output.close()


def test_window_switch_preserves_session_and_restores_on_restart(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(LiveClient, "open", lambda self: None)
    target = tmp_path / "画面偏好.json"
    window = StudioWindow("http://127.0.0.1:1", 360, 640, output_port=0, muted=True,
                          display_settings_path=target)
    window.status_path = tmp_path / "status.json"
    try:
        window.show()
        wait_until(qt, lambda: window.frames.sequence > 0)
        window.show_output()
        with window.state.lock:
            window.state.paused = True
            window.state.music = {"playing": True, "current": {"id": "current", "title": "当前中文歌曲"},
                                  "queue": [{"id": "next", "title": "下一首中文歌曲"}]}
            window.state.like_goal = {"active": {**STUNTS["bicycle"], "motion": "bicycle", "elapsed": 2}, "pending": 2}
            window.state.like_goal_at = time.monotonic()
            window.state.video_pending = 2
        previous = window.state.snapshot()
        audio, video, worker, url = window.audio, window.video, window.worker, window.output.url
        window.orientation_buttons["landscape"].click()
        wait_until(qt, lambda: window._displayed_size == (640, 360) and target.exists())
        assert window.popout.width() > window.popout.height()
        assert window.state.paused and window.state.music == previous["music"]
        assert window.state.video_pending == 2
        assert window.state.like_goal == previous["like_goal"]
        assert stunt_age(window.state.snapshot(), time.monotonic())[1] == 2
        assert window.audio is audio and window.video is video and window.worker is worker
        assert window.output.url == url and not window.frames.stopped
        for index in range(10):
            window.set_orientation("portrait" if index % 2 else "landscape")
        window.set_orientation("landscape")
        wait_until(qt, lambda: window._pending_display_save is None)
        window._telemetry()
        assert json.loads(window.status_path.read_text(encoding="utf-8"))["orientation"] == "landscape"
        assert display.load_size(target) == (640, 360)
    finally:
        window.close()
    restored = StudioWindow("http://127.0.0.1:1", output_port=0, muted=True, display_settings_path=target)
    restored.status_path = tmp_path / "restored-status.json"
    try:
        assert restored.worker.size == (640, 360)
        assert restored.orientation_buttons["landscape"].isChecked()
        restored.state.apply({"type": "hello", "config": {"stage": {"show_tokens": False}}})
        restored._message({"type": "hello"})
        assert restored.worker.requested_size == (640, 360)
    finally:
        restored.close()


def test_save_error_keeps_live_output_and_reports_failure(qt, tmp_path, monkeypatch):
    from app.native import window as window_module

    monkeypatch.setattr(LiveClient, "open", lambda self: None)
    window = StudioWindow("http://127.0.0.1:1", 360, 640, output_port=0, muted=True,
                          display_settings_path=tmp_path / "size.json")
    window.status_path = tmp_path / "status.json"

    def fail_save(*args):
        raise PermissionError("测试：文件只读")

    monkeypatch.setattr(window_module, "save_size", fail_save)
    try:
        window.set_orientation("landscape")
        wait_until(qt, lambda: window._pending_display_save is None)
        assert window.worker.is_alive() and window._displayed_size == (640, 360)
        assert "无法记住选择" in window.status.text()
    finally:
        window.close()


def test_closed_client_ignores_late_hello_and_connection(qt):
    state = StageState()
    client = LiveClient(state, "http://127.0.0.1:1")
    messages, connections = [], []
    client.message.connect(messages.append)
    client.connection.connect(connections.append)
    client.close()
    previous = state.snapshot()
    client._received(json.dumps({"type": "hello", "music": {"playing": True, "current": {"id": "late"}}}))
    client._connected()
    assert state.snapshot() == previous
    assert not messages and not any(connections) and not client.retry.isActive()


@pytest.mark.parametrize("options,expected", [({}, (1280, 720)), ({"width":720,"height":1280}, (720, 1280))])
def test_api_start_honors_saved_size_and_explicit_override(tmp_path, monkeypatch, options, expected):
    import subprocess
    from app import main, renderer

    target = tmp_path / "display.json"
    display.save_size(target, 1280, 720)
    monkeypatch.setattr(display, "display_path", lambda port: target)
    monkeypatch.setattr(main, "_renderer_alive", lambda: False)
    monkeypatch.setattr(renderer, "renderer_status", lambda: {"running": False})
    monkeypatch.setattr(main.app.state, "renderer_lock", asyncio.Lock(), raising=False)
    monkeypatch.setattr(main.app.state, "renderer_proc", None, raising=False)
    launches = []

    def start(args, **kwargs):
        launches.append(args)
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(subprocess, "Popen", start)
    response = asyncio.run(main.api_renderer_start(options))
    assert json.loads(response.body)["ok"]
    args = launches[0]
    assert (int(args[args.index("--width") + 1]), int(args[args.index("--height") + 1])) == expected
