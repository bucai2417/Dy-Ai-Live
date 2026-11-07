from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.brain import scheduler
from app.brain.intent import decode_intent, local_intent, video_requested
from app.events import Event
from app.gifts import GiftBoard
from app.native.actions import ACTIONS, COSTUMES, pose
from app.native.scene import Scene
from app.native.state import StageState
from app.native.video import ClipDecoder, VideoController
from app.settings_store import store
from tests.test_renderer import qt, pump
from tests.test_runtime import isolated_runtime
from tools.sample_media import create_sample_video


def add(board, user, value, count=1, name=None):
    board.add({"userId": user, "userName": name or user, "giftValue": value, "giftCount": count})


def test_below_top_ten_can_accumulate_and_overtake():
    board = GiftBoard()
    for index in range(11):
        add(board, str(index), 110 - index)
    assert [row["userId"] for row in board.ranking(99)] == [str(index) for index in range(10)]
    add(board, "10", 5)
    rows = board.ranking(99)
    assert len(rows) == 10 and "10" in [row["userId"] for row in rows]
    assert "9" not in [row["userId"] for row in rows]
    add(board, "9", 100)
    assert board.ranking()[0]["userId"] == "9"


def test_gift_modes_and_ties_keep_consistent_identity():
    board = GiftBoard()
    add(board, "first", 100, count=1, name="同名用户")
    add(board, "second", 100, count=2, name="同名用户")
    add(board, "first", 0)
    assert [row["userId"] for row in board.ranking()] == ["first", "second"]
    add(board, "second", 0, count=8)
    assert board.ranking(by="count")[0]["userId"] == "second"
    assert len(board.records) == 2


def test_native_reconciles_authoritative_ranking_and_rank_mode():
    board = GiftBoard()
    for index in range(12):
        add(board, str(index), 100 - index, count=index + 1)
    state = StageState()
    state.apply({"type": "hello", "stats": {"giftRanking": board.snapshot()}})
    assert len(state.gift_senders) == 10 and "id:11" not in state.gift_senders
    add(board, "11", 200)
    state.apply({"type": "gift_ranking", "ranking": board.snapshot()})
    assert next(iter(state.gift_senders)) == "id:11"
    assert state.stats["ranking"][0]["userId"] == "11"
    assert "id:9" not in state.gift_senders
    state.apply({"type": "config_changed", "config": {"stage": {"ball_rank_by": "count", "ball_max": 99}}})
    assert len(state.gift_senders) == 10 and "id:10" in state.gift_senders
    state.apply({"type": "gift_ranking", "ranking": {"value": [], "count": []}})
    assert not state.gift_senders


def test_scene_state_triggers_without_replaying_on_hello():
    board = GiftBoard()
    add(board, "one", 100)
    add(board, "two", 50)
    state = StageState()
    state.apply({"type": "hello", "config": {"stage": {"scene_reactions": True}},
                 "stats": {"giftRanking": board.snapshot()}})
    assert not state.reaction
    add(board, "two", 100)
    state.apply({"type": "gift_ranking", "ranking": board.snapshot()})
    assert state.reaction["motion"] == "happy" and state.celebration_until > time.monotonic()
    state.apply({"type": "reset"})
    state.apply({"type": "music", "music": {"playing": True, "current": {"id": "track-one"}}})
    assert state.reaction["motion"] == "dance"


@pytest.mark.parametrize("text,action,motion", [
    ("倒立", "motion", "handstand"), ("转个圈吧", "motion", "spin"), ("变小", "motion", "mini"),
    ("换衣服", "costume", "next_outfit"), ("戴皇冠", "costume", "crown"), ("戴上墨镜吧", "costume", "cool"),
    ("恢复原装", "costume", "original"),
])
def test_new_interaction_commands(text, action, motion):
    result = local_intent(text)
    assert result.action == action and result.motion == motion


def test_video_and_costume_schema_is_allowlisted():
    assert decode_intent('{"action":"video","confidence":0.99}').action == "video"
    assert decode_intent('{"action":"costume","motion":"crown","confidence":0.99}').motion == "crown"
    assert decode_intent('{"action":"costume","motion":"arbitrary-file","confidence":0.99}') is None
    assert video_requested("我想看奶蛙", "奶蛙,奶娃")
    assert video_requested("奶娃", "奶蛙,奶娃")
    assert not video_requested("不要播放奶蛙", "奶蛙,奶娃")


def test_repeated_video_keywords_bypass_slow_ai_queue(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        broadcast, classify = AsyncMock(), AsyncMock()
        monkeypatch.setattr(scheduler.hub, "broadcast", broadcast)
        monkeypatch.setattr(brain.llm, "classify_intent", classify)
        for index in range(3):
            await brain.handle(Event(kind="chat", text="奶蛙", user_name="小鱼"))
        videos = [call.args[0] for call in broadcast.await_args_list if call.args[0]["type"] == "video"]
        assert len(videos) == 3 and len({message["event_id"] for message in videos}) == 3
        classify.assert_not_called()
        await brain.set_paused(True)
        await brain.handle(Event(kind="chat", text="奶蛙"))
        assert sum(call.args[0]["type"] == "video" for call in broadcast.await_args_list) == 3

    asyncio.run(scenario())


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    pytest.importorskip("imageio_ffmpeg")
    return create_sample_video(tmp_path_factory.mktemp("透明视频样例") / "透明方块.webm")


@pytest.fixture(scope="module")
def decoded_clip(sample_video):
    return ClipDecoder().decode(sample_video)


def test_generated_video_retains_transparency_and_audio(decoded_clip):
    assert 1.7 < decoded_clip.duration < 2
    assert len(decoded_clip.frames) == 55
    assert decoded_clip.audio.startswith(b"RIFF")
    image = decoded_clip.frames[0]
    assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() < 10
    assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() > 240


def test_video_repeats_pause_resume_reset_and_bounded_queue(qt, decoded_clip, monkeypatch):
    from app.native import video as video_module
    clock = [100.0]
    monkeypatch.setattr(video_module.time, "monotonic", lambda: clock[0])
    state = StageState()
    controller = VideoController(state, muted=True)
    controller.clip = decoded_clip
    try:
        assert controller.trigger("one")
        assert not controller.trigger("one")
        assert controller.trigger("two") and len(controller.queue) == 1
        clock[0] += 0.5
        controller._tick()
        assert state.video_image is not None
        controller.set_paused(True)
        frame = state.video_image
        clock[0] += 10
        controller._tick()
        assert state.video_image is frame
        controller.set_paused(False)
        assert controller.elapsed == pytest.approx(0.5)
        clock[0] += 1.5
        controller._tick()
        assert controller.active and not controller.queue
        for index in range(10):
            assert controller.trigger(f"queued-{index}")
        assert not controller.trigger("overflow")
        controller.handle({"type": "reset"})
        assert not controller.active and not controller.queue and state.video_image is None
        controller.mute(False)
        assert not controller.output.isMuted()
    finally:
        controller.close()


def test_loading_is_async_and_close_cancels(qt, sample_video):
    state = StageState()
    state.config["stage"]["video_path"] = str(sample_video)
    controller = VideoController(state, muted=True)
    try:
        controller.configure()
        assert controller.loading
        assert controller.trigger("loading-request")
        deadline = time.monotonic() + 10
        while controller.loading and time.monotonic() < deadline:
            pump(qt, 0.01)
        assert controller.clip is not None and controller.active
    finally:
        controller.close()
    assert not controller.jobs and controller.clip is None


@pytest.mark.parametrize("costume", COSTUMES)
def test_costume_draws_with_rotated_character(qt, costume):
    state = StageState()
    state.apply({"type": "costume", "name": costume})
    state.apply({"type": "reaction", "motion": "handstand", "manual": True})
    scene = Scene(720, 1280)
    started = state.reaction["started"]
    assert pose(state.reaction, started + 1)["tilt"] == 180
    image = scene.render(state.snapshot(), 1, started + 1)
    assert not image.isNull()


def test_video_and_bubbles_render_together(qt, decoded_clip):
    state = StageState()
    state.set_video(decoded_clip.frames[0], 1)
    scene = Scene(720, 1280)
    visible = scene.render(state.snapshot(), 1, time.monotonic())
    state.set_video(None)
    hidden = scene.render(state.snapshot(), 1, time.monotonic())
    assert visible != hidden
