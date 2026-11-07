from __future__ import annotations

import asyncio
import json
import math
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.brain import scheduler
from app.brain.intent import Intent, decode_intent, local_intent
from app.events import Event
from app.like_goal import STUNTS
from app.native.avatar.rig import joint_pose
from app.native.scene import Scene
from app.native.state import StageState
from app.native.stunts import swim_path
from app.native.world_scene import Projector, WorldRenderer, build_room, build_terrace
from app.settings_store import Effective, store
from app.world import CAMERA_POSES, COMMANDS, SCENES, WEATHERS, WorldDirector, view
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


@pytest.mark.parametrize("text,command", [
    ("小鱼去窗边吧", "move:window"), ("往左游", "move:left"), ("向右走两步", "move:right"),
    ("请向前游", "move:forward"), ("后退一下", "move:back"), ("回到中央", "move:center"),
    ("去沙发旁", "move:sofa"), ("停止移动", "move:stop"),
    ("镜头向右转", "camera:turn_right"), ("拉近镜头", "camera:closer"), ("切换俯视", "camera:overhead"),
    ("让这里下雪吧", "weather:snow"), ("切换雨天", "weather:rain"), ("下点雨", "weather:rain"),
    ("切换晴天", "weather:clear"), ("换成星夜", "weather:night"), ("去露台", "scene:terrace"),
    ("回到室内", "scene:room"),
])
def test_natural_scene_intent(text, command):
    intent = local_intent(text)
    assert intent.action == "world" and intent.motion == command


@pytest.mark.parametrize("text", ["今天下雨了", "不要下雪", "如果下雪就好了", "他说去露台", "镜头真好看", "别往左游"])
def test_discussion_does_not_control_scene(text):
    assert local_intent(text).action == "chat"


def test_ai_allowlist_and_offline_swim():
    for kind, values in COMMANDS.items():
        for value in values:
            intent = decode_intent(json.dumps({"action": "world", "motion": f"{kind}:{value}", "confidence": .95}))
            assert intent.motion == f"{kind}:{value}"
    for value in ("move:../../file", "weather:storm", "camera:999", {}, None):
        assert decode_intent(json.dumps({"action": "world", "motion": value, "confidence": .95})) is None
    assert local_intent("游来游去").motion == "swim"
    assert decode_intent('{"action":"stunt","motion":"swim","confidence":0.9}').action == "stunt"


def test_movement_interrupt_stop_clamps_and_pause():
    director = WorldDirector(Effective.world(), 0)
    director.command("move", "right", manual=True, now=0)
    midpoint = view(director.snapshot(.5))["position"]
    assert 0 < midpoint[0] < .9
    director.command("move", "stop", manual=True, now=.5)
    assert view(director.snapshot(100))["position"] == midpoint
    for moment in range(1, 12):
        director.command("move", "right", manual=True, now=moment * 5)
    assert view(director.snapshot(100))["position"][0] == 2.5
    director.command("move", "window", manual=True, now=100)
    director.pause(True, 101)
    frozen = director.snapshot(999)
    with pytest.raises(ValueError, match="暂停"):
        director.command("camera", "left", manual=True, now=102)
    director.pause(False, 121)
    assert view(director.snapshot(121)) == view(frozen)
    assert view(director.snapshot(140))["position"] == [0, -1.35]


def test_camera_interrupt_bounds_and_cooldown():
    director = WorldDirector(Effective.world(), 0)
    director.command("camera", "right", now=0)
    with pytest.raises(ValueError, match="稍后"):
        director.command("weather", "rain", now=1)
    director.command("weather", "rain", now=2)
    midway = view(director.snapshot(.6))["camera"]
    assert 0 < midway[0] < 26
    director.command("camera", "turn_left", manual=True, now=.6)
    assert view(director.snapshot(.6))["camera"] == midway
    for moment in range(1, 12):
        director.command("camera", "closer", manual=True, now=moment * 5)
    assert view(director.snapshot(100))["camera"][2] == 9.4
    director.configure({**Effective.world(), "chat_control": False}, 101)
    with pytest.raises(ValueError, match="关闭"):
        director.command("weather", "snow", now=102)
    director.command("weather", "snow", manual=True, now=102)
    assert director.weather == "snow"


def test_native_pause_clock_matches_server(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: clock[0])
    director = WorldDirector(Effective.world(), 10)
    director.command("move", "window", manual=True, now=10)
    state = StageState()
    state.apply({"type": "hello", "world": director.snapshot(10)})
    clock[0] = 11
    state.apply({"type": "state", "paused": True})
    expected = view(director.snapshot(11))
    assert view(state.snapshot()["world"]) == expected
    clock[0] = 50
    state.apply({"type": "state", "paused": False})
    assert view(state.snapshot()["world"]) == expected
    assert state.world_at == 50


def test_swim_and_overlay_pose_freeze_together(qt):
    state = StageState()
    snapshot = state.snapshot()
    snapshot.update(paused=True, paused_at=5, animation_time=5, reaction_until=8,
                    reaction={"motion": "handstand", "started": 3, "duration": 5, "effect": "sparkle"},
                    like_goal={"active": {**STUNTS["swim"], "motion": "swim", "elapsed": 5, "id": "paused-swim"}})
    scene = Scene(720, 1280)
    assert scene.render(snapshot, 5, 100) == scene.render(snapshot, 99, 200)


def test_cached_panels_refresh_statistics_chat_and_settings(qt):
    state = StageState()
    snapshot = state.snapshot()
    snapshot["animation_time"] = 0
    scene = Scene(1280, 720)
    first = scene.render(snapshot, 0, 0)
    snapshot["stats"]["tokens"] = 987654
    snapshot["events"].append({"kind": "chat", "text": "去窗边", "userName": "观众甲"})
    second = scene.render(snapshot, 0, 0)
    assert first.copy(*scene.layout.tokens) != second.copy(*scene.layout.tokens)
    assert first.copy(*scene.layout.messages) != second.copy(*scene.layout.messages)
    snapshot["config"]["stage"]["show_tokens"] = False
    third = scene.render(snapshot, 0, 0)
    assert second != third


def test_scene_websocket_commands_reconnect_and_unrelated_config():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as connection:
            hello = connection.receive_json()
            assert hello["world"]["scene"] == "room"
            connection.receive_json()
            connection.send_json({"cmd": "world", "action": "weather", "value": "snow"})
            assert connection.receive_json()["world"]["weather"] == "snow"
            connection.send_json({"cmd": "world", "action": "weather", "value": "bad"})
            assert connection.receive_json()["type"] == "error"
        client.post("/api/config", json={"stage": {"show_tokens": False}})
        with client.websocket_connect("/ws") as connection:
            assert connection.receive_json()["world"]["weather"] == "snow"
        client.post("/api/live/pause")
        with client.websocket_connect("/ws") as connection:
            connection.receive_json()
            connection.receive_json()
            connection.send_json({"cmd": "world", "action": "move", "value": "left"})
            assert "暂停" in connection.receive_json()["error"]


def test_chat_routes_scene_without_speech_and_honors_interaction_switch(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.llm.classify_intent = AsyncMock(return_value=Intent("world", motion="weather:snow", source="ai"))
        brain._speak = AsyncMock()
        broadcast = AsyncMock()
        monkeypatch.setattr(scheduler.hub, "broadcast", broadcast)
        assert await brain._chat_intent(Event(kind="chat", user_name="观众", text="下雪吧"))
        assert brain.world.weather == "snow"
        assert broadcast.call_args_list[0].args[0]["type"] == "world"
        brain._speak.assert_not_called()
        store.update({"interact": {"enabled": False}})
        brain.llm.classify_intent.return_value = Intent("world", motion="scene:terrace")
        await brain._chat_intent(Event(kind="chat", text="去露台"))
        assert brain.world.scene == "room"
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [{"scene": "bad"}, {"weather": "bad"}, {"camera": "bad"},
                                  {"cooldown": -1}, {"cooldown": 121}, {"enabled": "yes"}])
def test_scene_settings_are_validated(patch):
    with pytest.raises(ValueError):
        store.update({"world": patch})


def test_perspective_geometry_and_swim_path_stay_bounded():
    camera = Projector(656, 438, CAMERA_POSES["front"])
    assert camera.project((2, 0, 1))[0] > camera.project((2, 0, -1))[0]
    assert len(build_room("clear")) > 250 and len(build_terrace("snow")) > 250
    bounds = (32, 292, 656, 378)
    assert STUNTS["swim"]["duration"] > 10
    for index in range(321):
        horizontal, vertical, size, tilt = swim_path(bounds, index / 20)
        radius = size * .57
        assert bounds[0] <= horizontal - radius and horizontal + radius <= bounds[0] + bounds[2]
        assert bounds[1] <= vertical - radius and vertical + radius <= bounds[1] + bounds[3]
        assert math.isfinite(tilt)
    reaction = {"motion": "swim", "duration": 16, "started": 0}
    first, second = joint_pose(reaction, 2, 2), joint_pose(reaction, 2.5, 2.5)
    assert first["tail"] != second["tail"] and first["upper_left"] != second["upper_left"]


@pytest.mark.parametrize("camera_name", ["front", "left", "right", "overhead"])
def test_rotating_camera_keeps_floor_above_foundation(qt, camera_name):
    snapshot = WorldDirector({**Effective.world(), "camera": camera_name}, 0).snapshot(0)
    camera = CAMERA_POSES[camera_name]
    renderer = WorldRenderer()
    renderer._layers(624, 438, snapshot, camera, 12)
    projection = Projector(624, 438, camera)
    for horizontal, depth in ((-2.25, 1.2), (2.25, .3), (-1.25, 2.1)):
        point = projection.project((horizontal, .036, depth))
        color = renderer.floor.pixelColor(round(point[0]), round(point[1]))
        assert color.alpha() > 240 and color.red() > 120


@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
@pytest.mark.parametrize("environment", list(SCENES))
def test_weather_world_clothes_and_ordinary_interactions_render(qt, size, environment):
    state = StageState()
    state.config["world"] = {**Effective.world(), "scene": environment}
    state.config["stage"] = {"scale": .64, "show_tokens": True}
    state.outfit = "raincoat"
    state.costume = "cool"
    scene = Scene(*size)
    images = []
    director = WorldDirector(state.config["world"], 0)
    for weather in WEATHERS:
        director.command("weather", weather, manual=True, now=0)
        state.world = director.snapshot(0)
        state.world_at = 0
        sample = state.snapshot()
        sample["animation_time"] = 4
        sample["reaction"] = {"motion": "handstand", "started": 0, "duration": 5}
        sample["reaction_until"] = 5
        images.append(scene.render(sample, 4, 2))
    assert all(not image.isNull() for image in images)
    assert all(images[0] != image for image in images[1:])
    sample["paused"] = True
    sample["paused_at"] = 2
    assert scene.render(sample, 4, 2) == scene.render(sample, 99, 99)
