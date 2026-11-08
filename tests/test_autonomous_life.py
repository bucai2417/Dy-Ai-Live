from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.brain import scheduler
from app.brain.capabilities import runtime_context
from app.settings_store import Effective, store
from app.world import LANDMARKS, view
from tests.test_runtime import isolated_runtime


@pytest.mark.parametrize("scene,visit,action", [("room", 0, "sleep"), ("room", 1, "window"),
    ("room", 2, "tea"), ("terrace", 0, "lookout"), ("terrace", 1, "sleep"), ("cloud", 0, "lookout")])
def test_arrival_interacts_then_returns_and_waits(monkeypatch, scene, visit, action):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        store.update({"world": {"scene": scene}, "idle": {"scene_switching": False}})
        brain = scheduler.Brain()
        brain._auto_visits[scene] = visit
        clock[0] = 190
        assert await brain.autonomous_tick()
        assert brain.world.describe()["activity"] == "travel"
        assert brain.world.activity == action
        clock[0] += brain.world.movement["duration"] + .1
        await brain.autonomous_tick()
        assert brain.world.describe()["activity"] == action
        assert brain._auto_walk["phase"] == "resting"
        place = "sofa" if action == "sleep" else action
        assert view(brain.world.snapshot())["position"] == list(LANDMARKS[scene][place])
        context = runtime_context(brain, scheduler.hub, scheduler.queue)
        assert context["automatic_movement"]["action"] == action
        assert context["automatic_movement"]["phase"] == "resting"
        clock[0] += brain._auto_walk["dwell"] + 1
        assert await brain.autonomous_tick()
        assert brain._auto_walk["phase"] == "returning"
        assert brain.world.activity == "idle"
        clock[0] += brain.world.movement["duration"] + .1
        await brain.autonomous_tick()
        assert brain._auto_walk is None
        assert view(brain.world.snapshot())["position"] == list(LANDMARKS[scene]["center"])
        clock[0] += 89
        assert not await brain.autonomous_tick()
        clock[0] += 2
        assert await brain.autonomous_tick()
        assert brain._auto_walk["action"] != action
    asyncio.run(scenario())


def test_self_sleep_yields_but_viewer_sleep_is_not_automatically_woken(monkeypatch):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        clock[0] = 190
        await brain.autonomous_tick()
        clock[0] += brain.world.movement["duration"] + 1
        brain.note_audience()
        assert await brain.autonomous_tick()
        assert not brain._auto_walk and brain.world.activity == "idle"
        brain.world.command("activity", "sleep", manual=True)
        clock[0] += 1000
        assert not await brain.autonomous_tick()
        assert brain.world.describe()["activity"] == "sleep"
    asyncio.run(scenario())


def test_pause_and_gift_do_not_consume_rest_time(monkeypatch):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        clock[0] = 190
        await brain.autonomous_tick()
        clock[0] += brain.world.movement["duration"] + 1
        await brain.autonomous_tick()
        brain.world.pause(True)
        age = view(brain.world.snapshot())["activity_age"]
        clock[0] += 1000
        assert not await brain.autonomous_tick()
        assert view(brain.world.snapshot())["activity_age"] == age
        brain.world.pause(False)
        await brain.autonomous_tick()
        assert brain.world.activity == "sleep"
        await brain.set_paused(True)
        clock[0] += 1000
        await brain.set_paused(False)
        await brain.autonomous_tick()
        assert brain.world.activity == "sleep"
        clock[0] += brain._auto_walk["dwell"]
        assert await brain.autonomous_tick()
        assert brain.world.activity == "idle"
    asyncio.run(scenario())


@pytest.mark.parametrize("scene,destination", [("room", "cloud"), ("cloud", "terrace"), ("terrace", "room")])
def test_autonomous_scene_change_walks_to_door_first(monkeypatch, scene, destination):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        store.update({"world": {"scene": scene}})
        brain = scheduler.Brain()
        clock[0] = 401
        assert await brain.autonomous_tick()
        assert brain.world.scene == scene
        assert brain._auto_walk["phase"] == "door"
        assert brain.world.movement["to"] == list(LANDMARKS[scene]["door"])
        clock[0] += brain.world.movement["duration"] + .1
        assert await brain.autonomous_tick()
        assert brain.world.scene == destination
        assert brain._auto_walk["phase"] == "visiting"
        assert brain.world.activity in ("sleep", "lookout")
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["disable", "manual", "audience"])
def test_pending_scene_change_can_be_cancelled(monkeypatch, change):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        clock[0] = 401
        await brain.autonomous_tick()
        clock[0] += .1
        if change == "disable":
            store.update({"idle": {"scene_switching": False}})
        elif change == "manual":
            brain.world.command("activity", "window", manual=True)
        else:
            brain.note_audience()
        clock[0] += 20
        await brain.autonomous_tick()
        assert not brain._auto_walk and brain.world.scene == "room"
        if change == "manual":
            assert brain.world.activity == "window"
    asyncio.run(scenario())


def test_auto_voice_uses_current_state_and_yields_to_music_and_audience(monkeypatch):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        clock[0] = 190
        await brain.autonomous_tick()
        candidate = brain.autonomous_voice_candidate()
        assert candidate and "沙发" in candidate[0] and candidate[1]()
        scheduler.queue.playing = True
        assert brain.autonomous_voice_candidate() is None
        scheduler.queue.playing = False
        brain.note_audience()
        assert not candidate[1]() and brain.autonomous_voice_candidate() is None
    asyncio.run(scenario())


def test_dedicated_fishing_never_leaves_for_autonomous_room_activity(monkeypatch):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        await brain.fishing_command("enter_dedicated", manual=True)
        clock[0] = 500
        await brain.autonomous_tick()
        assert brain.fishing.active and not brain._auto_walk
        assert brain.world.scene == "room"
    asyncio.run(scenario())


def test_fishing_autonomy_runs_after_interrupting_an_automatic_room_visit(monkeypatch):
    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        clock[0] = 190
        await brain.autonomous_tick()
        clock[0] = 200
        brain.note_audience()
        await brain.fishing_command("enter_dedicated", manual=True)
        assert brain.world.paused_at is not None and brain._auto_walk
        tick = Mock(return_value=True)
        sync = AsyncMock()
        monkeypatch.setattr(brain.fishing, "auto_tick", tick)
        monkeypatch.setattr(brain, "_sync_fishing", sync)
        clock[0] = 250
        assert await brain.autonomous_tick()
        tick.assert_called_once_with(250)
        sync.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [{"interaction_seconds": 7}, {"interaction_seconds": 61},
    {"scene_switch_seconds": 119}, {"scene_switching": "true"}, {"movement_voice": 1}])
def test_autonomous_life_settings_reject_invalid_values(patch):
    before = Effective.idle()
    with pytest.raises(ValueError):
        store.update({"idle": patch})
    assert Effective.idle() == before
