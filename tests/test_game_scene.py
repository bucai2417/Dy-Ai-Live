from __future__ import annotations

import copy
import time

import pytest

from app.minigames import MiniGame, validate_question
from app.native.game_scene import game_view
from app.native.preferences import Preferences
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS, Effective, store
from tests.test_ai_games import drawing
from tests.test_runtime import isolated_runtime
from tests.test_speech_idle import FakeClient, qt


@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
@pytest.mark.parametrize("kind", ["idiom", "drawing"])
def test_native_game_renders_question_result_and_freezes(qt, size, kind):
    state = StageState()
    state.config = copy.deepcopy(DEFAULTS)
    game = MiniGame()
    game.start(kind, Effective.games(), now=100)
    game.install(validate_question(kind, drawing() if kind == "drawing" else {"word": "心想事成", "meaning": "愿望实现"}), now=100)
    state.apply({"type": "game", "game": game.snapshot(104)})
    scene = Scene(*size)
    now = time.monotonic()
    early = scene.render(state.snapshot(), 1, now)
    full = scene.render(state.snapshot(), 10, now + 10)
    assert not early.isNull() and early != full
    game.award({"id": "中文", "name": "爱看小鱼画画的观众"}, "成千上万" if kind == "idiom" else "雨伞", now=120)
    state.apply({"type": "game", "game": game.snapshot(121)})
    won = scene.render(state.snapshot(), 21, time.monotonic())
    assert won != full
    state.apply({"type": "state", "paused": True})
    snapshot = state.snapshot()
    assert game_view(snapshot, now + 30) == game_view(snapshot, now + 100)


def test_native_reconnect_and_gift_pause_do_not_double_advance(monkeypatch):
    clock = [100.]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    state = StageState()
    state.apply({"type": "hello", "game": {"active": True, "elapsed": 5, "remaining": 55, "paused": False}})
    clock[0] = 105
    state.apply({"type": "state", "paused": True})
    assert state.game["elapsed"] == 10 and state.game["remaining"] == 50
    clock[0] = 110
    state.apply({"type": "game", "game": {"active": True, "elapsed": 10, "remaining": 50, "paused": True}})
    clock[0] = 150
    state.apply({"type": "state", "paused": False})
    assert game_view(state.snapshot(), 155)["elapsed"] == 15
    state.apply({"type": "reset"})
    assert not state.game


def test_settings_save_chinese_topics_and_game_controls(qt):
    client = FakeClient()
    dialog = Preferences(client)
    try:
        client.response.emit("GET /api/config", store.get_all())
        dialog._save()
        patch = client.posts[-1][1]
        assert patch["idle"]["ai_chat"] is True
        assert patch["games"]["auto_start"] is False
        patch["idle"]["topic_preferences"] = "聊聊海底世界、童年趣事，不重复昨天的话题"
        store.update(patch)
        assert "海底世界" in Effective.idle()["topic_preferences"]
    finally:
        dialog.close()
