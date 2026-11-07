from __future__ import annotations

import asyncio
import copy
import json
import random
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from PySide6.QtGui import QImage, QPainter

from app import main, playlist
from app.brain import scheduler
from app.brain.capabilities import available_commands, status_line
from app.brain.intent import decode_intent, local_intent
from app.events import Event
from app.fishing import FISH, FishingGame
from app.native.avatar.rig import joint_pose
from app.native.fishing_scene import fishing_view
from app.native.scene import Scene
from app.native.state import StageState
from app.native.ticker import TickerRenderer
from app.settings_store import DEFAULTS, Effective, SettingsStore, store
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def game_at_bite(seed=5, escape=0):
    game = FishingGame({"enabled": True, "escape_chance": escape, "likes_per_pull": 10,
                        "bite_min": 4, "bite_max": 8, "fight_min": 25, "fight_max": 25}, random.Random(seed))
    game.start("海风会客厅", 0)
    assert 4 <= game.deadline <= 8
    game.tick(8)
    return game


@pytest.mark.parametrize("text,operation", [("钓鱼", "start"), ("去钓鱼", "start"), ("小鱼去钓鱼吧", "start"),
    ("拉", "pull"), ("拉钩", "pull"), ("拉勾", "pull"), ("拉一下", "pull"), ("提竿", "pull"),
    ("不钓了", "cancel"), ("收竿", "cancel"), ("结束钓鱼", "cancel")])
def test_fishing_intent(text, operation):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == ("fishing", operation)
    assert decode_intent(json.dumps({"action": "fishing", "motion": operation, "confidence": 1})).motion == operation


@pytest.mark.parametrize("text", ["不要钓鱼", "今天钓鱼很开心", "他说拉钩", "如果去钓鱼", "拉近镜头"])
def test_fishing_does_not_capture_unrelated_text(text):
    assert local_intent(text).action != "fishing"


def test_waiting_cannot_pull_and_repeated_start_preserves_round():
    game = FishingGame(Effective.fishing(), random.Random(2))
    game.start("星海露台", 0)
    original = game.snapshot(1)
    with pytest.raises(ValueError, match="还没咬钩"):
        game.pull(user={"name": "过早拉钩者"}, now=1)
    assert game.winner is None
    with pytest.raises(ValueError, match="还没结束"):
        game.start("room", 1)
    assert game.snapshot(1) == original


def test_equal_contribution_keeps_first_and_repeated_messages_count():
    game = game_at_bite()
    game.add_likes(5, "likes", 9)
    assert game.winner is None
    game.pull(user={"name": "星海旅人", "id": "first"}, event_id="chat1", now=10)
    progress = game.progress
    assert not game.pull(user={"name": "网络重复"}, event_id="chat1", now=10)
    assert game.progress == progress
    game.pull(user={"name": "另一位观众", "id": "second"}, event_id="chat2", now=10)
    assert game.progress > progress
    game.add_likes(10000, "finish", 11)
    assert game.phase == "landing" and game.progress == 1
    assert game.winner == {"name": "星海旅人", "id": "first"}
    assert game.tick(13) and game.phase == "caught"
    assert game.fish["name"] in {fish["name"] for fish in FISH.values()}
    assert game.fish["weight"] > 0 and game.fish["value"] > 0
    assert game.tick(19) and not game.active
    assert game.return_scene == "海风会客厅"


def test_likes_only_do_not_invent_a_winner():
    game = game_at_bite()
    game.add_likes(10000, "all-likes", 9)
    assert game.winner is None
    game.tick(11)
    assert game.phase == "caught" and game.winner is None


def test_probability_escape_and_timeout_return():
    game = game_at_bite(escape=.5)
    game.rng.random = lambda: 0
    assert game.pull(now=9)
    assert game.phase == "escaped" and "脱钩" in game.reason
    game.tick(13)
    assert not game.active
    game = game_at_bite()
    assert game.tick(34) and game.phase == "escaped"
    assert "太久" in game.reason
    game.tick(38)
    assert not game.active


@pytest.mark.parametrize("phase", ["waiting", "hooked", "landing", "caught", "escaped"])
def test_pause_freezes_all_game_phases_and_reconnect(phase, monkeypatch):
    game = game_at_bite()
    game._phase(phase, 8, 25)
    game.pause(True, 9)
    frozen = game.snapshot(100)
    assert not game.tick(100)
    assert not game.add_likes(100, now=100)
    with pytest.raises(ValueError, match="暂停"):
        game.pull(now=100)
    assert game.snapshot(200) == frozen
    clock = [200.0]
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: clock[0])
    state = StageState()
    state.apply({"type": "hello", "paused": True, "fishing": frozen})
    assert fishing_view(state.snapshot(), 400)["remaining"] == frozen["remaining"]
    game.pause(False, 200)
    state.apply({"type": "state", "paused": False})
    assert fishing_view(state.snapshot(), 201)["remaining"] == pytest.approx(game.snapshot(201)["remaining"])


def test_fish_population_has_sizes_values_and_seeded_variation():
    assert len({fish["size"] for fish in FISH.values()}) >= 4
    assert len({fish["value"] for fish in FISH.values()}) >= 20
    caught = {game_at_bite(seed).fish["kind"] for seed in range(50)}
    assert len(caught) >= 5
    assert len(game_at_bite().school) == 12
    assert {entry["kind"] for entry in game_at_bite().school} <= set(FISH)


def test_brain_direct_repeated_pull_and_cumulative_likes(monkeypatch):
    async def scenario():
        store.update({"fishing": {"escape_chance": 0}})
        brain = scheduler.Brain()
        brain.world.command("activity", "sleep", manual=True, now=time.monotonic() - 30)
        before = brain.world.describe()
        brain.likes.add(Event(kind="like", like_count=0, like_total=100, like_source="room"))
        original_total = brain.likes.total
        classifier = AsyncMock(side_effect=AssertionError("钓鱼明确指令不调用 AI"))
        monkeypatch.setattr(brain.llm, "classify_intent", classifier)
        assert await brain._publish_event(Event(kind="chat", text="去钓鱼"))
        assert brain.fishing.active and brain.world.paused_at is not None and brain.likes.paused_at is not None
        assert not brain._ambient_available()
        brain.fishing.deadline = time.monotonic() - 1
        brain.fishing.tick()
        for name in ("首拉观众", "第二位"):
            assert await brain._publish_event(Event(kind="chat", text="拉钩", user_name=name))
        assert brain.fishing.winner["name"] == "首拉观众"
        progress = brain.fishing.progress
        await brain._publish_event(Event(kind="like", like_total=105, like_source="room"))
        assert brain.likes.total == original_total + 5
        assert brain.fishing.progress > progress
        progress = brain.fishing.progress
        await brain._publish_event(Event(kind="like", like_total=105, like_source="room"))
        assert brain.fishing.progress == progress
        assert not brain.likes.pending
        assert "云汐钓台" in status_line(brain.runtime_context())
        await brain.fishing_command("cancel")
        after = brain.world.describe()
        assert after["scene"] == before["scene"] and after["activity"] == "sleep" and not after["paused"]
        assert brain.likes.paused_at is None
        classifier.assert_not_called()
    asyncio.run(scenario())


def test_game_return_restores_mid_motion_and_live_pause(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.world.command("activity", "run", manual=True)
        await brain.fishing_command("start")
        frozen = brain.world.snapshot()
        await brain.set_paused(True)
        game = brain.fishing.snapshot()
        await brain.set_paused(False)
        assert brain.world.snapshot() == frozen
        assert brain.fishing.snapshot()["remaining"] == pytest.approx(game["remaining"], abs=.1)
        brain.fishing._phase("caught", time.monotonic() - 8, 6)
        brain.fishing.tick()
        await brain._sync_fishing()
        assert not brain.fishing.active and brain.world.paused_at is None
        assert brain.world.snapshot()["movement"]["elapsed"] == pytest.approx(frozen["movement"]["elapsed"], abs=.1)
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [{"ticker": {"speed": 0}}, {"ticker": {"speed": 241}},
    {"ticker": {"text": "鱼" * 2001}}, {"fishing": {"escape_chance": .51}},
    {"fishing": {"likes_per_pull": 1.5}}, {"fishing": {"likes_per_pull": 0}}])
def test_invalid_game_or_ticker_settings(patch):
    with pytest.raises(ValueError):
        store.update(patch)


def test_chinese_ticker_settings_roundtrip_and_capabilities():
    text = "去钓鱼！咬钩后发拉钩 🐟\n一起玩"
    store.update({"ticker": {"text": text, "speed": 88}, "fishing": {"enabled": False}})
    assert SettingsStore().section("ticker")["text"] == text
    assert Effective.ticker()["speed"] == 88
    assert "fishing" not in available_commands()


@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
def test_fishing_frames_and_celebration_are_distinct(qt, size):
    state = StageState()
    state.config.update(stage=copy.deepcopy(DEFAULTS["stage"]), ticker=copy.deepcopy(DEFAULTS["ticker"]))
    game = game_at_bite()
    snapshot = state.snapshot()
    snapshot.update(fishing=game.snapshot(8), fishing_at=0, animation_time=0)
    scene = Scene(*size)
    hooked = scene.render(snapshot, 0, 0)
    game.pull(100, user={"name": "中文首拉观众"}, now=9)
    game.tick(11)
    snapshot["fishing"] = game.snapshot(12)
    caught = scene.render(snapshot, 0, 0)
    assert hooked != caught and not hooked.isNull()
    assert all(image.hasAlphaChannel() and not image.isNull() for image in scene.fishing.fish_images.values())
    assert len({image.cacheKey() for image in scene.fishing.fish_images.values()}) == len(FISH)
    snapshot.update(paused=True, paused_at=0)
    assert scene.render(snapshot, 10, 100) == scene.render(snapshot, 20, 200)


def test_ticker_speed_pause_empty_and_cached_text(qt):
    ticker = TickerRenderer()
    image = QImage(720, 1280, QImage.Format.Format_RGB32)
    image.fill(0)
    painter = QPainter(image)
    config = {"enabled": True, "text": "玩法介绍：发拉钩协力", "speed": 20}
    try:
        assert ticker.draw(painter, config, 720, 1280, 0)
        cached = ticker.strip
        ticker.draw(painter, config, 720, 1280, 2)
        assert ticker.distance == 40 and ticker.strip is cached
        ticker.draw(painter, config, 720, 1280, 2)
        assert ticker.distance == 40
        ticker.draw(painter, {**config, "speed": 40}, 720, 1280, 3)
        assert ticker.distance == 80
        assert not ticker.draw(painter, {**config, "text": ""}, 720, 1280, 4)
        assert not ticker.draw(painter, {**config, "enabled": False}, 720, 1280, 5)
    finally:
        painter.end()


def test_fishing_pose_changes_arms_and_tail():
    idle = joint_pose({}, 2, 2)
    hold = joint_pose({"motion": "fishing", "duration": 20, "started": 0}, 2, 2)
    pull = joint_pose({"motion": "fishing_pull", "duration": 20, "started": 0}, 2, 2)
    assert hold["tail"] != idle["tail"]
    assert hold["upper_right"] != pull["upper_right"]


def test_websocket_reconnect_and_disable_or_clear_returns_from_game():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as socket:
            hello = socket.receive_json()
            assert "ticker" in hello["config"] and not hello["fishing"]["active"]
            socket.receive_json()
            socket.send_json({"cmd": "fishing", "action": "start"})
            while True:
                message = socket.receive_json()
                if message["type"] == "fishing":
                    assert message["fishing"]["active"]
                    break
        with client.websocket_connect("/ws") as socket:
            hello = socket.receive_json()
            assert hello["fishing"]["active"]
        client.post("/api/config", json={"fishing": {"enabled": False}})
        assert not main.app.state.brain.fishing.active
        assert main.app.state.brain.world.paused_at is None
        assert client.get("/api/ai/context").json()["context"]["fishing"]["active"] is False
