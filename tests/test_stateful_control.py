from __future__ import annotations

import asyncio
import json
import math
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, playlist
from app.brain import scheduler
from app.brain.capabilities import available_commands, status_line
from app.brain.intent import Intent, decode_intent, local_intent
from app.events import Event
from app.native.actions import ACTIONS
from app.native.state import StageState
from app.native.world_scene import Projector, WorldRenderer
from app.performance import PerformanceState
from app.settings_store import Effective, store
from app.world import WorldDirector, view
from app.world_navigation import walkable
from tests.test_runtime import isolated_runtime


@pytest.mark.parametrize("text,action,motion", [
    ("切换场景", "world", "scene:next"), ("换个场景", "world", "scene:next"),
    ("小鱼换个地方吧", "world", "scene:next"), ("回家", "world", "activity:enter"),
    ("出门", "world", "activity:exit"), ("去云上天镜", "world", "scene:cloud"),
    ("下车", "world", "activity:dismount"), ("停车", "world", "activity:park"),
    ("别开了", "world", "activity:park"), ("别睡了", "world", "activity:wake"),
    ("散散步", "world", "activity:walk"), ("你现在在睡觉吗", "status", ""),
    ("睡觉", "world", "activity:sleep"), ("你正在做什么呢", "status", ""),
    ("人物状态", "status", ""), ("你在干嘛", "status", ""), ("你在哪里", "status", ""),
    ("你能做什么", "help", ""), ("停止动作", "control", "cancel"),
    ("停一下", "stop", ""), ("别跳了", "control", "cancel"),
    ("再来一次", "control", "repeat"), ("重复刚才的动作", "control", "repeat"),
    ("骑自行车", "stunt", "bicycle"), ("荡蛛丝", "stunt", "web_swing"),
])
def test_stateful_command_aliases(text, action, motion):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == (action, motion)
    assert decode_intent(json.dumps({"action": action, "motion": motion, "confidence": 1})).motion == motion


@pytest.mark.parametrize("text", ["不要回家", "如果出门呢", "他说下车", "今天换个场景真好看", "不要跳舞"])
def test_context_commands_do_not_execute_discussion(text):
    assert local_intent(text).action == "chat"


def test_scene_cycle_and_home_are_repeatable():
    world = WorldDirector({**Effective.world(), "cooldown": 0}, 0)
    for expected in ("cloud", "terrace", "room", "cloud"):
        label = world.command("scene", "next", now=0)
        assert world.scene == expected
        assert label == world.describe(0)["scene_label"]
    world.command("activity", "enter", now=0)
    assert world.scene == "room"
    world.command("activity", "exit", now=0)
    assert world.scene == "cloud"


@pytest.mark.parametrize("after", [.1, 6, 19])
def test_parking_and_dismount_preserve_car_and_heading_during_cooldown(after):
    world = WorldDirector({**Effective.world(), "cooldown": 30}, 0)
    world.command("activity", "drive", now=0)
    before = view(world.snapshot(after))
    world.command("activity", "park", now=after)
    stopped = world.describe(after)
    assert stopped["activity"] == "parked" and stopped["in_vehicle"]
    assert not stopped["moving"]
    assert view(world.snapshot(after))["heading"] == before["heading"]
    world.command("activity", "dismount", now=after)
    assert not world.describe(after)["in_vehicle"]
    assert world.parked_vehicle["position"] == before["position"]
    assert world.parked_vehicle["heading"] == before["heading"]
    final = view(world.snapshot(after + 4))
    assert final["activity"] == "idle" and walkable("cloud", final["position"])
    assert .5 < math.dist(final["position"], before["position"]) < 1.5
    with pytest.raises(ValueError, match="没有在车上"):
        world.command("activity", "dismount", now=after + 4)


def test_sleep_travel_arrival_and_pause_are_distinct():
    world = WorldDirector(Effective.world(), 0)
    world.command("activity", "sleep", now=0)
    moving = world.describe(.1)
    assert moving["activity"] == "travel"
    assert moving["on_arrival"] == "躺在沙发上睡觉"
    world.pause(True, .1)
    assert world.describe(100) == moving | {"paused": True}
    world.pause(False, 100)
    assert world.describe(100) == moving
    assert world.describe(200)["activity"] == "sleep"


def test_performance_priority_expiry_pause_and_reconnect(monkeypatch):
    performance = PerformanceState()
    config = {"interact": {"enabled": True, "intent_routing": True}}
    performance.observe({"type": "reaction", "motion": "dance", "manual": True}, config, 0)
    performance.observe({"type": "event", "event": {"kind": "gift"}}, config, .1)
    assert performance.snapshot(.1)["motion"] == "dance"
    performance.observe({"type": "state", "paused": True}, config, 1)
    frozen = performance.snapshot(100)
    assert frozen["elapsed"] == 1
    clock = [100.0]
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: clock[0])
    state = StageState()
    state.apply({"type": "hello", "paused": True, "performance": frozen})
    clock[0] = 110
    state.apply({"type": "config_changed", "config": config, "performance": frozen})
    assert state.reaction["started"] == 99
    performance.observe({"type": "state", "paused": False}, config, 110)
    state.apply({"type": "state", "paused": False, "performance": performance.snapshot(110)})
    assert state.reaction_until - 110 == pytest.approx(frozen["remaining"])
    assert performance.snapshot(120) is None
    performance.observe({"type": "reaction", "motion": "heart"}, config, 120)
    performance.observe({"type": "reaction_clear"}, config, 120)
    assert performance.snapshot(120) is None
    performance.observe({"type": "reaction", "motion": "heart"}, config, 120)
    assert performance.snapshot(120)["motion"] == "heart"


def test_local_execution_repeat_cancel_and_failed_history(monkeypatch):
    async def scenario():
        store.update({"world": {"cooldown": 0}})
        brain = scheduler.Brain()
        speak = AsyncMock()
        monkeypatch.setattr(brain, "_speak", speak)
        classifier = AsyncMock(side_effect=AssertionError("明确命令不应调用 AI"))
        monkeypatch.setattr(brain.llm, "classify_intent", classifier)
        await brain._chat_intent(Event(kind="chat", text="换个场景"))
        assert brain.world.scene == "cloud"
        await brain._chat_intent(Event(kind="chat", text="再来一次"))
        assert brain.world.scene == "terrace"
        successful = brain.last_interaction
        await brain._chat_intent(Event(kind="chat", text="下车"))
        assert brain.last_interaction == successful and not brain.last_operation["ok"]
        await brain._chat_intent(Event(kind="chat", text="开车"))
        track = playlist.make_track({"title": "测试歌曲", "url": "private-media-url"})
        playlist.queue.add(track)
        playlist.queue.next_track()
        brain.likes.total = 250
        brain.likes.preview("swim")
        brain.likes.tick()
        await scheduler.hub.broadcast({"type": "reaction", "motion": "dance"})
        await brain._chat_intent(Event(kind="chat", text="停止动作"))
        assert brain.world.describe()["activity"] == "parked"
        assert not brain.likes.active and not brain.likes.pending and brain.likes.total == 250
        assert scheduler.hub.performance.snapshot() is None
        assert playlist.queue.current == track and playlist.queue.playing
        await brain._chat_intent(Event(kind="chat", text="你在干嘛"))
        assert "仍坐在车内" in speak.call_args.args[0]
        await brain._chat_intent(Event(kind="chat", text="你能做什么"))
        assert "下车" in speak.call_args.args[0]
        classifier.assert_not_called()
    asyncio.run(scenario())


def test_interrupt_bypasses_voice_queue_and_cancels_stale_ai_request(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.world.command("activity", "drive", manual=True)
        received, release = asyncio.Event(), asyncio.Event()
        async def delayed(text):
            received.set()
            await release.wait()
            return Intent("world", motion="activity:drive", source="ai")
        monkeypatch.setattr(brain.llm, "classify_intent", delayed)
        pending = asyncio.create_task(brain._chat_intent(Event(kind="chat", text="再带我去外面兜兜风")))
        await received.wait()
        brain.speaker.busy = True
        event = Event(kind="chat", text="下车")
        assert await brain._publish_event(event)
        assert event.answered and not brain.world.describe()["in_vehicle"]
        release.set()
        await pending
        assert not brain.world.describe()["in_vehicle"]
    asyncio.run(scenario())


def test_context_tracks_real_behaviors_and_filters_capabilities():
    brain = scheduler.Brain()
    brain.world.command("activity", "sleep", now=0)
    assert "前往目的地" in status_line(brain.runtime_context(.1))
    assert "躺在沙发上睡觉" in status_line(brain.runtime_context(30))
    assert not brain._ambient_available()
    brain.likes.preview("swim")
    brain.likes.tick(30)
    context = brain.runtime_context(31)
    assert context["stunt"]["remaining"] == 15
    assert context["world"]["activity"] == "sleep"
    store.update({"world": {"chat_control": False}, "stage": {"avatar_mode": "image", "video_enabled": False}})
    commands = available_commands()
    assert "world" not in commands and "video" not in commands
    assert "next_outfit" not in commands["costume"] and "cool" in commands["costume"]
    store.update({"interact": {"enabled": False}, "music": {"enabled": False}})
    assert set(available_commands()) == {"status", "help"}


def test_cancel_respects_scene_permission_and_reports_remaining_behavior():
    async def scenario():
        store.update({"world": {"chat_control": False}})
        brain = scheduler.Brain()
        brain.world.command("activity", "sleep", manual=True, now=time.monotonic() - 40)
        await brain._chat_intent(Event(kind="chat", text="停止动作"))
        assert brain.world.describe()["activity"] == "sleep"
        assert "原场景行为继续" in brain.last_operation["result"]
    asyncio.run(scenario())


def test_active_manual_action_is_not_falsely_acknowledged(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        speak = AsyncMock()
        monkeypatch.setattr(brain, "_speak", speak)
        await scheduler.hub.broadcast({"type": "reaction", "motion": "dance", "manual": True})
        await brain._chat_intent(Event(kind="chat", text="比心"))
        assert not brain.last_operation["ok"] and brain.last_interaction is None
        assert scheduler.hub.performance.snapshot()["motion"] == "dance"
        assert "控制台指定" in speak.call_args.args[0]
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["system", "user"])
def test_classifier_and_chat_receive_fresh_context_without_secrets(monkeypatch, mode):
    async def scenario():
        brain = scheduler.Brain()
        model = brain.llm
        cfg = {"base_url": "https://model.invalid/v1", "api_key": "secret-only-in-header", "model": "test",
               "prompt_mode": mode, "temperature": .5, "max_tokens": 100}
        monkeypatch.setattr(model, "_cfg", lambda: cfg)
        bodies = []
        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            if body["stream"]:
                chunk = json.dumps({"choices": [{"delta": {"content": "我正躺在沙发上"}}]}, ensure_ascii=False)
                return httpx.Response(200, text="data: " + chunk + "\n\ndata: [DONE]\n\n")
            result = {"action": "world", "motion": "activity:wake", "confidence": .99}
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(model, "_ensure_client", lambda: client)
            brain.world.command("activity", "sleep", now=time.monotonic() - 40)
            assert (await model.classify_intent("休息够了起来活动活动")).motion == "activity:wake"
            await scheduler.hub.broadcast({"type": "reaction", "motion": "heart"})
            assert "我正躺在沙发上" == "".join([part async for part in model.stream("现在感觉怎样")])
            for body in bodies:
                content = body["messages"][0]["content"]
                assert "躺在沙发上睡觉" in content and "activity:dismount" in content
                assert "secret-only-in-header" not in content and "private-media-url" not in content
                assert body["messages"][0]["role"] == mode
            assert '"motion":"heart"' in bodies[-1]["messages"][0]["content"]
            assert '"reaction":null' in bodies[0]["messages"][0]["content"]
    asyncio.run(scenario())


def test_parked_car_geometry_is_cached_and_bounded():
    world = WorldDirector(Effective.world(), 0)
    world.command("activity", "drive", manual=True, now=0)
    renderer = WorldRenderer()
    baseline = len(renderer._prepare(world.snapshot(2))[0])
    world.command("activity", "dismount", now=2)
    snapshot = world.snapshot(4)
    vertices = renderer._prepare(snapshot)[0]
    assert len(vertices) > baseline
    assert renderer._prepare(snapshot)[0] is vertices
    for offset in range(30):
        snapshot["parked_vehicle"]["heading"] = offset / 30
        renderer._prepare(snapshot)
    assert len(renderer.meshes) <= 24


def test_context_endpoint_and_reconnect_restore_performance():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as connection:
            connection.receive_json()
            connection.receive_json()
            connection.send_json({"cmd": "reaction", "motion": "dance"})
            assert connection.receive_json()["performance"]["motion"] == "dance"
        context = client.get("/api/ai/context").json()["context"]
        assert context["reaction"]["motion"] == "dance"
        with client.websocket_connect("/ws") as connection:
            hello = connection.receive_json()
            assert hello["performance"]["motion"] == "dance"
            assert 0 < hello["performance"]["remaining"] <= ACTIONS["dance"]["duration"]
        main.app.state.brain.last_interaction = Intent("motion", motion="dance")
        client.post("/api/session/boot")
        assert main.app.state.brain.last_interaction is None
        assert client.get("/api/ai/context").json()["context"]["reaction"] is None


def test_stationary_car_projection_reuses_cache_but_camera_updates():
    world = WorldDirector(Effective.world(), 0)
    world.command("activity", "drive", manual=True, now=0)
    world.command("activity", "park", now=4)
    current = view(world.snapshot(6))
    renderer = WorldRenderer()
    projection = Projector(720, 600, current["camera"], current["focus"])
    faces = renderer._vehicle_faces(projection, current, 0)
    assert faces and renderer._vehicle_faces(projection, current, 0) is faces
    world.command("camera", "right", manual=True, now=6)
    moved = view(world.snapshot(8))
    projection = Projector(720, 600, moved["camera"], moved["focus"])
    assert renderer._vehicle_faces(projection, moved, 0) != faces
