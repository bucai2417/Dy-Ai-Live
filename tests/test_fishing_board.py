from __future__ import annotations

import asyncio
import copy
import datetime as dt
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.brain import scheduler
from app.brain.capabilities import invitation_options
from app.brain.llm import LLM
from app.fishing_board import FishingBoard
from app.hub import Hub
from app.like_goal import LikeGoal
from app.live_history import LiveHistory
from app.metrics import Metrics
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS, store
from tests.test_fishing import game_at_bite
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime
from tests.test_speech_idle import quiet_brain


def fish(value=180, kind="perch", name="青纹鲈鱼"):
    return {"value": value, "kind": kind, "name": name}


def test_rank_accumulates_catches_with_stable_identity_and_ties():
    board = FishingBoard()
    board.add("one", fish(180), {"id": "first", "name": "鱼友"})
    board.add("two", fish(500, "gold", "鎏金龙鱼"), {"id": "second", "name": "鱼友"})
    board.add("three", fish(320), {"id": "first", "name": "改名后的鱼友"})
    ranking = board.snapshot()["ranking"]
    assert [record["key"] for record in ranking] == ["id:first", "id:second"]
    assert ranking[0]["name"] == "改名后的鱼友"
    assert ranking[0]["value"] == 500 and ranking[0]["count"] == 2 and ranking[0]["best"] == 320
    assert ranking[1]["kind"] == "gold"
    assert not board.add("three", fish(9000), {"id": "first"})
    assert board.snapshot()["ranking"] == ranking


def test_community_catch_is_recorded_without_awarding_a_fake_user():
    board = FishingBoard()
    board.add("community", fish(1200), None)
    assert board.snapshot()["ranking"] == []
    assert board.snapshot()["community"] == {"value": 1200, "count": 1}
    assert not board.add("community", fish(1200), None)


def test_ranking_survives_restart_and_legacy_statistics(tmp_path):
    path = tmp_path / "中文鱼获.sqlite3"
    first = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    first.metrics.add_catch("round", fish(320), {"name": "首拉观众"})
    first.metrics.add_catch("shared", fish(760), None)
    first.write(*first.capture())
    restored = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    assert restored.restore()
    assert restored.metrics.fishing_ranking() == first.metrics.fishing_ranking()
    assert not restored.metrics.add_catch("round", fish(320), {"name": "首拉观众"})
    day, data = first.capture()
    del data["fishing_board"]
    first.write(day, data)
    legacy = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    assert legacy.restore() and legacy.metrics.fishing_ranking()["ranking"] == []


def test_corrupt_fishing_records_do_not_partially_restore_statistics(tmp_path):
    first = LiveHistory(tmp_path / "invalid.sqlite3", Metrics(), Hub(), LikeGoal())
    first.metrics.add_tokens(100, 50)
    first.metrics.add_catch("round", fish(), {"name": "首拉观众"})
    day, data = first.capture()
    data["fishing_board"]["records"]["name:首拉观众"]["value"] = -1
    first.write(day, data)
    restored = LiveHistory(first.path, Metrics(), Hub(), LikeGoal())
    assert not restored.restore()
    assert restored.metrics.tokens_in == 0 and not restored.metrics.fishing_board.records


def test_settlement_is_once_and_does_not_repeat_across_midnight(isolated_runtime, monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.fishing = game_at_bite()
        brain.fishing.pull(100, {"id": "viewer", "name": "获胜者"}, now=9)
        await brain._sync_fishing()
        assert scheduler.metrics.fishing_ranking()["catches"] == 0
        brain.fishing.tick(11)
        await brain._sync_fishing()
        await brain._sync_fishing()
        board = scheduler.metrics.fishing_ranking()
        assert board["ranking"][0]["value"] == brain.fishing.fish["value"]
        assert board["ranking"][0]["count"] == 1
        assert brain.runtime_context()["fishing"]["leaderboard"]["ranking"][0]["name"] == "获胜者"
        today = dt.date

        class Tomorrow(today):
            @classmethod
            def today(cls):
                return today.today() + dt.timedelta(days=1)

        monkeypatch.setattr(dt, "date", Tomorrow)
        await brain._sync_fishing()
        assert scheduler.metrics.fishing_ranking()["catches"] == 0
    asyncio.run(scenario())


def test_stats_reconnect_and_session_reset_keep_rank(isolated_runtime):
    with TestClient(main.app) as client:
        main.metrics.add_catch("saved", fish(888), {"name": "中文榜首"})
        with client.websocket_connect("/ws") as socket:
            hello = socket.receive_json()
            assert hello["stats"]["fishingRanking"]["ranking"][0]["name"] == "中文榜首"
        assert client.post("/api/session/boot").json()["ok"]
        context = client.get("/api/ai/context").json()["context"]
        assert context["fishing"]["leaderboard"]["ranking"][0]["value"] == 888
        assert "全屏" in context["interface"]["gift_bubbles"]


def test_idle_invites_use_editable_lines_rotate_and_obey_cooldown(isolated_runtime, monkeypatch):
    store._data["templates"].update(enabled=True, idle=True, prefer_llm=False)
    store._data["lines"]["idle_fishing"] = ["有点想去钓鱼了，你可以发钓鱼。咬钩后一起拉钩！"]
    brain = quiet_brain(isolated_runtime)
    spoken = AsyncMock()
    monkeypatch.setattr(brain, "_speak", spoken)

    async def scenario():
        assert await brain._idle_once()
        assert spoken.await_args.args[0] == store.lines("idle_fishing")[0]
        assert not brain.fishing.active
        assert brain._idle_invite_index == 1
        brain.last_speak_at = 0
        assert await brain._idle_once()
        assert brain._idle_invite_index == 1
        brain.last_speak_at = 0
        brain._last_idle_invite_at = 0
        assert await brain._idle_once()
        assert spoken.await_args.args[0] in store.lines("idle_scene")
        assert brain._idle_invite_index == 2
        await brain.fishing_command("start", manual=True)
        brain.last_speak_at = 0
        assert not await brain._idle_once()
    asyncio.run(scenario())


def test_invites_skip_disabled_features_and_cancel_if_settings_change(isolated_runtime, monkeypatch):
    store._data["fishing"]["enabled"] = False
    store._data["templates"].update(enabled=True, idle=True, prefer_llm=True)
    brain = quiet_brain(isolated_runtime)
    assert "fishing" not in dict(invitation_options(brain.runtime_context()))
    monkeypatch.setattr(LLM, "active", property(lambda self: True))

    async def generate(prompt, **kwargs):
        assert "换个场景" in prompt
        store._data["world"]["chat_control"] = False
        return "我们换个场景吧。"

    spoken = AsyncMock()
    monkeypatch.setattr(brain, "_llm_line", generate)
    monkeypatch.setattr(brain, "_speak", spoken)
    assert not asyncio.run(brain._idle_once())
    spoken.assert_not_awaited()


def test_online_invitation_receives_real_rules_and_rank(isolated_runtime, monkeypatch):
    store._data["templates"].update(enabled=True, idle=True, prefer_llm=True)
    brain = quiet_brain(isolated_runtime)
    scheduler.metrics.add_catch("actual", fish(450), {"name": "今天的榜首"})
    monkeypatch.setattr(LLM, "active", property(lambda self: True))

    async def generate(prompt, **kwargs):
        assert "钓鱼" in prompt and "不要自行开局" in prompt
        context = brain.llm._context_block()
        assert "今天的榜首" in context and "450" in context and "贡献" in context
        return "有点想去钓鱼了，你可以发钓鱼。"

    spoken = AsyncMock()
    monkeypatch.setattr(brain, "_llm_line", generate)
    monkeypatch.setattr(brain, "_speak", spoken)
    assert asyncio.run(brain._idle_once())
    assert "钓鱼" in spoken.await_args.args[0] and not brain.fishing.active


@pytest.mark.parametrize("size", [(720, 1280), (1280, 720)])
def test_gift_bubble_is_visible_over_hud_outside_scene(qt, size):
    state = StageState()
    state.config["stage"] = copy.deepcopy(DEFAULTS["stage"])
    state.apply({"type": "event", "event": {"kind": "gift", "userName": "全屏气泡", "giftCount": 1}})
    scene = Scene(*size)
    scene.render(state.snapshot(), 0, 100)
    bubble = next(iter(scene.gifts.bubbles.values()))
    bubble.x, bubble.y, bubble.vx, bubble.vy = 100, 50, 0, 0
    scene.gifts.last_time = 100
    with_bubble = scene.render(state.snapshot(), 0, 100)
    state.config["stage"]["show_ballpit"] = False
    without_bubble = scene.render(state.snapshot(), 0, 100)
    assert with_bubble.copy(55, 5, 90, 90) != without_bubble.copy(55, 5, 90, 90)
    assert scene.layout.effects[1] > 100


@pytest.mark.parametrize("seconds", [0, 29, 3601])
def test_invitation_interval_validation(seconds):
    with pytest.raises(ValueError):
        store.update({"idle": {"invite_seconds": seconds}})
