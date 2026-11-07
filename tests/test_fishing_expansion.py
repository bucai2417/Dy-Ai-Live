from __future__ import annotations

import asyncio
import datetime as dt
import random
import time
from unittest.mock import AsyncMock

import pytest

from app.brain import scheduler
from app.brain.intent import Intent, decode_intent, local_intent
from app.events import Event
from app.fishing import FISH, FishingGame
from app.fishing_board import FishingBoard
from app.hub import Hub
from app.like_goal import LikeGoal
from app.live_history import LiveHistory
from app.metrics import Metrics
from app.native.diving_scene import dive_position
from app.native.fishing_scene import fishing_view
from app.native.scene import Scene
from app.native.state import StageState
from app.performance import PerformanceState
from app.settings_store import Effective, store
from tests.test_fishing import game_at_bite
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def test_random_timing_catalog_and_bounded_school():
    waits, fights, kinds = set(), set(), set()
    for seed in range(1000):
        game = FishingGame(Effective.fishing(), random.Random(seed))
        game.start("会客厅", 0)
        assert 12 <= game.deadline <= 28
        waits.add(round(game.deadline, 2))
        game.tick(game.deadline)
        duration = game.deadline - game.phase_started
        assert 25 <= duration <= 45
        fights.add(round(duration, 2))
        kinds.add(game.fish["kind"])
        assert len(game.school) == 12
    assert len(waits) > 100 and len(fights) > 100
    assert kinds == set(FISH)
    assert FISH["whale"]["size"] > FISH["shark"]["size"] > FISH["silver"]["size"]


@pytest.mark.parametrize("patch", [
    {"bite_min": 29, "bite_max": 28}, {"fight_min": 0}, {"fight_max": 301},
    {"gift_pulls": 1}, {"gift_pulls": 31}, {"bite_max": float("nan")}, {"fight_min": True},
])
def test_invalid_ranges_do_not_change_settings(patch):
    previous = store.get_all()
    with pytest.raises(ValueError):
        store.update({"fishing": patch})
    assert store.get_all() == previous


def test_gift_boost_is_stronger_deduplicated_and_credited_while_show_pauses():
    game = game_at_bite()
    game.fish["pulls"] = 30
    game.pull(user={"id": "chat", "name": "拉钩鱼友"}, now=9)
    game.add_likes(1, "like", 9, {"id": "like", "name": "点赞鱼友"})
    game.pause(True, 10)
    assert game.add_gift(1, {"id": "gift", "name": "送礼鱼友"}, "gift-1", now=20)
    assert game.winner["id"] == "gift"
    assert game.contributors["gift"]["score"] == 5
    assert game.progress == pytest.approx(6.1 / 30)
    snapshot = game.snapshot(20)
    assert not game.add_gift(1, {"id": "gift", "name": "送礼鱼友"}, "gift-1", now=21)
    assert game.snapshot(21) == snapshot
    assert game.add_gift(1, {"id": "gift", "name": "送礼鱼友"}, "gift-2", now=22)
    game.pause(False, 30)
    assert game.snapshot(30)["remaining"] == pytest.approx(snapshot["remaining"])
    assert game.contributors["gift"]["gifts"] == 2


def test_banked_gifts_all_receive_credit_even_if_first_gift_finishes_fish():
    game = FishingGame({"escape_chance": 0}, random.Random(2))
    game.start("room", 0)
    game.add_gift(1, {"id": "first", "name": "先送礼"}, "one", 1)
    game.add_gift(10, {"id": "champion", "name": "礼物冠军"}, "two", 2)
    game.tick(game.deadline)
    assert game.phase == "landing" and game.progress == 1
    assert game.winner["id"] == "champion"
    assert game.contributors["first"]["score"] == 5
    assert game.contributors["champion"]["score"] == 50
    assert not game.gift_bank


def test_gift_escape_risk_is_not_multiplied_by_gift_count():
    game = game_at_bite(escape=.5)
    game.rng.random = lambda: .2
    game.add_gift(100, {"name": "礼物助力"}, "gift", 9)
    assert game.phase == "landing"
    game = game_at_bite(escape=.5)
    game.rng.random = lambda: .1
    game.add_gift(1, {"name": "礼物助力"}, "gift", 9)
    assert game.phase == "escaped"


def test_gift_at_timeout_is_saved_for_next_bite_and_effect_names_actual_donor():
    game = game_at_bite()
    game.fish["pulls"] = 50
    game.pull(10, {"id": "chat", "name": "拉钩领先者"}, now=9)
    game.add_gift(1, {"id": "donor", "name": "送礼者"}, "first", 10)
    assert game.winner["name"] == "拉钩领先者"
    assert game.last_gift["name"] == "送礼者"
    assert game.add_gift(1, {"name": "压线礼物"}, "late", game.deadline + .01)
    assert game.phase == "escaped" and game.snapshot()["gift_pending"] == 1
    game.tick(game.deadline)
    game.start("room", 40)
    game.tick(game.deadline)
    assert game.winner["name"] == "压线礼物"


def test_interaction_freezes_round_progress_and_survives_pause_extension():
    game = game_at_bite()
    game.pull(user={"name": "鱼友"}, now=9)
    old = game.snapshot(10)
    game.interrupt("跳舞", 6, 10)
    game.pause(True, 12)
    frozen = game.snapshot(100)
    assert not game.tick(100) and game.snapshot(200) == frozen
    game.pause(False, 22)
    game.interrupt("倒立", 5, 23)
    assert game.tick(28)
    current = game.snapshot(28)
    assert current["phase"] == old["phase"]
    assert current["round_id"] == old["round_id"] and current["winner"] == old["winner"]
    assert current["remaining"] == pytest.approx(old["remaining"])
    assert current["progress"] == old["progress"]


@pytest.mark.parametrize("text", ["跳水", "来个花式跳水", "下水游泳", "小鱼跳水吧", "跳入水中"])
def test_diving_commands(text):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == ("fishing", "dive")
    assert decode_intent('{"action":"fishing","motion":"dive","confidence":1}').motion == "dive"


@pytest.mark.parametrize("text", ["不要跳水", "他说跳水", "如果你跳水", "跳水好危险", "今天看跳水比赛"])
def test_diving_does_not_trigger_for_negation_or_chat(text):
    assert local_intent(text).motion != "dive"


def test_dive_scares_fish_preserves_banked_gifts_and_recasts_after_paused_timeline():
    game = game_at_bite()
    previous_round = game.round_id
    game.start_dive(10)
    assert game.fish is None and game.phase == "diving"
    game.add_gift(1, {"name": "跳水观众"}, "gift", 12)
    game.pause(True, 18)
    frozen = game.snapshot(80)
    assert not game.tick(80) and frozen["phase_elapsed"] == 8
    state = StageState()
    state.fishing, state.fishing_at = frozen, 80
    assert fishing_view(state.snapshot(), 100)["phase_elapsed"] == 8
    game.pause(False, 28)
    assert not game.tick(43.9)
    assert game.tick(44) and game.phase == "waiting"
    assert game.round_id != previous_round and game.snapshot(44)["gift_pending"] == 1
    game.tick(game.deadline)
    assert game.winner["name"] == "跳水观众"


def test_species_history_counts_actual_encounters_once_and_restores(tmp_path, monkeypatch):
    history = LiveHistory(tmp_path / "中文图鉴.sqlite3", Metrics(), Hub(), LikeGoal())
    fish = {"kind": "whale", "name": FISH["whale"]["name"], "value": 12800, "day": dt.date.today().isoformat()}
    assert history.metrics.add_fish_encounter("whale-1", fish)
    assert not history.metrics.add_fish_encounter("whale-1", fish)
    history.metrics.add_fish_encounter("whale-2", fish)
    history.metrics.add_fish_encounter("lobster-1", {**fish, "kind": "lobster"})
    history.metrics.add_catch("whale-1", fish, {"name": "冠军"})
    history.write(*history.capture())
    restored = LiveHistory(history.path, Metrics(), Hub(), LikeGoal())
    assert restored.restore()
    board = restored.metrics.fishing_ranking()
    assert board["encounters"] == 3 and board["catches"] == 1
    assert {item["kind"]: item["count"] for item in board["species"]} == {"whale": 2, "lobster": 1}
    assert not restored.metrics.add_fish_encounter("whale-1", fish)
    today = dt.date

    class Tomorrow(today):
        @classmethod
        def today(cls):
            return today.today() + dt.timedelta(days=1)

    monkeypatch.setattr(dt, "date", Tomorrow)
    assert not restored.metrics.add_fish_encounter("whale-1", fish)
    assert restored.metrics.fishing_ranking()["species"] == []


def test_species_history_validates_and_supports_older_files():
    assert FishingBoard.restore({"records": {}, "rounds": []}).snapshot()["species"] == []
    for data in ({"encounters": []}, {"encounters": {"id": "unknown"}}, {"encounters": {"id": []}}):
        with pytest.raises(ValueError):
            FishingBoard.restore(data)


def test_reaction_clock_survives_gift_and_live_pause_combination():
    performance = PerformanceState()
    config = {"interact": {"enabled": True}}
    performance.observe({"type": "reaction", "motion": "dance"}, config, 10)
    before = performance.snapshot(11)
    performance.observe({"type": "gift_show", "show": {"active": {"effect": "flowers"}}}, config, 11)
    assert performance.snapshot(100) == before
    performance.observe({"type": "state", "paused": True}, config, 13)
    performance.observe({"type": "gift_show", "show": {"active": None}}, config, 24)
    assert performance.snapshot(200) == before
    performance.observe({"type": "state", "paused": False}, config, 30)
    assert performance.snapshot(30) == before
    assert performance.snapshot(31)["elapsed"] == pytest.approx(before["elapsed"] + 1)


def test_invalid_world_command_does_not_leave_fishing_interrupted(monkeypatch):
    async def scenario():
        store._data["fishing"]["dedicated"] = True
        brain = scheduler.Brain()
        monkeypatch.setattr(brain, "_speak", AsyncMock())
        await brain.ensure_dedicated_fishing()
        event = Event(kind="chat", text="去观景台", user_name="鱼友")
        await brain._execute_intent(event, Intent("world", motion="move:lookout"), quick=True)
        assert brain.fishing.phase == "waiting"
        assert brain.world.paused_at is not None
    asyncio.run(scenario())


def test_stunt_waits_for_cooldown_before_resuming_fishing(monkeypatch):
    async def scenario():
        store._data["fishing"]["dedicated"] = True
        brain = scheduler.Brain()
        await brain.ensure_dedicated_fishing()
        brain.likes.ready_at = brain.likes.paused_at + 4
        await brain.play_stunt("bicycle")
        assert brain.fishing.phase == "interacting" and brain.likes.pending
        assert brain.fishing.snapshot()["remaining"] >= 12
    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["跳舞", "倒立", "荡蛛丝", "出门", "下雪", "奶蛙"])
def test_basic_interactions_resume_dedicated_round(text, monkeypatch):
    async def scenario():
        store._data["fishing"].update(dedicated=True, voice=False)
        store._data["world"]["cooldown"] = 0
        brain = scheduler.Brain()
        monkeypatch.setattr(brain, "_speak", AsyncMock())
        await brain.ensure_dedicated_fishing()
        previous = brain.fishing.snapshot()
        event = Event(kind="chat", user_name="互动鱼友", text=text)
        if text == "奶蛙":
            await brain._publish_event(event)
        else:
            await brain._execute_intent(event, local_intent(text), quick=True)
        assert brain.fishing.phase == "interacting"
        assert brain.world.paused_at is None and brain.likes.paused_at is None
        assert brain.runtime_context()["fishing"]["interruption"]
        brain.fishing.tick(brain.fishing.deadline)
        await brain._sync_fishing()
        assert brain.fishing.round_id == previous["round_id"]
        assert brain.fishing.phase == previous["phase"]
        assert brain.world.paused_at is not None
    asyncio.run(scenario())


def test_gift_events_during_gift_show_still_count_and_dive_commands_are_quick():
    async def scenario():
        store._data["fishing"].update(dedicated=True, escape_chance=0)
        brain = scheduler.Brain()
        await brain.ensure_dedicated_fishing()
        brain.fishing.deadline = time.monotonic() - 1
        brain.fishing.tick()
        brain.fishing.fish["pulls"] = 30
        for index in range(2):
            await brain._publish_event(Event(kind="gift", user_id="donor", user_name="礼物鱼友", gift_name="鲜花", gift_count=1, id=str(index)))
        assert brain.fishing.contributors["donor"]["score"] == 10
        assert brain.fishing.paused_at is not None
        brain.gift_show.clear()
        await brain.sync_gift_show()
        assert await brain._publish_event(Event(kind="chat", user_name="跳水鱼友", text="跳水"))
        assert brain.fishing.phase == "diving"
        context = brain.runtime_context()
        assert "放下鱼竿" in context["primary_activity"]
        assert "dive" in context["capabilities"]["fishing"]
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
def test_native_dive_interaction_species_images_and_pause(qt, size):
    scene, state = Scene(*size), StageState()
    state.connected = True
    state.config["stage"] = store.section("stage")
    game = game_at_bite()
    game.config["dedicated"] = True
    game.start_dive(10)
    state.stats["fishingRanking"] = {"species": [{"kind": "whale", "name": "云海蓝鲸", "count": 2}], "encounters": 2}
    samples = set()
    for style in ("tuck", "pike", "twist", "cannonball"):
        game.dive["style"] = style
        for age in (1, 3, 5, 6.4, 10, 19.5, 23):
            snapshot = state.snapshot()
            snapshot.update(fishing=game.snapshot(10 + age), fishing_at=100, animation_time=age)
            image = scene.render(snapshot, age, 100)
            assert not image.isNull()
            samples.add(bytes(image.constBits()))
    assert len(samples) >= 12
    assert len({bytes(image.constBits()) for image in scene.fishing.fish_images.values()}) == len(FISH)
    assert dive_position(4, 700, 330, "tuck", 3)[:2] == pytest.approx(dive_position(4 - 1e-7, 700, 330, "tuck", 3)[:2], abs=.01)
    game._phase("waiting", 40, 25)
    game.interrupt("倒立", 6, 41)
    snapshot.update(fishing=game.snapshot(43), fishing_at=100, reaction={"motion": "handstand", "duration": 6, "started": 98}, reaction_until=104)
    assert not scene.render(snapshot, 2, 100).isNull()
