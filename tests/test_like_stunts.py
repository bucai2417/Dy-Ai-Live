from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from PySide6.QtGui import QImage, QPainter

from app import main
from app.brain import scheduler
from app.events import Event
from app.ingest import douyin_cdp
from app.ingest.bridge import normalize
from app.like_goal import LikeGoal, STUNTS
from app.native.scene import Scene
from app.native.state import StageState
from app.native.stunts import draw_stunt, stunt_age
from app.settings_store import store
from tests.test_douyin_parse import fld_int, fld_msg, user_struct
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def like(count, total=None, source="test"):
    return Event(kind="like", gift_count=count, like_count=count, like_total=total, like_source=source)


def test_exact_threshold_repeated_counts_and_rotation():
    goal = LikeGoal()
    for _ in range(9):
        goal.add(like(10))
    assert not goal.pending and goal.snapshot()["remaining"] == 10
    goal.add(like(10))
    assert goal.total == 100 and goal.next_target == 200
    assert goal.tick(10) and goal.active["motion"] == "swim"
    goal.add(like(200))
    assert [item["motion"] for item in goal.pending] == ["bicycle", "web_swing"]
    assert not goal.tick(25.9)
    assert goal.tick(26) and goal.active is None
    assert not goal.tick(30.9)
    assert goal.tick(31) and goal.active["motion"] == "bicycle"


def test_large_batch_merges_extra_milestones_without_losing_count():
    goal = LikeGoal()
    goal.add(like(1055))
    assert goal.total == 1055 and goal.completed == 10
    assert len(goal.pending) == 3
    assert sum(item["milestones"] for item in goal.pending) == 10
    assert goal.snapshot()["remaining"] == 45


def test_cumulative_baseline_duplicates_out_of_order_and_source_handoff():
    goal = LikeGoal()
    goal.add(like(0, 5000, "douyin:room"))
    assert goal.total == 0
    goal.add(like(10, 5010, "douyin:room"))
    goal.add(like(10, 5010, "douyin:room"))
    goal.add(like(20, 5005, "douyin:room"))
    assert goal.total == 10
    goal.add(like(7, 5020, "douyin:room"))
    assert goal.total == 20
    goal.add(like(3, 99999, "new-source"))
    assert goal.total == 23


def test_pause_freezes_active_and_keeps_pending_progress():
    goal = LikeGoal()
    goal.add(like(250))
    goal.tick(10)
    goal.pause(True, 12)
    goal.add(like(35))
    assert not goal.tick(99)
    assert goal.snapshot(99)["active"]["elapsed"] == 2
    assert goal.total == 285 and goal.snapshot()["remaining"] == 50
    assert len(goal.pending) == 1
    goal.pause(False, 102)
    assert goal.snapshot(104)["active"]["elapsed"] == 4
    assert goal.tick(116)


def test_config_change_restarts_target_and_disable_clears_automatic():
    goal = LikeGoal()
    goal.add(like(270))
    goal.tick(0)
    goal.configure({"step": 50, "mode": "rocket"})
    assert goal.next_target == 320 and not goal.pending
    goal.configure({"enabled": False, "step": 50})
    assert goal.active is None
    goal.add(like(500))
    assert not goal.pending and goal.next_target - goal.total == 50
    assert goal.preview("rocket")
    goal.configure({"enabled": False, "step": 50})
    assert goal.tick(50) and goal.active["manual"]


def test_random_does_not_repeat_and_fixed_mode_is_respected():
    goal = LikeGoal()
    goal.configure({"step": 100, "mode": "random"})
    choices = [goal._pick() for _ in range(30)]
    assert all(before != after for before, after in zip(choices, choices[1:]))
    goal.configure({"step": 100, "mode": "web_swing"})
    assert all(goal._pick() == "web_swing" for _ in range(5))


def test_protocol_counts_field_two_and_total_field_three(monkeypatch):
    payload = fld_int(2, 17) + fld_int(3, 50000) + fld_msg(5, fld_int(1, 246801) + user_struct("点赞观众"))
    ingest = douyin_cdp.DouyinCDPIngestor("123")
    assert ingest._like_values(payload) == (17, 50000)
    assert ingest._like_count(payload) == 17
    emitted = []
    monkeypatch.setattr(douyin_cdp, "gunzip_any", lambda value: value)
    monkeypatch.setattr(douyin_cdp, "iter_envelopes", lambda value: [("WebcastLikeMessage", payload)])
    monkeypatch.setattr(ingest, "_emit_async", emitted.append)
    ingest._handle_frame(b"test")
    assert emitted[0].like_count == 17 and emitted[0].like_total == 50000
    assert emitted[0].user_name == "点赞观众"
    assert emitted[0].user_id == "246801"
    from app.ingest.protobuf_lite import collect_users
    assert collect_users(payload)[0]["id"] == "246801"
    payload = fld_int(2, 3) + fld_msg(5, fld_int(1, 135790) + user_struct("无头像鱼友", ""))
    ingest._handle_frame(b"without-avatar")
    assert emitted[-1].user_id == "135790" and emitted[-1].user_name == "无头像鱼友"


def test_dom_likes_keep_full_delta_without_invented_users(monkeypatch):
    ingest = douyin_cdp.DouyinCDPIngestor("123")
    emitted = []
    monkeypatch.setattr(ingest, "_emit_async", emitted.append)
    ingest._likes_changed(0, [])
    ingest._likes_changed(157, [])
    assert [event.like_count for event in emitted] == [0, 157]
    assert emitted[-1].user_name == "直播间观众"
    ingest._ws_like_seen = True
    ingest._likes_changed(200, [])
    assert len(emitted) == 2


def test_bridge_likes_support_delta_and_cumulative():
    event = normalize({"Type": 4, "Data": {"Count": 15, "Total": 10000}})
    assert event.like_count == 15 and event.like_total == 10000
    event = normalize({"type": "like", "count": 0, "totalLikeCount": 0})
    assert event.like_count == 0
    event = normalize({"type": "like", "total": 10000})
    assert event.like_count == 0 and event.like_total == 10000


def test_publish_likes_does_not_wait_for_ai():
    async def scenario():
        brain = scheduler.Brain()
        await brain._publish_event(like(100))
        assert brain.likes.pending
        assert brain.likes.tick()
        assert brain.likes.active["motion"] == "swim"
        await brain.set_paused(True)
        elapsed = brain.likes.snapshot()["active"]["elapsed"]
        assert brain.likes.snapshot()["active"]["elapsed"] == elapsed
        await brain.set_paused(False)
        assert brain.likes.active

    asyncio.run(scenario())


def test_websocket_reconnect_contains_current_progress_and_active_time():
    with TestClient(main.app) as client:
        goal = main.app.state.brain.likes
        goal.add(like(100))
        goal.tick(time.monotonic() - 1)
        with client.websocket_connect("/ws") as connection:
            hello = connection.receive_json()
            assert hello["likeGoal"]["total"] == 100
            assert hello["likeGoal"]["active"]["elapsed"] >= 1
        client.post("/api/live/pause")
        with client.websocket_connect("/ws") as connection:
            assert connection.receive_json()["paused"]
            connection.receive_json()
            connection.send_json({"cmd": "stunt", "motion": "rocket"})
            assert "暂停" in connection.receive_json()["error"]
        client.post("/api/live/resume")
        client.post("/api/session/boot")
        assert main.app.state.brain.likes.total == 100
        assert main.app.state.brain.likes.active is None


def test_stage_pause_resume_and_reconnect_timeline(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: clock[0])
    state = StageState()
    active = {**STUNTS["web_swing"], "motion": "web_swing", "elapsed": 2}
    state.apply({"type": "hello", "likeGoal": {"active": active}})
    assert stunt_age(state.snapshot(), 11)[1] == 3
    clock[0] = 11
    state.apply({"type": "state", "paused": True})
    assert stunt_age(state.snapshot(), 100)[1] == 3
    clock[0] = 100
    state.apply({"type": "state", "paused": False})
    assert stunt_age(state.snapshot(), 101)[1] == 4


@pytest.mark.parametrize("motion", list(STUNTS))
@pytest.mark.parametrize("width,height", [(720, 1280), (1280, 720)])
def test_stunts_animate_in_bounds_and_keep_ui(qt, motion, width, height, tmp_path):
    scene = Scene(width, height)
    state = StageState()
    state.config["stage"] = {"show_tokens": True}
    state.costume = "cool"
    bounds = (32, 292, 656, 378) if height > width else (320, 122, 624, 440)
    active = {**STUNTS[motion], "motion": motion, "elapsed": 0, "id": motion}
    frames = []
    for age in (1.2, 2.4, 3.6, 5.5):
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        draw_stunt(painter, scene, state.snapshot(), bounds, active, age, 10)
        painter.end()
        frames.append(image)
        assert image.pixelColor(0, 0).alpha() == 0
        assert image.pixelColor(int(bounds[0]), int(bounds[1] + bounds[3] + 5)).alpha() == 0
        if motion == "rocket":
            empty = QImage(bounds[2], 3, QImage.Format.Format_ARGB32_Premultiplied)
            empty.fill(0)
            assert image.copy(bounds[0], bounds[1], bounds[2], 3) == empty
    assert frames[0] != frames[1]
    state.like_goal = {"active": {**active, "elapsed": 2.4}, "total": 100, "remaining": 100}
    state.like_goal_at = 10
    image = scene.render(state.snapshot(), 1, 10)
    assert not image.isNull()


@pytest.mark.parametrize("patch", [{"step": 0}, {"step": 2.5}, {"step": 1000001},
                                   {"mode": "bad"}, {"cooldown": -1}, {"enabled": "true"}])
def test_goal_settings_reject_invalid_values(patch):
    with pytest.raises(ValueError):
        store.update({"like_goal": patch})
