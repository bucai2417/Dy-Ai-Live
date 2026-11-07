from __future__ import annotations

import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_renderer import qt, pump
from app.native.avatars import AvatarLoader
from app.native.gift_bubbles import Bubble, GiftBubbles
from app.native.scene import Scene
from app.native.state import StageState


def gift(state, identifier, name="星海旅人", user_id="viewer-1", avatar="", count=1):
    state.apply({"type": "event", "event": {
        "id": identifier, "kind": "gift", "userName": name, "userId": user_id,
        "avatar": avatar, "giftCount": count, "giftName": "小心心", "giftValue": 0}})


def test_any_gift_adds_sender_and_repeated_gifts_update_count():
    state = StageState()
    gift(state, "event-1")
    gift(state, "event-2", count=2)
    gift(state, "event-2", count=2)
    assert len(state.gift_senders) == 1
    assert next(iter(state.gift_senders.values()))["count"] == 3
    gift(state, "event-3", user_id="viewer-2")
    assert len(state.gift_senders) == 2
    for index in range(20):
        gift(state, f"more-{index}", user_id=f"viewer-{index + 10}")
    assert len(state.gift_senders) == 10


def test_reconnect_keeps_current_bubbles_without_replaying_history():
    state = StageState()
    gift(state, "live")
    before = state.snapshot()["gift_senders"]
    state.apply({"type": "hello", "recent": [
        {"type": "event", "event": {"id": "historic", "kind": "gift", "userName": "旧观众"}}]})
    assert state.snapshot()["gift_senders"] == before
    state.apply({"type": "reset"})
    assert not state.gift_senders and not state.avatar_images


def test_backend_routing_prevents_keyword_motion_on_negated_chat():
    state = StageState()
    state.config["interact"] = {"enabled": True, "intent_routing": True}
    state.apply({"type": "event", "event": {"id": "one", "kind": "chat", "text": "不要跳舞"}})
    assert not state.reaction
    state.apply({"type": "reaction", "motion": "dance"})
    assert state.reaction["motion"] == "dance"


def test_repeated_backend_action_restarts_animation(monkeypatch):
    from app.native import state as state_module
    clock = [10.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: clock[0])
    state = StageState()
    state.apply({"type": "reaction", "motion": "wave", "event_id": "first"})
    clock[0] += 0.2
    state.apply({"type": "reaction", "motion": "wave", "event_id": "second"})
    assert state.reaction["started"] == 10.2


def test_collision_exchanges_velocity_and_separates_bubbles():
    arena = GiftBubbles((0, 0, 500, 500))
    first = Bubble("one", 180, 250, 60, 0, 30, "#ffffff", {})
    second = Bubble("two", 239, 250, -60, 0, 30, "#ffffff", {})
    arena.bubbles = {"one": first, "two": second}
    arena._step(1 / 60, True)
    assert first.vx < 0 and second.vx > 0
    assert second.x - first.x >= 62 - 1e-8
    assert first.vx ** 2 + second.vx ** 2 == pytest.approx(7200)


@pytest.mark.parametrize("bounds", [(24, 220, 672, 440), (320, 130, 620, 420)])
def test_maximum_bubbles_stay_finite_inside_arena(bounds):
    state = StageState()
    for index in range(12):
        gift(state, f"event-{index}", user_id=f"viewer-{index}")
    arena = GiftBubbles(bounds)
    settings = {"ball_max": 12, "ball_size": 1.8, "ball_speed": 3}
    senders = state.snapshot()["gift_senders"]
    for frame in range(240):
        arena.update(senders, settings, frame / 30)
    left, top, width, height = bounds
    assert len(arena.bubbles) == 10
    for bubble in arena.bubbles.values():
        assert math.isfinite(bubble.vx + bubble.vy + bubble.x + bubble.y)
        assert left + bubble.radius <= bubble.x <= left + width - bubble.radius
        assert top + bubble.radius <= bubble.y <= top + height - bubble.radius
    positions = [(bubble.x, bubble.y) for bubble in arena.bubbles.values()]
    arena.update(senders, settings, 1000, paused=True)
    current_positions = [(bubble.x, bubble.y) for bubble in arena.bubbles.values()]
    for current, previous in zip(current_positions, positions, strict=True):
        assert current == pytest.approx(previous)
    arena.update(senders, {"show_ballpit": False}, 1001)
    assert not arena.bubbles


@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
def test_gifts_render_with_avatar_and_chinese_name(qt, tmp_path, size):
    from PySide6.QtGui import QColor, QImage
    state = StageState()
    state.connected = True
    gift(state, "image", avatar="https://avatar.invalid/test.png")
    image = QImage(96, 96, QImage.Format.Format_RGB32)
    image.fill(QColor("#ff70c0"))
    state.set_avatar("https://avatar.invalid/test.png", image)
    snapshot = state.snapshot()
    scene = Scene(*size)
    frame = scene.render(snapshot, 1, time.monotonic())
    assert not frame.isNull() and scene.gifts.bubbles
    sprite = next(iter(scene.gifts.sprites.values()))[1]
    scene.render(snapshot, 2, time.monotonic())
    assert next(iter(scene.gifts.sprites.values()))[1].cacheKey() == sprite.cacheKey()
    snapshot["config"]["stage"]["show_ballpit"] = False
    hidden = scene.render(snapshot, 1, time.monotonic())
    assert hidden != frame


def test_avatar_loader_is_async_bounded_and_uses_cache(qt):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage
    image = QImage(80, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("#69cbd5"))
    output = QByteArray()
    buffer = QBuffer(output)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    payload = bytes(output)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload if self.path == "/avatar.png" else b"broken-image")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state = StageState()
    loader = AvatarLoader(state)
    url = f"http://127.0.0.1:{server.server_port}/avatar.png"
    try:
        gift(state, "first", avatar=url)
        loader.refresh()
        deadline = time.monotonic() + 3
        while not state.avatar_images and time.monotonic() < deadline:
            pump(qt, 0.01)
        assert url in state.avatar_images
        avatar = state.avatar_images[url]
        assert avatar.width() == avatar.height() <= 128
        gift(state, "repeat", avatar=url)
        loader.refresh()
        pump(qt, 0.05)
        assert requests == ["/avatar.png"]
        gift(state, "bad", user_id="bad", avatar=url.replace("avatar.png", "bad"))
        gift(state, "local", user_id="local", avatar="file:///private.png")
        loader.refresh()
        pump(qt, 0.1)
        assert len(state.avatar_images) == 1 and not loader.active
        assert len(loader.cache) == 1
    finally:
        loader.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
