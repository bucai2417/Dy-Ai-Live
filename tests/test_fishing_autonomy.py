from __future__ import annotations

import asyncio
import copy
import json
import random

import pytest

from app.brain import scheduler
from app.brain.capabilities import status_line
from app.brain.intent import decode_intent, local_intent
from app.events import Event
from app.fishing import FishingGame
from app.fishing_autonomy import SPOTS
from app.native.avatar.rig import joint_pose
from app.native.fishing_scene import fishing_view
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS, Effective, store
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def quiet_game(**options):
    game = FishingGame({**Effective.fishing(), "dedicated": True, "bite_min": 300, "bite_max": 300,
                        "auto_relocate": False, **options}, random.Random(17))
    game.start("海风会客厅", 0)
    return game


@pytest.mark.parametrize("text,motion", [("换个钓点", "relocate"), ("换个地方钓鱼", "relocate"),
    ("去芦苇浅湾钓鱼", "spot_reeds"), ("请去星礁海岸吧", "spot_reef"), ("回云汐钓台", "spot_cloud")])
def test_location_commands_and_ai_decode(text, motion):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == ("fishing", motion)
    assert decode_intent(json.dumps({"action": "fishing", "motion": motion, "confidence": 1})).motion == motion


@pytest.mark.parametrize("text", ["不要换个钓点", "我昨天去芦苇浅湾", "如果去星礁海岸", "换个场景"])
def test_location_does_not_capture_unrelated_text(text):
    assert local_intent(text).action != "fishing"


def test_ambient_keeps_deadline_progress_and_reacts_to_real_audience():
    game = quiet_game()
    deadline = game.deadline
    assert not game.auto_tick(24)
    assert game.auto_tick(25)
    assert game.ambient and game.progress == 0 and game.deadline == deadline
    assert not game.contributors and not game.fish
    assert game.viewer_activity(26) and not game.ambient
    assert not game.auto_tick(40)
    game.auto_tick(75)
    assert game.ambient
    game.tick(300)
    assert game.phase == "hooked" and not game.ambient
    assert not game.auto_tick(301)


def test_ambient_varies_and_never_repeats_immediately():
    game = quiet_game(auto_voice=False)
    kinds = []
    for index in range(1, 5):
        game.auto_tick(index * 50 - 10)
        game.auto_tick(index * 50)
        kinds.append(game.last_ambient_kind)
    assert all(first != second for first, second in zip(kinds, kinds[1:]))


def test_dry_or_failed_rounds_relocate_only_when_safe_and_reset():
    game = quiet_game(auto_relocate=True, auto_actions=False, auto_voice=False)
    assert not game.auto_tick(179)
    assert game.auto_tick(180) and game.phase == "relocating"
    assert game.relocation["automatic"] and "180秒" in game.relocation["reason"]
    previous = game.round_id
    game.tick(190)
    assert game.spot == "reeds" and game.phase == "waiting" and game.round_id != previous
    assert game.return_scene == "海风会客厅" and not game.contributors
    assert not game.auto_tick(200)
    for index in range(3):
        game._phase("escaped", 201 + index * 2, 1)
        game._phase("waiting", 202 + index * 2, 200)
    assert game.failed_rounds == 3
    assert game.auto_tick(207) and game.relocation["to"] == "reef"
    game.tick(217)
    assert game.failed_rounds == 0
    game._phase("caught", 220, 6)
    game._phase("waiting", 226, 300)
    assert game.last_success_at == 220
    assert not game.auto_tick(399)
    assert game.auto_tick(400)


@pytest.mark.parametrize("phase", ["hooked", "landing", "caught", "escaped", "net_cast", "net_caught", "diving", "interacting", "relocating"])
def test_auto_actions_do_not_interrupt_busy_phases(phase):
    game = quiet_game(auto_relocate=True)
    game.phase = phase
    game.deadline = 500
    assert not game.auto_tick(200)
    assert game.phase == phase and not game.ambient


def test_manual_relocate_round_reset_gift_queue_and_cancel():
    game = quiet_game()
    game.relocate("reef", 5)
    assert game.add_net_gift(1, 5, {"id": "gift", "name": "换点时送礼"}, "gift", now=6)
    assert game.phase == "relocating" and len(game.net_queue) == 1
    with pytest.raises(ValueError):
        game.start_dive(7)
    with pytest.raises(ValueError):
        game.interrupt("跳舞", 5, 7)
    game.tick(15)
    assert game.spot == "reef" and game.phase == "waiting"
    game.tick(15.1)
    assert game.phase == "net_cast" and game.net["user"]["id"] == "gift"
    game.cancel(20)
    assert not game.net_queue and not game.relocation and game.session_started is None


def test_pause_excludes_offline_time_and_preserves_transition():
    game = quiet_game(auto_relocate=True)
    game.pause(True, 20)
    assert not game.auto_tick(1000)
    game.pause(False, 1020)
    assert not game.auto_tick(1024)
    assert game.auto_tick(1025) and game.ambient
    game.relocate("reeds", 1040)
    game.pause(True, 1043)
    frozen = game.snapshot(1043)
    game.pause(False, 2043)
    assert game.snapshot(2043)["phase_elapsed"] == frozen["phase_elapsed"]
    game.tick(2050)
    assert game.spot == "reeds" and game.phase == "waiting"


def test_invitation_does_not_reset_by_own_waiting_speech_and_stops_on_chat(monkeypatch):
    async def scenario():
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: 100)
        store.update({"fishing": {"dedicated": True, "bite_min": 300, "bite_max": 300}})
        brain = scheduler.Brain()
        await brain.ensure_dedicated_fishing()
        brain.last_speak_at = scheduler.time.time()
        assert await brain.autonomous_tick(191)
        candidate = brain.live_voice_candidate()
        assert candidate and "云汐钓台" in candidate[0] and candidate[2][0] == "fishing_auto"
        assert candidate[1]()
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: 192)
        await brain._publish_event(Event(kind="chat", text="你好鱼友", user_id="viewer"))
        assert not candidate[1]() and not brain.fishing.ambient
        assert not await brain.autonomous_tick(200)
    asyncio.run(scenario())


def test_system_and_duplicate_likes_do_not_hide_silence(monkeypatch):
    async def scenario():
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: 100)
        brain = scheduler.Brain()
        await brain.fishing_command("enter_dedicated", manual=True)
        await brain._publish_event(Event(kind="system", text="连接保持"))
        await brain._publish_event(Event(kind="like", like_count=0, like_total=9999))
        assert brain.fishing.quiet_since == 100
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: 120)
        like = Event(kind="like", id="real-like", like_count=1, like_incremental=True)
        await brain._publish_event(like)
        assert brain.fishing.quiet_since == 120
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: 130)
        await brain._publish_event(like)
        assert brain.fishing.quiet_since == 120
    asyncio.run(scenario())


def test_world_walk_yields_to_audience_but_not_overwrite_new_command(monkeypatch):
    async def scenario():
        store.update({"idle": {"scene_interactions": False}})
        clock = [100]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        assert not await brain.autonomous_tick(189)
        assert await brain.autonomous_tick(190)
        assert brain.world.describe(191)["activity"] == "walk"
        clock[0] = 192
        brain.note_audience()
        await brain.autonomous_tick(192)
        assert not brain.world.describe(192)["moving"]
        assert not brain._auto_walk
        assert await brain.autonomous_tick(283)
        brain.world.command("activity", "sleep", manual=True, now=284)
        await brain.autonomous_tick(285)
        assert not brain._auto_walk and brain.world.activity == "sleep"
        assert not await brain.autonomous_tick(500)
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [{"quiet_seconds": 9}, {"dry_seconds": 59}, {"failed_limit": 0},
    {"failed_limit": 1.5}, {"auto_invite_seconds": 901}])
def test_autonomy_settings_validate(patch):
    before = store.get_all()
    with pytest.raises(ValueError):
        store.update({"fishing": patch})
    assert store.get_all() == before


def test_spot_preferences_change_encounters_without_excluding_catalog():
    game = quiet_game()
    kinds = ("silver", "shark", "koi")
    original = game.spot_weights(kinds)
    game.spot = "reeds"
    reeds = game.spot_weights(kinds)
    game.spot = "reef"
    reef = game.spot_weights(kinds)
    assert reeds[0] == original[0] * 2 and reeds[1] == original[1]
    assert reef[0] == original[0] and reef[1] == original[1] * 2
    assert all(weight > 0 for weight in reeds + reef)


def test_disabled_automatic_features_and_gift_priority():
    game = quiet_game(auto_actions=False, auto_voice=False, auto_relocate=False)
    assert not game.auto_tick(200)
    game.config.update(auto_actions=True, auto_relocate=True)
    game.add_net_gift(1, 3, {"name": "先送礼"}, "gift", now=201)
    assert game.phase == "net_cast"
    assert not game.auto_tick(202) and not game.relocation
    game.cancel(203)
    game.start("海风会客厅", 204)
    assert not game.auto_tick(205)
    game.config["dedicated"] = False
    assert not game.auto_tick(499)


def test_natural_failed_rounds_trigger_relocation_without_fabricated_catch():
    game = quiet_game(auto_relocate=True, bite_min=12, bite_max=12, fight_min=25, fight_max=25,
                      auto_actions=False, auto_voice=False, dry_seconds=3600)
    for now in range(1, 141):
        game.tick(now)
        if not game.active:
            game.start("海风会客厅", now)
        game.auto_tick(now)
    assert game.spot != "cloud" or game.phase == "relocating"
    assert game.last_success_at is None and not game.contributors


def test_world_auto_pause_does_not_consume_silence(monkeypatch):
    async def scenario():
        clock = [100]
        monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
        brain = scheduler.Brain()
        clock[0] = 120
        await brain.set_paused(True)
        clock[0] = 1120
        await brain.set_paused(False)
        assert not await brain.autonomous_tick(1189)
        assert await brain.autonomous_tick(1190)
        store.update({"idle": {"movement_enabled": False}})
        await brain.autonomous_tick(1191)
        assert not brain._auto_walk and not brain.world.describe(1191)["moving"]
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(1280, 720), (720, 1280)])
def test_locations_and_relocation_render_and_freeze(qt, size):
    game = quiet_game()
    state = StageState()
    state.config["stage"] = copy.deepcopy(DEFAULTS["stage"])
    state.config["stage"]["layout"] = "landscape" if size[0] > size[1] else "portrait"
    scene = Scene(*size)
    snapshot = state.snapshot()
    frames = []
    for spot in SPOTS:
        game.spot = spot
        snapshot.update(fishing=game.snapshot(1), fishing_at=0, animation_time=0)
        frames.append(scene.render(snapshot, 0, 0))
    assert all(not frame.isNull() for frame in frames)
    assert frames[0] != frames[1] and frames[1] != frames[2]
    game.relocate("reeds", 2)
    snapshot.update(fishing=game.snapshot(8), fishing_at=0)
    travel = scene.render(snapshot, 0, 0)
    assert travel != frames[-1]
    snapshot.update(paused=True, paused_at=0)
    assert scene.render(snapshot, 10, 100) == scene.render(snapshot, 20, 200)
    assert len(scene.fishing.backgrounds) <= 4


def test_ambient_snapshot_animation_expiry_and_distinct_poses():
    game = quiet_game()
    game.auto_tick(25)
    frozen = game.snapshot(25)
    state = {"fishing": frozen, "fishing_at": 0}
    assert fishing_view(state, 3)["ambient"]["elapsed"] == 3
    assert fishing_view(state, 20)["ambient"] is None
    assert game.ambient is not None
    state["paused"] = True
    assert fishing_view(state, 20)["ambient"]["elapsed"] == 0
    poses = [joint_pose({"motion": "fishing_" + kind, "started": 0, "duration": 8}, 2, 2)
             for kind in ("scan", "stretch", "shuffle", "inspect")]
    assert all(first != second for first, second in zip(poses, poses[1:]))
