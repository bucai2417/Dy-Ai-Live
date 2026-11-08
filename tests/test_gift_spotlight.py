from __future__ import annotations

import asyncio
import copy

import pytest
from PySide6.QtGui import QImage

from app.brain import scheduler
from app.events import Event
from app.gift_show import GiftShow
from app.gifts import GiftBoard
from app.native.avatars import AvatarLoader
from app.native.gift_scene import gift_view
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS
from tests.test_fishing_activities import waiting_game
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def test_tribute_uses_actual_daily_counts_and_merges_without_losing_first():
    board = GiftBoard()
    shows = GiftShow()
    first = Event(kind="gift", user_id="one", user_name="星光鱼友", gift_name="鲜花", gift_count=2, avatar="https://example.invalid/first.png")
    board.add(first.to_dict())
    tribute = board.tribute(first.to_dict())
    assert tribute == {"first": True, "daily_count": 2}
    shows.enqueue(first, tribute=tribute)
    following = Event(kind="gift", user_id="one", user_name="星光鱼友", gift_name="鲜花", gift_count=3, avatar="https://example.invalid/new.png")
    board.add(following.to_dict())
    assert not board.tribute(following.to_dict())["first"]
    shows.enqueue(following, tribute=board.tribute(following.to_dict()))
    shows.tick(0)
    assert shows.active["count"] == 5
    assert shows.active["tribute"] == {"first": True, "daily_count": 5}
    assert shows.active["user"]["avatar"] == following.avatar
    board.clear()
    board.add(following.to_dict())
    assert board.tribute(following.to_dict()) == {"first": True, "daily_count": 3}


def test_net_retains_avatar_and_tribute_through_queue_merge():
    game = waiting_game()
    game.add_net_gift(1, 5, {"id": "first", "name": "先到的鱼友"}, "first", now=5)
    donor = {"id": "two", "name": "星光鱼友", "avatar": "https://example.invalid/donor.png",
             "tribute": {"first": True, "daily_count": 1}}
    game.add_net_gift(1, 5, donor, "second", now=6)
    game.add_net_gift(1, 5, {**donor, "tribute": {"first": False, "daily_count": 2}}, "third", now=7)
    assert len(game.net_queue) == 1
    assert game.net_queue[0]["user"]["tribute"] == {"first": True, "daily_count": 2}
    assert donor["tribute"]["daily_count"] == 1
    game.tick(15)
    game.tick(23)
    game.tick(24)
    assert game.net["user"]["avatar"] == donor["avatar"]
    assert game.net["count"] == 2


def test_thanks_holds_visible_final_pose_until_finished_and_freezes_on_pause():
    shows = GiftShow()
    shows.enqueue(Event(kind="gift", id="first", gift_name="鲜花", user_name="第一位鱼友"))
    shows.enqueue(Event(kind="gift", id="second", gift_name="鲜花", user_name="第二位鱼友"))
    shows.tick(0)
    assert shows.tick(10, hold=True)
    assert shows.holding and shows.active["id"] == "first"
    shows.tick(16, hold=True)
    snapshot = shows.snapshot(16)
    assert snapshot["elapsed"] == pytest.approx(9.8)
    assert gift_view({"gift_show": snapshot, "gift_show_at": 16}, 50)["elapsed"] == pytest.approx(9.8)
    shows.pause(True, 16)
    assert not shows.tick(100, hold=True)
    shows.pause(False, 116)
    assert shows.tick(117, hold=False)
    assert not shows.holding and shows.active["id"] == "second"


def test_thanks_hold_has_a_time_limit_and_requires_current_gift_voice():
    async def scenario():
        brain = scheduler.Brain()
        brain.queue_gift_show(Event(kind="gift", id="first", gift_name="鲜花"))
        brain.tick_gift_show(0)
        future = asyncio.get_running_loop().create_future()
        brain._live_voice_task = future
        brain._live_voice_guard = lambda: True
        brain._gift_voice_id = "first"
        assert brain.tick_gift_show(10) and brain.gift_show.holding
        assert brain.tick_gift_show(30) and brain.gift_show.active is None
        future.cancel()
        brain.queue_gift_show(Event(kind="gift", id="second", gift_name="鲜花"))
        brain.tick_gift_show(31)
        assert brain.tick_gift_show(43) and brain.gift_show.active is None
    asyncio.run(scenario())


def test_net_avatar_is_loaded_even_when_gift_bubbles_disabled(qt, monkeypatch):
    state = StageState()
    state.config["stage"] = {"show_ballpit": False}
    state.fishing = {"net": {"user": {"avatar": "https://example.invalid/net.png"}}}
    loader = AvatarLoader(state)
    monkeypatch.setattr(loader, "_start", lambda: None)
    try:
        loader.refresh()
        assert loader.pending == ["https://example.invalid/net.png"]
        image = QImage(16, 16, QImage.Format.Format_ARGB32)
        image.fill(0xff66aacc)
        state.set_avatar(loader.pending[0], image)
        state._rank({"value": [], "count": []}, 0)
        assert state.avatar_images[loader.pending[0]] == image
    finally:
        loader.close()


def test_gift_event_enriches_exact_sender_and_voice_without_duplicate_thanks():
    async def scenario():
        brain = scheduler.Brain()
        event = Event(kind="gift", id="first", user_id="supporter", user_name="小海风", gift_name="鲜花", gift_count=1, gift_value=10)
        await brain._publish_event(event)
        show = brain.gift_show.active
        assert show["user"]["name"] == "小海风" and show["tribute"]["first"]
        candidate = brain.live_voice_candidate()
        assert candidate and "小海风" in candidate[0]
        assert "第一" in candidate[0]
        await brain._publish_event(event)
        assert not brain.gift_show.pending
        assert scheduler.metrics.gift_board.tribute(event.to_dict())["daily_count"] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(1280, 720), (720, 1280)])
def test_gift_and_net_cards_render_long_chinese_names_and_freeze(qt, size):
    state = StageState()
    state.config["stage"] = copy.deepcopy(DEFAULTS["stage"])
    state.config["stage"]["show_ballpit"] = False
    avatar = QImage(64, 64, QImage.Format.Format_ARGB32)
    avatar.fill(0xff4fabc8)
    state.avatar_images["avatar"] = avatar
    scene = Scene(*size)
    shows = GiftShow()
    event = Event(kind="gift", user_id="supporter", user_name="喜欢看海的超长中文昵称鱼友", gift_name="鲜花", gift_count=5, avatar="avatar")
    shows.enqueue(event, tribute={"first": False, "daily_count": 25})
    shows.tick(0)
    shows.pause(True, 4)
    snapshot = state.snapshot()
    snapshot.update(gift_show=shows.snapshot(), gift_show_at=0)
    gift = scene.render(snapshot, 0, 0)
    assert gift == scene.render(snapshot, 0, 40)
    game = waiting_game()
    game.add_net_gift(5, 1000, {"id": event.user_id, "name": event.user_name, "avatar": "avatar"}, "net", now=5)
    game.tick(15)
    snapshot.update(gift_show={}, fishing=game.snapshot(18), fishing_at=0)
    net = scene.render(snapshot, 0, 0)
    assert not net.isNull() and net != gift
