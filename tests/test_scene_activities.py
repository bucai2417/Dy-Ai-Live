from __future__ import annotations

import asyncio
import json
import math
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.brain.intent import decode_intent, local_intent
from app.brain import scheduler
from app.events import Event
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import Effective, store
from app.wardrobe import wear
from app.world import WorldDirector, view
from app.world_navigation import LANDMARKS, route, walkable
from tests.test_runtime import isolated_runtime
from tests.test_world_scene import qt


@pytest.mark.parametrize("text,command", [
    ("跑步", "activity:run"), ("小鱼跑起来吧", "activity:run"), ("开车", "activity:drive"),
    ("开车兜风", "activity:drive"), ("去沙发睡觉", "activity:sleep"), ("躺下", "activity:sleep"),
    ("去窗边看风景", "activity:window"), ("喝杯茶", "activity:tea"), ("起床", "activity:wake"),
    ("看看云海", "activity:lookout"), ("进屋", "activity:enter"), ("出门", "activity:exit"),
    ("去云台", "scene:cloud"), ("去花园", "move:garden"), ("去停车位", "move:parking"),
])
def test_activity_language(text, command):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == ("world", command)
    assert decode_intent(json.dumps({"action": "world", "motion": command, "confidence": .99})).motion == command


@pytest.mark.parametrize("text", ["不要开车", "今天跑步很累", "他说去沙发睡觉", "如果开车就好了", "别换衣服"])
def test_activity_discussion_is_not_a_command(text):
    assert local_intent(text).action == "chat"


@pytest.mark.parametrize("scene", ["room", "terrace", "cloud"])
@pytest.mark.parametrize("activity,duration", [("run", 15), ("drive", 18)])
def test_real_path_duration_boundaries_and_finish(scene, activity, duration):
    director = WorldDirector({**Effective.world(), "scene": scene}, 0)
    director.command("activity", activity, manual=True, now=0)
    assert director.movement["duration"] == duration
    positions = [view(director.snapshot(index / 10))["position"] for index in range(duration * 10 + 1)]
    assert len({tuple(point) for point in positions}) > 100
    assert all(walkable(director.scene, point) for point in positions)
    assert max(math.dist(start, end) for start, end in zip(positions, positions[1:])) < .5
    assert view(director.snapshot(duration - .01))["activity"] == activity
    assert view(director.snapshot(duration))["activity"] == ("parked" if activity == "drive" else "idle")
    if activity == "drive":
        assert director.scene == "cloud"
        assert positions[-1] == pytest.approx(LANDMARKS["cloud"]["parking"])


@pytest.mark.parametrize("place,activity", [("sofa", "sleep"), ("window", "window"), ("tea", "tea")])
def test_arrival_triggers_interaction_and_camera(place, activity):
    director = WorldDirector(Effective.world(), 0)
    director.command("move", place, manual=True, now=0)
    assert view(director.snapshot(.01))["activity"] == "travel"
    arrived = view(director.snapshot(30))
    assert arrived["activity"] == activity
    assert arrived["position"] == pytest.approx(LANDMARKS["room"][place])
    assert arrived["camera"][2] < 12.8
    director.command("activity", "wake", manual=True, now=30)
    assert view(director.snapshot(50))["activity"] == "idle"
    assert view(director.snapshot(50))["position"] == [0, .7]


def test_directional_approach_triggers_window():
    director = WorldDirector(Effective.world(), 0)
    for moment in (0, 3, 6):
        director.command("move", "back", manual=True, now=moment)
    assert view(director.snapshot(20))["activity"] == "window"


@pytest.mark.parametrize("activity", ["run", "drive", "sleep", "window", "tea"])
def test_pause_reconnect_and_stop_do_not_teleport(activity, monkeypatch):
    director = WorldDirector(Effective.world(), 0)
    director.command("activity", activity, manual=True, now=0)
    director.pause(True, 4)
    frozen = view(director.snapshot(100))
    clock = [100.0]
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: clock[0])
    state = StageState()
    state.apply({"type": "hello", "paused": True, "world": director.snapshot(100)})
    assert view(state.world) == frozen
    director.pause(False, 104)
    assert view(director.snapshot(104)) == frozen
    director.command("move", "stop", manual=True, now=104)
    assert view(director.snapshot(130))["position"] == pytest.approx(frozen["position"])
    assert view(director.snapshot(130))["activity"] == ("parked" if activity == "drive" else "idle")


def test_routes_avoid_fountain_and_reject_invalid_places():
    points = route("cloud", (-3, .3), (3, .3))
    assert len(points) > 2
    for origin, target in zip(points, points[1:]):
        for index in range(51):
            assert walkable("cloud", [start + (end - start) * index / 50 for start, end in zip(origin, target)])
    director = WorldDirector(Effective.world(), 0)
    before = director.snapshot(0)
    with pytest.raises(ValueError, match="当前场景"):
        director.command("move", "parking", manual=True, now=0)
    assert director.snapshot(0) == before


def test_generic_outfit_and_accessories_cycle_on_every_repeated_command():
    async def scenario():
        bus = AsyncMock()
        await wear("sailor", bus)
        await wear("cool", bus)
        names = [await wear(local_intent("换衣服").motion, bus) for _ in range(7)]
        assert names == ["raincoat", "starry", "sailor", "raincoat", "starry", "sailor", "raincoat"]
        assert Effective.stage()["costume"] == "cool"
        accessories = [await wear("next_accessory", bus) for _ in range(4)]
        assert all(start != end for start, end in zip(accessories, accessories[1:]))
        assert Effective.stage()["outfit"] == "raincoat"
        assert await wear("starry", bus) == await wear("starry", bus) == "starry"
    asyncio.run(scenario())


def test_repeated_chat_outfits_do_not_depend_on_model_guess(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.llm.classify_intent = AsyncMock()
        brain._speak = AsyncMock()
        await wear("sailor", scheduler.hub)
        selected = []
        for _ in range(4):
            await brain.handle(Event(kind="chat", user_name="同一位观众", text="换衣服"))
            selected.append(Effective.stage()["outfit"])
        assert selected == ["raincoat", "starry", "sailor", "raincoat"]
        brain.llm.classify_intent.assert_not_awaited()
        assert brain._speak.await_count == 4
    asyncio.run(scenario())


@pytest.mark.parametrize("activity", ["run", "drive", "sleep", "window", "tea", "lookout"])
@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
def test_activity_render_pauses_and_keeps_outfit(qt, activity, size):
    state = StageState()
    state.config["world"] = Effective.world()
    state.config["stage"] = {"scale": .64}
    state.outfit, state.costume = "starry", "cool"
    director = WorldDirector(Effective.world(), 0)
    director.command("activity", activity, manual=True, now=0)
    state.world, state.world_at = director.snapshot(8), 8
    sample = state.snapshot()
    sample.update(paused=True, paused_at=8, animation_time=8)
    scene = Scene(*size)
    first = scene.render(sample, 8, 8)
    assert first == scene.render(sample, 99, 99)
    assert scene.rig is not None and not first.isNull()
    assert sample["outfit"] == "starry" and sample["costume"] == "cool"


def test_native_command_and_reconnect_include_activity():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as connection:
            connection.receive_json()
            connection.receive_json()
            connection.send_json({"cmd": "world", "action": "activity", "value": "drive"})
            world = connection.receive_json()["world"]
            assert world["scene"] == "cloud" and world["activity"] == "drive"
        with client.websocket_connect("/ws") as connection:
            restored = connection.receive_json()["world"]
            assert restored["movement"]["path"] == world["movement"]["path"]
            assert restored["movement"]["elapsed"] >= world["movement"]["elapsed"]
