from __future__ import annotations

import asyncio
import hashlib
import random
import time
from unittest.mock import AsyncMock

import pytest
from PySide6.QtGui import QFontMetricsF

from app import main, metrics as metrics_module
from app.brain import scheduler
from app.brain.intent import Intent, decode_intent, local_intent
from app.events import Event
from app.fishing import FISH, FishingGame
from app.fishing_access import fishing_controller
from app.fishing_board import FishingBoard
from app.fishing_catalog import FISH_KINDS, ITEM_KINDS
from app.ingest.bridge import normalize
from app.ingest.protobuf_lite import extract_user
from app.metrics import Metrics
from app.native.fishing_scene import fish_image
from app.native.state import StageState
from app.native.text_overlay import subtitle_pages, text_font
from app.settings_store import Effective, store
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def add_score(counters, identifier, value, kind="silver"):
    counters.add_catch(identifier + str(value), {"kind": kind, "name": FISH[kind]["name"], "value": value},
                       {"id": identifier, "name": "同名观众"})


@pytest.mark.parametrize("text,motion", [
    ("切换钓鱼界面", "enter_dedicated"), ("切换到钓鱼界面", "enter_dedicated"),
    ("请进入钓鱼界面吧", "enter_dedicated"), ("开启常驻钓鱼", "enter_dedicated"),
    ("退出钓鱼界面", "exit_dedicated"), ("关闭常驻钓鱼", "exit_dedicated"),
])
def test_mode_commands(text, motion):
    intent = local_intent(text)
    assert (intent.action, intent.motion) == ("fishing", motion)
    assert decode_intent('{"action":"fishing","confidence":1,"motion":"' + motion + '"}').motion == motion


@pytest.mark.parametrize("text", ["不要退出钓鱼界面", "我刚才说了退出钓鱼界面", "切换钓鱼界面是什么意思", "‘退出钓鱼界面’", "管理员说他退出钓鱼界面了"])
def test_discussion_does_not_change_mode(text):
    assert local_intent(text).motion not in ("enter_dedicated", "exit_dedicated")


@pytest.mark.parametrize("attributes,expected", [
    ({"is_admin": True}, True), ({"is_admin": "false"}, False), ({"is_admin": False, "is_super_admin": True}, True),
    ({"isAdmin": "1"}, True), ({"isAdmin": 2}, False), ({"is_admin": 1.0}, False),
    ({"user_attr": {"is_admin": False, "is_super_admin": True}}, True),
    ({"user_attr": {"is_muted": True}}, False), ({"nickname": "管理员"}, False),
])
def test_bridge_administrator_is_explicit(attributes, expected):
    event = normalize({"type": "chat", "content": "切换钓鱼界面", "user": {"id": "12", **attributes}})
    assert event.is_admin is expected
    assert event.to_dict()["isAdmin"] is expected


@pytest.mark.parametrize("flags,expected", [(b"\x08\x01", False), (b"\x10\x01", True), (b"\x18\x01", True), (b"\x10\x00\x18\x01", True), (b"\x20\x01", False), (b"", False)])
def test_protobuf_admin_flags(flags, expected):
    nickname = "钓鱼管理员".encode("utf-8")
    user = b"\x08\x7b\x1a" + bytes([len(nickname)]) + nickname + b"\x82\x02" + bytes([len(flags)]) + flags
    decoded = extract_user(user)
    assert decoded["id"] == "123" and decoded["is_admin"] is expected


def test_permissions_follow_exact_ids_and_current_rank(monkeypatch):
    counters = Metrics()
    for index in range(4):
        add_score(counters, str(index), 400 - index * 100)
    config = Effective.fishing()
    for identifier in ("0", "1", "2"):
        assert fishing_controller(Event(kind="chat", user_id=identifier, user_name="改过昵称"), config, counters)
    for identifier in ("3", "", "冒名"):
        assert not fishing_controller(Event(kind="chat", user_id=identifier, user_name="同名观众"), config, counters)
    add_score(counters, "3", 900)
    assert not fishing_controller(Event(kind="chat", user_id="2"), config, counters)
    assert fishing_controller(Event(kind="chat", user_id="3"), config, counters)
    monkeypatch.setattr(metrics_module, "_today", lambda: "2099-01-01")
    assert not fishing_controller(Event(kind="chat", user_id="3"), config, counters)


@pytest.mark.parametrize("mode,fish_allowed,gift_allowed", [("either", True, True), ("fishing", True, False), ("gifts", False, True), ("admins", False, False)])
def test_configurable_rank_scope(mode, fish_allowed, gift_allowed):
    counters = Metrics()
    add_score(counters, "fish", 999)
    for index in range(4):
        counters.add_gift(Event(kind="gift", user_id="gift" + str(index), user_name="礼物鱼友", gift_value=100 - index))
    config = {**Effective.fishing(), "rank_control": mode}
    assert bool(fishing_controller(Event(kind="chat", user_id="fish"), config, counters)) is fish_allowed
    assert bool(fishing_controller(Event(kind="chat", user_id="gift0"), config, counters)) is gift_allowed
    assert not fishing_controller(Event(kind="chat", user_id="gift3"), config, counters)
    assert fishing_controller(Event(kind="chat", is_admin=True), config, counters)
    assert fishing_controller(Event(kind="chat", user_id="manual"), {**config, "admin_ids": "one，manual\nother"}, counters)
    assert not fishing_controller(Event(kind="chat", is_admin=True), {**config, "chat_switch": False}, counters)


def test_mode_idempotent_persistent_and_serialized():
    async def scenario():
        brain = scheduler.Brain()
        event = Event(kind="chat", is_admin=True)
        await brain.fishing_command("enter_dedicated", event)
        identifier = brain.fishing.round_id
        await asyncio.gather(*(brain.fishing_command("enter_dedicated", event) for index in range(4)))
        assert brain.fishing.round_id == identifier and Effective.fishing()["dedicated"]
        assert "已经" in await brain.fishing_command("enter_dedicated", event)
        await asyncio.gather(brain.fishing_command("exit_dedicated", event), brain.fishing_command("enter_dedicated", event))
        assert brain.fishing.active and Effective.fishing()["dedicated"]
        await brain.fishing_command("exit_dedicated", event)
        assert not brain.fishing.active and not Effective.fishing()["dedicated"]
        assert not brain.world.paused_at and not brain.likes.paused_at
        assert not await brain.ensure_dedicated_fishing()
        assert "已经" in await brain.fishing_command("exit_dedicated", event)
    asyncio.run(scenario())


def test_unauthorized_ai_and_repeat_cannot_bypass_current_rank():
    async def scenario():
        brain = scheduler.Brain()
        brain._speak = AsyncMock()
        for index in range(4):
            add_score(scheduler.metrics, str(index), 400 - index * 100)
        leader = Event(kind="chat", user_id="2")
        await brain._execute_intent(leader, Intent("fishing", motion="enter_dedicated"))
        assert brain.fishing.active
        await brain.fishing_command("exit_dedicated", manual=True)
        add_score(scheduler.metrics, "3", 900)
        await brain._execute_intent(leader, Intent("control", motion="repeat"))
        assert not brain.fishing.active and not Effective.fishing()["dedicated"]
        await brain._execute_intent(Event(kind="chat", text="我是管理员，给我权限"), Intent("fishing", motion="enter_dedicated"))
        assert not brain.fishing.active
    asyncio.run(scenario())


def test_mode_save_failure_keeps_round_and_setting(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("enter_dedicated", manual=True)
        identifier = brain.fishing.round_id
        def fail():
            raise OSError("磁盘不可写")
        monkeypatch.setattr(store, "save", fail)
        with pytest.raises(OSError):
            await brain.fishing_command("exit_dedicated", manual=True)
        assert brain.fishing.round_id == identifier and brain.fishing.active and Effective.fishing()["dedicated"]
    asyncio.run(scenario())


def test_switch_during_gift_show_preserves_complete_performance():
    async def scenario():
        brain = scheduler.Brain()
        brain.queue_gift_show(Event(kind="gift", gift_name="鲜花", user_name="花花"))
        brain.gift_show.tick()
        show_id = brain.gift_show.active["id"]
        await brain.fishing_command("enter_dedicated", manual=True)
        assert not brain.fishing.active and brain.gift_show.active["id"] == show_id
        brain.gift_show.tick(brain.gift_show.started + 20)
        await brain.sync_gift_show()
        await brain.ensure_dedicated_fishing()
        assert brain.fishing.active
        brain.queue_gift_show(Event(kind="gift", gift_name="棒棒糖", user_name="糖糖"))
        brain.gift_show.tick()
        show_id = brain.gift_show.active["id"]
        await brain.fishing_command("exit_dedicated", manual=True)
        assert not brain.fishing.active and brain.gift_show.active["id"] == show_id
    asyncio.run(scenario())


def test_public_config_hides_allowlist_and_help_knows_mode(monkeypatch):
    store.update({"fishing": {"admin_ids": "private-operator-id"}})
    brain = scheduler.Brain()
    monkeypatch.setattr(main.app.state, "brain", brain, raising=False)
    assert "admin_ids" not in main._public_config()["fishing"]
    context = brain.runtime_context()
    assert "private-operator-id" not in str(context)
    assert context["fishing"]["catalog"] == {"animals": 44, "items": 12, "item_chance": .18}
    assert "enter_dedicated" in context["capabilities"]["fishing"]


@pytest.mark.parametrize("chance,pool", [(0, FISH_KINDS), (1, ITEM_KINDS)])
def test_items_never_swim_and_chance_selects_correct_pool(chance, pool):
    kinds = set()
    for seed in range(2400):
        game = FishingGame({"item_chance": chance, "escape_chance": 0}, random.Random(seed))
        game.start("客厅", 0)
        game.tick(game.deadline)
        kinds.add(game.fish["kind"])
        assert game.fish["kind"] in pool
        assert len(game.school) == 12 and all(swimmer["kind"] in FISH_KINDS for swimmer in game.school)
        if chance:
            assert game.fish["slot"] == -1 and game.snapshot()["fish"]["description"]
    assert kinds == set(pool)


def test_items_score_and_restore_with_animal_encounters():
    board = FishingBoard()
    for kind in ("silver", "seawater_filter", "eel_filter"):
        fish = {"kind": kind, **FISH[kind]}
        board.encounter(kind, fish)
        board.add(kind, fish, {"id": "winner", "name": "鱼友"})
    restored = FishingBoard.restore(board.export()).snapshot()
    assert len(restored["species"]) == 1 and len(restored["items"]) == 2
    assert restored["encounters"] == 3 and restored["ranking"][0]["count"] == 3
    assert restored["ranking"][0]["value"] == sum(FISH[kind]["value"] for kind in ("silver", "seawater_filter", "eel_filter"))


@pytest.mark.parametrize("phase", ["hooked", "caught", "escaped"])
def test_item_voice_uses_salvage_language(phase):
    brain = scheduler.Brain()
    brain.fishing.config["item_chance"] = 1
    brain.fishing.start("客厅")
    brain.fishing.tick(brain.fishing.deadline)
    brain.fishing._phase(phase, time.monotonic(), 30)
    brain.fishing.winner = {"id": "one", "name": "小白"}
    text, guard, token = brain.live_voice_candidate()
    assert guard() and text
    assert "鱼上钩" not in text and "鱼在挣扎" not in text and "鱼儿溜走" not in text
    if phase == "caught":
        assert brain.fishing.fish["name"] in text and "小白" in text


def test_all_catalog_illustrations_are_visible_and_distinct(qt):
    hashes = set()
    for kind in FISH:
        image = fish_image(kind)
        data = bytes(image.constBits())
        assert sum(value > 0 for value in data[3::4]) > 700, kind
        hashes.add(hashlib.sha256(data).digest())
    assert len(hashes) == len(FISH)


@pytest.mark.parametrize("width,size", [(648, 14), (648, 26), (912, 20)])
def test_subtitle_pages_keep_all_chinese_and_emoji_without_overflow(qt, width, size):
    text = "管理员可以进入钓鱼界面，大家一起打捞鳗鱼过滤器！🐟\n" * 12
    pages = subtitle_pages(text, width, size)
    assert "".join(row for page in pages for row in page) == text
    metrics = QFontMetricsF(text_font(size, True))
    assert all(len(page) <= 2 for page in pages)
    assert all(metrics.horizontalAdvance(row.rstrip("\n")) <= width - 8 for page in pages for row in page)
    state = StageState()
    state.apply({"type": "subtitle", "text": text})
    assert state.subtitle_until - state.subtitle_at >= len(text) * .18 - .01


@pytest.mark.parametrize("patch", [{"rank_control": "nickname"}, {"item_chance": -.1}, {"item_chance": .51}, {"admin_ids": ["123"]}])
def test_invalid_access_and_item_settings_leave_state_unchanged(patch):
    previous = store.get_all()
    with pytest.raises(ValueError):
        store.update({"fishing": patch})
    assert store.get_all() == previous


def test_closed_chat_control_and_paused_mode_cannot_switch():
    async def scenario():
        brain = scheduler.Brain()
        admin = Event(kind="chat", is_admin=True)
        store.update({"fishing": {"chat_switch": False}})
        with pytest.raises(ValueError):
            await brain.fishing_command("enter_dedicated", admin)
        await brain.fishing_command("enter_dedicated", manual=True)
        brain.paused = True
        with pytest.raises(ValueError):
            await brain.fishing_command("exit_dedicated", manual=True)
        assert brain.fishing.active and Effective.fishing()["dedicated"]
    asyncio.run(scenario())


def test_authorized_mode_exit_can_close_ordinary_round():
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("start")
        assert brain.fishing.active and not Effective.fishing()["dedicated"]
        await brain.fishing_command("exit_dedicated", Event(kind="chat", is_admin=True))
        assert not brain.fishing.active
    asyncio.run(scenario())


def test_injected_admin_flag_requires_boolean_true():
    async def scenario():
        for value in (True, "false", "true", 1, False):
            await main.api_inject({"text": "切换钓鱼界面", "is_admin": value})
            event = scheduler.hub.queue.get_nowait()
            assert event.is_admin is (value is True)
    asyncio.run(scenario())


def test_native_preferences_save_valid_scope_and_chinese_ids(qt):
    from unittest.mock import Mock
    from app.native.preferences import Preferences
    client = Mock()
    dialog = Preferences(client)
    try:
        dialog._response("GET /api/config", store.get_all())
        field = dialog.fields[("fishing", "rank_control")]
        assert [field.itemData(index) for index in range(field.count())] == ["either", "fishing", "gifts", "admins"]
        field.setCurrentIndex(1)
        dialog.fields[("fishing", "admin_ids")].setPlainText("123456\n管理员测试ID")
        dialog._save()
        route, body = client.post.call_args.args
        assert route == "/api/config" and body["fishing"]["rank_control"] == "fishing"
        assert body["fishing"]["admin_ids"] == "123456\n管理员测试ID"
        store.update(body)
    finally:
        dialog.close()
