from __future__ import annotations

import asyncio
import copy
import time
import wave
from unittest.mock import AsyncMock, Mock

import pytest
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer

from app.brain import scheduler
from app import main
from app.brain.intent import local_intent
from app.events import Event
from app.gift_show import GiftShow, SHOWS, gift_effect
from app.native.audio import AudioController
from app.native.avatars import AvatarLoader
from app.native.gift_scene import gift_view
from app.native.fishing_scene import fishing_view
from app.native.scene import Scene
from app.native.state import StageState
from app.native.video import VideoClip, VideoController
from app.settings_store import Effective, store
from tests.test_fishing import game_at_bite
from tests.test_renderer import qt, pump
from tests.test_runtime import isolated_runtime


def test_catch_goes_to_largest_combined_contribution_and_ties_keep_first():
    game = game_at_bite()
    game.pull(user={"id": "first", "name": "首位鱼友"}, event_id="one", now=9)
    game.pull(user={"id": "second", "name": "认真拉钩"}, event_id="two", now=9)
    assert game.winner["id"] == "first"
    game.pull(user={"id": "second", "name": "认真拉钩"}, event_id="three", now=10)
    assert game.winner["id"] == "second"
    game.add_likes(25, "likes", 11, user={"id": "like", "name": "点赞冠军"})
    assert game.winner == {"id": "like", "name": "点赞冠军"}
    leader = game.snapshot(11)["contributors"][0]
    assert (leader["likes"], leader["pulls"], leader["score"]) == (25, 0, 2.5)
    snapshot = game.snapshot(11)
    assert not game.add_likes(25, "likes", 11, user={"id": "other", "name": "重放"})
    assert game.snapshot(11) == snapshot


def test_named_like_events_keep_identity_and_room_totals_do_not_belong_to_last_user():
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("start")
        brain.fishing.config["escape_chance"] = 0
        brain.fishing.tick(brain.fishing.deadline)
        brain.fishing.deadline = time.monotonic() + 25
        await brain._publish_event(Event(kind="like", user_id="one", user_name="鱼友甲", like_count=15, like_total=1000))
        assert brain.fishing.winner["name"] == "鱼友甲"
        await brain._publish_event(Event(kind="like", user_id="two", user_name="鱼友乙", like_count=1, like_total=1020))
        assert brain.fishing.contributors["two"]["likes"] == 1
        assert brain.fishing.winner["name"] == "鱼友甲"
        before = brain.fishing.snapshot()
        await brain._publish_event(Event(kind="like", user_id="two", user_name="鱼友乙", like_count=20, like_total=1020))
        assert brain.fishing.contributors["two"]["likes"] == 1
        assert brain.fishing.progress == before["progress"]

    asyncio.run(scenario())


def test_anonymous_likes_do_not_create_fake_viewer():
    game = game_at_bite()
    game.add_likes(999, "unknown", 9, user={"id": "", "name": "观众"})
    assert game.winner is None and not game.contributors


def test_injected_repeated_messages_share_user_identity_but_keep_distinct_event_ids():
    async def scenario():
        for kind in ("chat", "chat", "like"):
            await main.api_inject({"kind": kind, "user": "同一个人", "text": "拉", "count": 1})
        events = [scheduler.hub.queue.get_nowait() for _ in range(3)]
        assert len({event.user_id for event in events}) == 1
        assert len({event.id for event in events}) == 3
    asyncio.run(scenario())


def test_new_settings_validate_and_preserve_chinese_file_paths():
    store.update({"bgm": {"path": "D:/音乐/海风.mp3", "enabled": True, "volume": .32},
                  "fishing": {"dedicated": True}, "idle": {"dream_seconds": 30},
                  "lines": {"fishing_caught": ["恭喜{name}，你钓到{fish}，价值{value}。"]}})
    assert Effective.bgm()["path"] == "D:/音乐/海风.mp3"
    assert Effective.fishing()["dedicated"]
    before = store.get_all()
    for invalid in ({"bgm": {"volume": 2}}, {"bgm": {"duck": -1}}, {"idle": {"dream_seconds": 0}},
                    {"fishing": {"dedicated": "true"}}, {"lines": {"gift_flowers": ["{wrong}"]}}):
        with pytest.raises(ValueError):
            store.update(invalid)
        assert store.get_all() == before


def test_queued_glasses_only_wear_on_their_own_show_and_skip_generic_gift_rule(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        monkeypatch.setattr(brain, "_maybe_gift_song", AsyncMock(return_value=False))
        match = Mock()
        monkeypatch.setattr(brain.interact, "match", match)
        await brain.handle(Event(kind="gift", gift_name="鲜花", user_name="送花的人"))
        await brain.handle(Event(kind="gift", gift_name="墨镜", user_name="送墨镜的人"))
        assert Effective.stage()["costume"] != "cool"
        match.assert_not_called()
        brain.gift_show.tick(brain.gift_show.started + 12)
        await brain.sync_gift_show()
        assert Effective.stage()["costume"] == "cool"
        assert brain.gift_show.active["user"]["name"] == "送墨镜的人"
    asyncio.run(scenario())


def test_result_voice_finishes_before_late_pull_or_like_can_advance_round(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.fishing = game_at_bite()
        brain.fishing.pull(999, {"name": "冠军"}, now=9)
        brain.fishing._phase("caught", time.monotonic() - 7, 6)
        entered = asyncio.Event()
        async def speak(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        monkeypatch.setattr(brain, "_speak", speak)
        await brain.live_voice_tick()
        await entered.wait()
        try:
            assert brain.fishing_result_speaking()
            with pytest.raises(ValueError, match="播报本轮鱼获"):
                await brain.fishing_command("pull", Event(kind="chat", text="拉"))
            await brain._publish_event(Event(kind="like", gift_count=100))
            assert brain.fishing.phase == "caught"
        finally:
            await brain.stop_live_voice()
    asyncio.run(scenario())


def test_dedicated_fishing_settles_once_recasts_and_setting_off_returns():
    async def scenario():
        brain = scheduler.Brain()
        store._data["fishing"].update(dedicated=True, escape_chance=0)
        await brain.configure_live_features()
        assert brain.fishing.active and brain.world.paused_at is not None
        original_round = brain.fishing.round_id
        brain.fishing.tick(brain.fishing.deadline)
        brain.fishing.pull(999, {"id": "champion", "name": "贡献冠军"}, now=brain.fishing.phase_started + 1)
        brain.fishing.tick(brain.fishing.deadline)
        await brain._sync_fishing()
        value = brain.fishing.fish["value"]
        await brain._sync_fishing()
        ranking = scheduler.metrics.fishing_ranking()["ranking"]
        assert ranking[0]["name"] == "贡献冠军" and ranking[0]["value"] == value
        brain.fishing.tick(brain.fishing.deadline)
        await brain._sync_fishing()
        assert brain.fishing.phase == "waiting" and brain.fishing.round_id != original_round
        with pytest.raises(ValueError, match="常驻钓鱼"):
            await brain.cancel_fishing()
        await brain._execute_intent(Event(kind="chat", text="回家"), local_intent("回家"), quick=True)
        assert brain.fishing.active
        previous = Effective.fishing()
        store._data["fishing"]["dedicated"] = False
        await brain.configure_live_features(previous)
        assert not brain.fishing.active and brain.world.paused_at is None

    asyncio.run(scenario())


@pytest.mark.parametrize("gift,effect", [("大啤酒", "tea"), ("鲜花", "flowers"), ("棒棒糖", "lollipop"),
                                         ("墨镜", "sunglasses"), ("小心心", "heart"), ("火箭", "rocket"),
                                         ("嘉年华", "crown"), ("其它礼物", "sparkles")])
def test_gift_effect_selection(gift, effect):
    assert gift_effect(gift, False) == effect


def test_beer_chooses_both_only_with_available_video():
    import random
    rng = random.Random(1)
    assert {gift_effect("大啤酒", True, rng) for _ in range(20)} == {"tea", "video"}
    assert {gift_effect("大啤酒", False, rng) for _ in range(20)} == {"tea"}


def test_gift_queue_never_interrupts_active_and_pause_preserves_full_show():
    shows = GiftShow()
    first = Event(kind="gift", user_id="one", user_name="甲", gift_name="鲜花", gift_count=1)
    second = Event(kind="gift", user_id="two", user_name="乙", gift_name="棒棒糖", gift_count=2)
    assert shows.enqueue(first) and shows.tick(0)
    assert shows.enqueue(first) and not shows.pending
    shows.enqueue(second)
    shows.enqueue(Event(kind="gift", user_id="two", user_name="乙", gift_name="棒棒糖", gift_count=3))
    assert len(shows.pending) == 1 and shows.pending[0]["count"] == 5
    assert not shows.tick(9) and shows.active["id"] == first.id
    shows.pause(True, 9)
    assert not shows.tick(900)
    frozen = shows.snapshot(900)
    assert frozen["elapsed"] == 9
    shows.pause(False, 900)
    assert not shows.tick(901)
    assert shows.tick(902) and shows.active["user"]["name"] == "乙"


def test_gift_freezes_hook_then_restores_same_round_without_losing_sleep(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("start")
        identifier = brain.fishing.round_id
        await brain._publish_event(Event(kind="gift", user_name="星光", gift_name="鲜花", gift_count=1))
        frozen = brain.fishing.snapshot()
        assert brain.fishing.paused_at is not None
        assert brain.fishing.snapshot(time.monotonic() + 80) == frozen
        assert brain.runtime_context()["gift_show"]["active"]["user"]["name"] == "星光"
        assert "星光" in brain.runtime_context()["primary_activity"]
        brain.gift_show.tick(brain.gift_show.started + 12)
        await brain.sync_gift_show()
        assert brain.fishing.round_id == identifier and brain.fishing.paused_at is None
        assert brain.world.paused_at is not None
        await brain.cancel_fishing()
        brain.world.command("activity", "sleep", manual=True)
        brain.world.movement["started"] -= 30
        await brain._publish_event(Event(kind="gift", user_name="月光", gift_name="棒棒糖"))
        assert brain.world.describe()["activity"] == "sleep"
        brain.gift_show.tick(brain.gift_show.started + 12)
        await brain.sync_gift_show()
        assert brain.world.describe()["activity"] == "sleep" and brain.world.paused_at is None

    asyncio.run(scenario())


def test_bite_voice_is_prompt_deduplicated_and_cancelled_after_catch(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.fishing = game_at_bite()
        brain.fishing.deadline = time.monotonic() + 25
        spoken = []
        cancelled = asyncio.Event()

        async def speak(text, *args, **kwargs):
            spoken.append(text)
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        monkeypatch.setattr(brain, "_speak", speak)
        await brain.live_voice_tick()
        await asyncio.sleep(0)
        assert "拉" in spoken[0] and "鱼" in spoken[0]
        await brain.live_voice_tick()
        assert len(spoken) == 1
        brain.fishing._phase("landing", time.monotonic(), 1.6)
        await brain.live_voice_tick()
        assert cancelled.is_set() and brain._live_voice_task is None

    asyncio.run(scenario())


def test_dream_talk_preserves_sleep_and_wake_invalidates_line():
    brain = scheduler.Brain()
    brain.world.command("activity", "sleep", manual=True)
    brain.world.movement["started"] -= 30
    brain._sleep_since = time.monotonic() - 10
    candidate = brain.live_voice_candidate()
    assert candidate and "醒" in candidate[0]
    assert candidate[1]() and brain.world.describe()["activity"] == "sleep"
    brain.world.command("activity", "wake", manual=True)
    assert not candidate[1]()
    assert brain.live_voice_candidate() is None


def test_dream_speech_has_no_motion_and_keeps_full_editable_line(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        store._data["lines"]["dream_talk"] = ["梦里也在想你们，发醒醒就能叫醒我，我们继续一起玩吧。"]
        brain.world.command("activity", "sleep", manual=True)
        brain.world.movement["started"] -= 30
        brain._sleep_since = time.monotonic() - 10
        speech = AsyncMock()
        monkeypatch.setattr(brain, "_speak", speech)
        await brain.live_voice_tick()
        await brain._live_voice_task
        speech.assert_awaited_once_with(store.lines("dream_talk")[0], None, 1, motion="")
        await brain.live_voice_tick()
        assert speech.await_count == 1

    asyncio.run(scenario())


def test_direct_reply_cancels_dream_before_new_voice_can_start(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        entered = asyncio.Event()
        order = []
        async def say(text, *args, **kwargs):
            order.append(text)
            if text == "梦话":
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    order.append("梦话停止")
        monkeypatch.setattr(brain.speaker, "say", say)
        brain._live_voice_guard = lambda: True
        brain._live_voice_task = asyncio.create_task(brain._speak("梦话", None, 1, motion=""))
        await entered.wait()
        await brain._speak("我醒啦", Event(kind="chat", text="醒醒"), 1)
        assert order == ["梦话", "梦话停止", "我醒啦"]
        assert brain._live_voice_task is None
    asyncio.run(scenario())


def test_gift_native_reconnect_and_frozen_fishing_do_not_advance(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: clock[0])
    shows = GiftShow()
    shows.enqueue(Event(kind="gift", gift_name="鲜花", user_name="送礼鱼友"))
    shows.tick(95)
    shows.pause(True, 100)
    game = game_at_bite()
    game.pause(True, 10)
    state = StageState()
    state.apply({"type": "hello", "paused": True, "giftShow": shows.snapshot(), "fishing": game.snapshot()})
    assert gift_view(state.snapshot(), 500)["elapsed"] == 5
    state.apply({"type": "state", "paused": False})
    assert gift_view(state.snapshot(), 101)["elapsed"] == 6
    assert fishing_view(state.snapshot(), 500)["remaining"] == game.snapshot()["remaining"]


def test_active_donor_avatar_loads_when_bubbles_disabled(qt):
    state = StageState()
    state.config["stage"] = {"show_ballpit": False}
    state.gift_show = {"active": {"user": {"avatar": "https://example.invalid/donor.png"}}}
    loader = AvatarLoader(state)
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(0xffabcdef)
    loader.cache["https://example.invalid/donor.png"] = image
    try:
        loader.refresh()
        state._rank({}, time.monotonic())
        assert state.avatar_images["https://example.invalid/donor.png"] == image
    finally:
        loader.close()


def test_sunglasses_config_immediately_takes_priority_over_old_rank_crown():
    state = StageState()
    state.celebration_until = time.monotonic() + 8
    state.apply({"type": "config_changed", "config": {"stage": {"costume": "cool"}}})
    assert state.costume == "cool" and state.celebration_until == 0
    state.celebration_until = time.monotonic() + 8
    state.apply({"type": "hello", "config": {"stage": {"costume": "cool"}}})
    assert state.celebration_until == 0


@pytest.mark.parametrize("orientation", [(720, 1280), (1280, 720)])
def test_all_gift_shows_render_distinct_complete_stages(qt, orientation):
    state = StageState()
    state.config["world"] = {"enabled": False}
    scene = Scene(*orientation)
    for effect, (label, duration) in SHOWS.items():
        show = {"id": effect, "effect": effect, "label": label, "duration": duration,
                "user": {"id": "test", "name": "给你送礼的观众", "avatar": ""}, "gift": label, "count": 3}
        captures = []
        for elapsed in (1, 5.5, duration - 1):
            state.gift_show = {"active": show, "elapsed": elapsed, "paused": True, "pending": 2}
            captures.append(scene.render(state.snapshot(), elapsed, 100))
        assert captures[0] != captures[1] != captures[2]
        assert all(not capture.isNull() and capture.size() == captures[0].size() for capture in captures)


def test_bgm_loops_pauses_ducks_and_resumes_without_touching_song_queue(qt, tmp_path):
    path = tmp_path / "中文背景音乐.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 1600)
    state = StageState()
    state.connected = True
    state.config["bgm"] = {"enabled": True, "path": str(path), "volume": .4, "duck": .25}
    controller = AudioController(state, "http://127.0.0.1:1", muted=True)
    errors = []
    controller.notice.connect(errors.append)
    try:
        controller.handle({"type": "hello"})
        pump(qt, .65)
        assert controller.bgm.loops() == QMediaPlayer.Loops.Infinite.value
        assert controller.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        assert controller.bgm.source().toLocalFile() == str(path).replace("\\", "/")
        controller.pending_music = {"playing": True}
        controller.apply_bgm()
        assert controller.bgm.playbackState() == QMediaPlayer.PlaybackState.PausedState
        controller.pending_music = {}
        controller.apply_bgm()
        state.video_image = QImage(1, 1, QImage.Format.Format_ARGB32)
        for _ in range(25):
            controller._tick()
        assert controller.bgm_output.volume() == pytest.approx(.1, abs=.01)
        state.paused = True
        controller.handle({"type": "state", "paused": True})
        assert controller.bgm.playbackState() == QMediaPlayer.PlaybackState.PausedState
        state.paused = False
        state.connected = False
        controller.apply_bgm()
        assert controller.bgm.playbackState() == QMediaPlayer.PlaybackState.PausedState
        state.connected = True
        controller.apply_bgm()
        assert controller.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        assert not errors
    finally:
        controller.close()
    assert controller.bgm.source().isEmpty()


def test_gift_video_joins_current_time_and_cannot_spill_into_next_show(qt):
    state = StageState()
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(0xff88ccdd)
    controller = VideoController(state, muted=True)
    controller.clip = VideoClip((image,) * 90, b"")
    show = {"id": "milk", "effect": "video", "duration": 12}
    state.apply({"type": "gift_show", "show": {"active": show, "elapsed": 1, "paused": False}})
    try:
        controller.handle({"type": "gift_show"})
        assert controller.active and controller.elapsed == pytest.approx(1, abs=.1)
        assert not controller.trigger("normal-during-gift")
        state.apply({"type": "gift_show", "show": {"active": {**show, "id": "flowers", "effect": "flowers"}, "elapsed": 0}})
        controller.handle({"type": "gift_show"})
        assert not controller.active and state.video_image is None
        state.apply({"type": "gift_show", "show": {"active": None}})
        controller.handle({"type": "gift_show"})
        assert controller.trigger("normal-after-gift")
    finally:
        controller.close()
