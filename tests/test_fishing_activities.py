from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import random
import time

import pytest

from app.brain import scheduler
from app.brain.capabilities import available_commands, runtime_context
from app.brain.intent import decode_intent, local_intent
from app.events import Event
from app.fishing import FishingGame
from app.hub import Hub
from app.like_goal import LikeGoal
from app.live_history import LiveHistory
from app.metrics import Metrics
from app.ingest import douyin_cdp, douyin_direct
from app.ingest.bridge import normalize
from app.native.avatar.rig import joint_pose
from app.native.fishing_scene import fishing_view
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS, Effective, store
from tests.test_fishing import game_at_bite
from tests.test_douyin_parse import fld_int, fld_msg, user_struct
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def waiting_game(seed=4):
    game = FishingGame({**Effective.fishing(), "dedicated": True, "escape_chance": 0,
                        "bite_min": 30, "bite_max": 30}, random.Random(seed))
    game.ambience_started = 0
    game.start("海风会客厅", 0)
    return game


@pytest.mark.parametrize("text,operation", [("退出常驻钓鱼模式", "exit_dedicated"),
    ("关闭钓鱼模式", "exit_dedicated"), ("结束常驻钓鱼", "exit_dedicated"),
    ("进入常驻钓鱼模式", "enter_dedicated"), ("松", "loosen"), ("放松鱼线", "loosen"),
    ("小鱼请投饵吧", "feed"), ("喂鱼", "feed"), ("加油", "cheer"), ("护线", "cheer")])
def test_new_commands_and_ai_decode(text, operation):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == ("fishing", operation)
    assert decode_intent(json.dumps({"action": "fishing", "motion": operation, "confidence": 1})).motion == operation


@pytest.mark.parametrize("text", ["别松", "不要松线", "他说松", "松鼠", "不想退出常驻钓鱼模式", "不要喂鱼"])
def test_negative_or_quoted_text_is_not_executed(text):
    assert local_intent(text).action != "fishing"


def test_loosen_clamped_no_credit_and_repeated_text_is_not_deduplicated():
    game = game_at_bite()
    game.fish["pulls"] = 20
    game.pull(5, {"id": "leader", "name": "拉钩鱼友"}, now=9)
    contributions = copy.deepcopy(game.contributors)
    assert game.fish_action("loosen", {"id": "mischief", "name": "捣蛋鱼友"}, "first", 10)
    assert game.progress == pytest.approx(.17)
    assert game.contributors == contributions and game.winner["id"] == "leader"
    assert not game.fish_action("loosen", event_id="first", now=11)
    with pytest.raises(ValueError, match="冷却"):
        game.fish_action("loosen", event_id="second", now=11)
    assert game.fish_action("loosen", event_id="second", now=12)
    game.fish_action("loosen", event_id="third", now=14)
    game.fish_action("loosen", event_id="fourth", now=16)
    assert game.progress == 0
    with pytest.raises(ValueError, match="为零"):
        game.fish_action("loosen", now=18)
    game.add_likes(10, "like", now=19)
    assert game.progress == pytest.approx(.05)


def test_loosen_disabled_and_paused_and_other_phases():
    game = waiting_game()
    with pytest.raises(ValueError, match="咬钩"):
        game.fish_action("loosen", now=1)
    game.tick(31)
    game.progress = .5
    game.config["loosen_enabled"] = False
    with pytest.raises(ValueError, match="关闭"):
        game.fish_action("loosen", now=32)
    game.config["loosen_enabled"] = True
    game.pause(True, 32)
    with pytest.raises(ValueError, match="暂停"):
        game.fish_action("loosen", now=33)
    assert game.progress == .5


def test_feed_preserves_round_and_shortens_remaining_after_animation():
    game = waiting_game()
    identifier = game.round_id
    game.fish_action("feed", event_id="feed", now=10)
    assert game.phase == "interacting" and game.interruption["action"] == "feed"
    game.tick(14.5)
    assert game.phase == "waiting" and game.round_id == identifier
    assert game.deadline - 14.5 == pytest.approx(20 * .65)
    with pytest.raises(ValueError, match="冷却"):
        game.fish_action("feed", now=15)


def test_cheer_reduces_escape_risk_and_expires(monkeypatch):
    guarded, ordinary = game_at_bite(escape=.1), game_at_bite(escape=.1)
    for game in (guarded, ordinary):
        monkeypatch.setattr(game.rng, "random", lambda: .075)
    guarded.fish_action("cheer", now=9)
    assert guarded.snapshot(10)["cheer_remaining"] == 9
    guarded.pull(now=10)
    ordinary.pull(now=10)
    assert guarded.phase == "hooked" and ordinary.phase == "escaped"
    guarded.pull(now=20)
    assert guarded.phase == "escaped"


def test_net_value_budget_owner_and_duplicate_restores_waiting():
    game = waiting_game()
    identifier = game.round_id
    donor = {"id": "gift", "name": "送礼鱼友"}
    assert game.add_net_gift(2, 100, donor, "gift-1", "鲜花", 5)
    assert game.phase == "net_cast"
    haul = copy.deepcopy(game.net)
    assert haul["user"] == donor and not haul["estimated"]
    assert 100 * 20 * .8 <= haul["total"] <= 100 * 20 * 1.4
    assert sum(fish["value"] for fish in haul["fish"]) == haul["total"]
    assert len(haul["fish"]) == 3 and not game.gift_bank
    assert game.add_net_gift(2, 100, donor, "gift-1", "鲜花", 6)
    assert game.net == haul and not game.net_queue
    game.tick(15)
    assert game.phase == "net_caught"
    game.tick(23)
    assert game.phase == "waiting" and game.round_id == identifier
    assert game.deadline - 23 == 25
    assert game.add_net_gift(2, 100, donor, "gift-1", "鲜花", 24)
    assert game.phase == "waiting" and not game.net_queue


def test_hooked_gifts_still_pull_and_interrupted_hooked_is_not_net():
    game = game_at_bite()
    game.config["dedicated"] = True
    game.fish["pulls"] = 50
    assert not game.add_net_gift(1, 100, {"id": "gift", "name": "鱼友"}, "one", now=9)
    assert game.add_gift(1, {"id": "gift", "name": "鱼友"}, "one", 9)
    assert game.progress == .1
    game.interrupt("跳舞", 5, 10)
    assert not game.add_net_gift(1, 100, event_id="two", now=11)
    assert game.add_gift(1, {"id": "gift", "name": "鱼友"}, "two", 11)
    game.resume(15)
    assert game.progress == .2 and not game.net_queue


def test_net_queue_coalesces_same_sender_and_falls_back_when_full():
    game = waiting_game()
    game.add_net_gift(1, 1, {"id": "first"}, "first", now=1)
    for index in range(32):
        assert game.add_net_gift(1, 1, {"id": str(index)}, str(index), now=2)
    assert not game.add_net_gift(1, 10, {"id": "overflow"}, "overflow", now=3)
    assert game.add_gift(1, {"id": "overflow"}, "overflow", 3)
    assert game.add_net_gift(2, 5, {"id": "0"}, "merged", now=3)
    assert len(game.net_queue) == 32
    assert game.net_queue[0]["value"] == 6 and game.net_queue[0]["count"] == 3
    game.tick(11)
    game.tick(19)
    game.tick(19.1)
    assert game.net["user"]["id"] == "0" and len(game.net_queue) == 31
    game.cancel(20)
    assert not game.net_queue and not game.net and not game.gift_bank


@pytest.mark.parametrize("value", [0, None, -5, float("nan"), float("inf")])
def test_unknown_gift_value_falls_back_to_count(value):
    game = waiting_game()
    game.add_net_gift(3, value, {"name": "无价格鱼友"}, now=1)
    assert game.net["estimated"] and game.net["value"] == 3
    assert 48 <= game.net["total"] <= 84


def test_pause_freezes_net_action_and_day_cycle():
    game = waiting_game()
    game.add_net_gift(1, 10, now=1)
    game.pause(True, 4)
    snapshot = game.snapshot(4)
    assert not game.tick(200) and game.snapshot(200) == snapshot
    game.pause(False, 204)
    resumed = game.snapshot(204)
    assert resumed["phase_elapsed"] == snapshot["phase_elapsed"]
    assert resumed["day_phase"] == snapshot["day_phase"]
    game.tick(211)
    game.tick(219)
    assert game.phase == "waiting" and game.deadline - 219 == 29


@pytest.mark.parametrize("patch", [{"day_mode": "broken"}, {"day_cycle": 20}, {"day_cycle": 7201},
    {"net_multiplier": 0}, {"net_multiplier": 1.5}, {"loosen_amount": 0}, {"loosen_amount": .26}, {"loosen_amount": float("nan")}])
def test_new_settings_validation(patch):
    before = store.get_all()
    with pytest.raises(ValueError):
        store.update({"fishing": patch})
    assert before == store.get_all()


def test_dispatch_net_scores_donor_once_voice_ai_and_history(tmp_path, monkeypatch):
    async def scenario():
        store.update({"fishing": {"dedicated": True}})
        brain = scheduler.Brain()
        await brain.ensure_dedicated_fishing()
        gift = Event(kind="gift", user_id="donor", user_name="中文送礼者", gift_name="鲜花", gift_count=2, gift_value=100)
        assert await brain._publish_event(gift)
        assert brain.fishing.phase == "net_cast" and not brain.gift_show.active
        assert "中文送礼者" in brain.live_voice_candidate()[0]
        assert "net_multiplier" in brain.runtime_context()["fishing"]["rules"]
        total = brain.fishing.net["total"]
        brain.fishing.tick(brain.fishing.deadline)
        await brain._sync_fishing()
        assert "恭喜中文送礼者" in brain.live_voice_candidate()[0]
        await brain._sync_fishing()
        await brain._publish_event(gift)
        board = scheduler.metrics.fishing_ranking()
        assert board["ranking"][0]["key"] == "id:donor"
        assert board["ranking"][0]["value"] == total
        assert board["catches"] == len(brain.fishing.net["fish"])
        assert board["encounters"] == board["catches"]
        history = LiveHistory(tmp_path / "撒网.sqlite3", scheduler.metrics, Hub(), LikeGoal())
        history.write(*history.capture())
        restored = LiveHistory(history.path, Metrics(), Hub(), LikeGoal())
        assert restored.restore() and restored.metrics.fishing_ranking() == board
        today = dt.date

        class Tomorrow(today):
            @classmethod
            def today(cls):
                return today.today() + dt.timedelta(days=1)

        monkeypatch.setattr(dt, "date", Tomorrow)
        await brain._sync_fishing()
        assert scheduler.metrics.fishing_ranking()["catches"] == 0
    asyncio.run(scenario())


def test_exit_alias_authorization_and_net_cleanup():
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("enter_dedicated", manual=True)
        brain.fishing.add_net_gift(1, 2, {"name": "礼物用户"})
        operation = local_intent("退出常驻钓鱼模式").motion
        with pytest.raises(ValueError, match="管理员"):
            await brain.fishing_command(operation, Event(kind="chat", user_id="stranger"))
        assert brain.fishing.net and Effective.fishing()["dedicated"]
        await brain.fishing_command(operation, Event(kind="chat", is_admin=True))
        assert not Effective.fishing()["dedicated"] and not brain.fishing.active
        assert not brain.fishing.net and not brain.fishing.net_queue
        assert not await brain.ensure_dedicated_fishing()
    asyncio.run(scenario())


def test_net_does_not_steal_hooked_gifts_or_disabled_shows():
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("enter_dedicated", manual=True)
        brain.fishing.tick(brain.fishing.deadline)
        brain.fishing.deadline = time.monotonic() + 60
        brain.fishing.config["escape_chance"] = 0
        brain.fishing.fish["pulls"] = 50
        await brain._publish_event(Event(kind="gift", user_id="pull", user_name="礼物拉钩", gift_count=1, gift_value=99))
        assert brain.fishing.progress == .1 and brain.fishing.winner["id"] == "pull"
        assert not brain.fishing.net and brain.gift_show.active
        brain.gift_show.clear()
        brain.sync_activity_pauses()
        brain.fishing.cancel()
        store.update({"fishing": {"net_enabled": False}})
        await brain.ensure_dedicated_fishing()
        await brain._publish_event(Event(kind="gift", user_id="saved", gift_count=1))
        assert not brain.fishing.net and brain.fishing.gift_bank
        assert brain.gift_show.active
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(1280, 720), (720, 1280)])
def test_net_day_night_and_actions_render_and_freeze(qt, size):
    game = waiting_game()
    state = StageState()
    state.config.update(stage=copy.deepcopy(DEFAULTS["stage"]))
    state.config["stage"]["layout"] = "landscape" if size[0] > size[1] else "portrait"
    snapshot = state.snapshot()
    scene = Scene(*size)
    frames = []
    game.add_net_gift(1, 1000, {"name": "中文送礼鱼友"}, "gift", now=1)
    for elapsed in (0, 2, 4, 6, 8, 10):
        game.tick(1 + elapsed)
        snapshot.update(fishing=game.snapshot(1 + elapsed), fishing_at=0, animation_time=0)
        frames.append(scene.render(snapshot, 0, 0))
    assert all(not frame.isNull() for frame in frames)
    assert all(first != second for first, second in zip(frames, frames[1:]))
    snapshot["fishing"].update(day_phase=0, day_speed=0)
    night = scene.render(snapshot, 0, 0)
    snapshot["fishing"].update(day_phase=.5)
    day = scene.render(snapshot, 0, 0)
    assert night != day and len(scene.fishing.backgrounds) <= 4
    snapshot.update(paused=True, paused_at=0)
    assert scene.render(snapshot, 10, 100) == scene.render(snapshot, 20, 200)


def test_new_rig_poses_distinct_and_day_view_interpolates():
    poses = [joint_pose({"motion": motion, "started": 0, "duration": 30}, 3, 3)
             for motion in ("net_windup", "net_cast", "net_haul", "fishing_loosen", "fishing_cheer")]
    assert all(first != second for first, second in zip(poses, poses[1:]))
    game = waiting_game()
    snapshot = {"fishing": game.snapshot(0), "fishing_at": 0}
    assert fishing_view(snapshot, 300)["day_phase"] == pytest.approx(0)
    snapshot["paused"] = True
    assert fishing_view(snapshot, 300)["day_phase"] == pytest.approx(.5)
    store.update({"fishing": {"loosen_enabled": False}})
    assert "loosen" not in available_commands()["fishing"]


@pytest.mark.parametrize("factory", [douyin_cdp.DouyinCDPIngestor, douyin_direct.DouyinDirectIngestor])
def test_protocol_gift_price_name_sender_and_combo_increment(monkeypatch, factory):
    ingest = factory()
    ingest.room = "123"
    events = []
    monkeypatch.setattr(douyin_cdp, "gunzip_any", lambda raw: raw)
    monkeypatch.setattr(douyin_cdp, "iter_envelopes", lambda raw: [("WebcastGiftMessage", raw)])
    monkeypatch.setattr(ingest, "_emit_async", events.append)
    gift = fld_msg(2, "描述不是礼物名".encode()) + fld_msg(16, "超级鲜花".encode()) + fld_int(12, 88)
    for identifier, count in ((501, 1), (502, 2), (503, 2), (504, 1), (505, 5)):
        payload = (fld_msg(1, fld_int(2, identifier)) + fld_int(2, 9) + fld_int(5, count) + fld_int(11, 777)
                   + fld_msg(7, fld_int(1, 12345) + user_struct("送礼者", ""))
                   + fld_msg(8, fld_int(1, 99999) + user_struct("接收者", "")) + fld_msg(15, gift))
        ingest._handle_frame(payload)
    assert [event.gift_count for event in events] == [1, 1, 3]
    assert sum(event.gift_value for event in events) == 5 * 88
    assert all(event.gift_name == "超级鲜花" and event.user_id == "12345" for event in events)
    assert all(not event.gift_value_estimated for event in events)
    assert events[0].id == "douyin:123:WebcastGiftMessage:501"


def test_unknown_price_is_explicit_and_bridge_unit_price_not_doubled():
    unknown = normalize({"type": "gift", "userName": "鱼友", "giftName": "鲜花", "giftCount": 3})
    assert unknown.gift_value_estimated
    priced = normalize({"type": "gift", "userName": "鱼友", "giftName": "鲜花", "giftCount": 3, "giftValue": 10})
    assert priced.gift_value == 30 and not priced.gift_value_estimated
    game = waiting_game()
    game.add_net_gift(priced.gift_count, priced.gift_value, now=1)
    assert 30 * 20 * .8 <= game.net["total"] <= 30 * 20 * 1.4
