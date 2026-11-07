from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, music, playlist
from app.brain import scheduler
from app.brain.llm import LLM
from app.events import Event
from app.metrics import Metrics
from app.music_policy import ai_allowed, blocked_reason, query_reason
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import Effective, store
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


@pytest.mark.parametrize("title", [
    "义勇军进行曲", "義勇軍進行曲（纯音乐）", "义 勇 军·进 行 曲", "强军战歌 DJ版",
    "播放军歌", "军中绿花", "当兵的人", "国\u200b歌", "ＮＡＴＩＯＮＡＬ ＡＮＴＨＥＭ",
    "March of the Volunteers", "三大纪律八项注意", "歌唱祖国", "%E5%9B%BD%E6%AD%8C",
])
def test_builtin_music_restrictions(title):
    assert blocked_reason(title)


@pytest.mark.parametrize("key", ["by", "album", "genre", "category", "tags", "aliases", "file", "request_query"])
def test_metadata_restrictions(key):
    assert blocked_reason({"title": "无关标题", key: "军歌精选"})


def test_custom_rules_and_confirmed_playlist():
    store._data["music"].update(ai_allowlist="晴天 | 周杰伦\n稻香\n义勇军进行曲", blocked_keywords="测试禁播，测试专辑")
    assert ai_allowed("周杰伦 晴天")
    assert ai_allowed({"title": "晴天", "by": "周杰伦"})
    assert not ai_allowed({"title": "晴天", "by": "其他歌手"})
    assert not ai_allowed({"title": "晴天 remix", "by": "周杰伦"})
    assert not ai_allowed("义勇军进行曲")
    assert blocked_reason({"title": "普通歌", "album": "测试专辑"})
    assert query_reason("https://example.invalid/song.mp3")
    assert query_reason("D:\\music\\song.mp3")
    assert not blocked_reason("晴天")


def test_request_stops_before_resolving_and_rechecks_result(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        resolver = AsyncMock(return_value={"title": "义勇军进行曲", "by": "合唱团", "url": "test"})
        monkeypatch.setattr(scheduler, "music_resolve", resolver)
        assert not (await brain._enqueue("播放国歌", "观众"))[0]
        assert not (await brain.ai_song("晴天"))[0]
        resolver.assert_not_awaited()
        assert not (await brain._enqueue("未知别名", "观众"))[0]
        assert not playlist.queue.pending()
        store._data["music"]["ai_allowlist"] = "晴天 | 周杰伦"
        resolver.return_value = {"title": "晴天", "by": "错误歌手", "url": "test"}
        assert not (await brain.ai_song("晴天"))[0]
        resolver.return_value["by"] = "周杰伦"
        assert (await brain.ai_song("晴天"))[0]
        assert playlist.queue.pending()[0].from_ai

    asyncio.run(scenario())


def test_policy_changed_while_resolving(monkeypatch):
    async def scenario():
        async def resolve(*args, **kwargs):
            store._data["music"]["blocked_keywords"] = "晴天"
            return {"title": "晴天", "url": "test"}

        monkeypatch.setattr(scheduler, "music_resolve", resolve)
        assert not (await scheduler.Brain()._enqueue("晴天", "观众"))[0]
        assert not playlist.queue.pending()

    asyncio.run(scenario())


def test_queue_rechecks_existing_current_pending_and_resume():
    queue = playlist.Queue()
    assert not queue.add(playlist.make_track({"title": "国歌"}))[0]
    for title in ("晴天", "稻香", "夜曲"):
        assert queue.add(playlist.make_track({"title": title, "url": "test"}))[0]
    assert queue.next_track().title == "晴天"
    queue.pause()
    store._data["music"]["blocked_keywords"] = "晴天,稻香"
    assert queue.resume().title == "夜曲"
    assert not queue.pending()
    store._data["music"]["blocked_keywords"] += ",夜曲"
    assert queue.enforce_policy() and queue.current is None and not queue.playing


def test_ai_queued_track_removed_when_allowlist_cleared():
    store._data["music"]["ai_allowlist"] = "晴天"
    track = playlist.make_track({"title": "晴天", "url": "test"}, from_ai=True)
    assert playlist.queue.add(track)[0]
    store._data["music"]["ai_allowlist"] = ""
    assert playlist.queue.next_track() is None


def test_resolver_skips_blocked_candidates_and_cache(monkeypatch):
    async def scenario():
        monkeypatch.setattr(music, "_CACHE", {})
        candidates = AsyncMock(return_value=[
            {"title": "别名", "album": "军歌", "url": "blocked", "referer": ""},
            {"title": "晴天", "url": "allowed", "referer": ""}])
        probe = AsyncMock(return_value=400000)
        monkeypatch.setattr(music, "_netease_candidates", candidates)
        monkeypatch.setattr(music, "_probe", probe)
        track = await music.resolve("晴天", providers=["netease"])
        assert track["url"] == "allowed"
        probe.assert_awaited_once_with("allowed", "")
        store._data["music"]["blocked_keywords"] = "晴天"
        assert await music.resolve("晴天", providers=["netease"]) is None

    asyncio.run(scenario())


def test_local_library_search_and_stream_apply_restrictions(monkeypatch, tmp_path):
    monkeypatch.setenv("MUSIC_DIR", str(tmp_path))
    monkeypatch.setattr(music, "_lib", {"at": 0, "tracks": []})
    (tmp_path / "军歌精选").mkdir()
    (tmp_path / "军歌精选" / "普通名字.wav").write_bytes(b"test")
    (tmp_path / "晴天.wav").write_bytes(b"test")
    assert music.find_local("普通名字") is None
    forbidden = base64.urlsafe_b64encode("军歌精选/普通名字.wav".encode()).decode().rstrip("=")
    assert music.local_path(forbidden) is None
    with TestClient(main.app) as client:
        rows = client.get("/api/music/local").json()["tracks"]
        assert [row["title"] for row in rows] == ["晴天"]
        assert client.get(f"/api/music/stream/{forbidden}").status_code == 403
        assert client.get("/api/music/search", params={"q": "国歌"}).status_code == 400
        result = client.post("/api/music/request", json={"query": "国歌"}).json()
        assert not result["ok"] and "歌曲规则" in result["msg"]
        assert not client.post("/api/music/ai", json={"query": "晴天"}).json()["ok"]


def test_config_change_stops_forbidden_playback_and_refreshes_price():
    with TestClient(main.app) as client:
        main.metrics.add_tokens(1000000, 500000)
        playlist.queue.add(playlist.make_track({"title": "晴天", "url": "test"}))
        playlist.queue.next_track()
        result = client.post("/api/config", json={"music": {"blocked_keywords": "晴天"},
                                                "llm": {"price_in": 3, "price_out": 10},
                                                "stage": {"show_tokens": False}})
        assert result.json()["ok"]
        assert playlist.queue.current is None
        assert client.get("/api/stats").json()["stats"]["cost"] == 8
        assert not Effective.stage()["show_tokens"]


@pytest.mark.parametrize("paused,enabled,expected", [(False, True, 2), (True, True, 0), (False, False, 0)])
def test_sunglasses_gift_without_show_is_immediate_and_repeatable(monkeypatch, paused, enabled, expected):
    async def scenario():
        brain = scheduler.Brain()
        brain.paused = paused
        store._data["gift_show"]["enabled"] = False
        store._data["interact"]["enabled"] = enabled
        state = StageState()
        state.config["stage"] = {"scene_reactions": True}
        broadcast = AsyncMock(side_effect=lambda message, **kwargs: state.apply(message))
        monkeypatch.setattr(scheduler.hub, "broadcast", broadcast)
        await brain._publish_event(Event(kind="gift", gift_name="小心心", user_name="首位", gift_value=1))
        for index in range(2):
            await brain._publish_event(Event(kind="gift", gift_name="墨镜", user_name="观众", gift_value=20))
        messages = [call.args[0] for call in broadcast.call_args_list]
        assert len([item for item in messages if item["type"] == "costume"]) == expected
        if expected:
            assert state.costume == "cool" and state.celebration_until == 0
            await brain._publish_event(Event(kind="gift", gift_name="小心心", user_name="新榜首", gift_value=100))
            assert state.celebration_until == 0

    asyncio.run(scenario())


def test_singing_and_gift_song_refusal_do_not_call_model(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        speak, classify = AsyncMock(), AsyncMock()
        monkeypatch.setattr(brain, "_speak", speak)
        monkeypatch.setattr(brain.llm, "classify_intent", classify)
        await brain.handle(Event(kind="chat", text="给大家唱一下国歌"))
        classify.assert_not_awaited()
        assert "歌曲规则" in speak.call_args.args[0]
        assert await brain._maybe_gift_song(Event(kind="gift", text="播放军歌", gift_value=100, user_name="观众"))
        assert "谢谢观众的礼物" in speak.call_args.args[0]
        assert not playlist.queue.pending()

    asyncio.run(scenario())


@pytest.mark.parametrize("usage,estimated", [
    ({"prompt_tokens": 0, "completion_tokens": 0}, False),
    ({"prompt_tokens": 12}, True), (None, True),
    ({"prompt_tokens": -1, "completion_tokens": "bad"}, True),
])
def test_token_usage_preserves_zero_and_marks_missing_fields(usage, estimated):
    counters = Metrics()
    counters.add_usage(usage, "输入测试", "输出测试")
    assert counters.estimated is estimated and counters.llm_calls == 1
    if not estimated:
        assert counters.tokens_in == counters.tokens_out == 0
    elif usage and usage.get("prompt_tokens") == 12:
        assert counters.tokens_in == 12 and counters.tokens_out > 0


@pytest.mark.parametrize("usage,estimated", [({"prompt_tokens": 0, "completion_tokens": 0}, False), ({}, True)])
def test_classifier_counts_real_zero_or_estimate(monkeypatch, usage, estimated):
    async def scenario():
        counters = Metrics()
        monkeypatch.setattr("app.metrics.metrics", counters)
        model = LLM()
        monkeypatch.setattr(model, "_cfg", lambda: {"base_url": "https://test.invalid", "api_key": "test", "model": "test"})
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"action":"chat"}'}}], "usage": usage}))
        async with httpx.AsyncClient(transport=transport) as client:
            monkeypatch.setattr(model, "_ensure_client", lambda: client)
            await model.classify_intent("你好")
        assert counters.llm_calls == 1 and counters.estimated is estimated
        assert bool(counters.tokens_in) is estimated

    asyncio.run(scenario())


@pytest.mark.parametrize("width,height", [(720, 1280), (1280, 720)])
def test_token_panel_is_independent_and_handles_large_numbers(qt, width, height, monkeypatch):
    monkeypatch.setattr("app.native.state.time.monotonic", lambda: 100.0)
    state = StageState()
    state.config["stage"] = {"show_datapods": False, "show_tokens": True}
    state.stats = {"tokens": 1234567890, "tokensIn": 1234567800, "tokensOut": 90,
                   "cost": 12345678, "estimated": True}
    scene = Scene(width, height)
    enabled = scene.render(state.snapshot(), 0, 100)
    token_rect = scene.layout.tokens
    state.config["stage"]["show_tokens"] = False
    disabled = scene.render(state.snapshot(), 0, 100)
    assert enabled.copy(*token_rect) != disabled.copy(*token_rect)
    assert enabled.copy(0, 0, width, 70) == disabled.copy(0, 0, width, 70)
    assert enabled.copy(*scene.layout.effects) == disabled.copy(*scene.layout.effects)


@pytest.mark.parametrize("price", [-1, float("inf"), float("nan"), "免费"])
def test_invalid_prices_cannot_be_saved(price):
    with pytest.raises(ValueError):
        store.update({"llm": {"price_in": price}})


@pytest.mark.parametrize("allowlist,expected", [("", 0), ("国歌", 0), ("晴天 | 周杰伦", 1)])
def test_idle_dj_uses_only_reviewed_choices(monkeypatch, allowlist, expected):
    async def scenario():
        brain = scheduler.Brain()
        store._data["idle"]["gameplay_invites"] = False
        store._data["templates"].update(enabled=True, idle=True)
        store._data["pace"]["idle_chat_seconds"] = 0
        store._data["music"]["ai_allowlist"] = allowlist
        monkeypatch.setattr(LLM, "active", property(lambda model: True))
        sleeps = 0

        async def sleep(delay):
            nonlocal sleeps
            sleeps += 1
            if sleeps > 1:
                raise asyncio.CancelledError

        async def stream(*args, **kwargs):
            yield "来聊聊天吧"

        pick = AsyncMock(return_value=(True, "已安排"))
        monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
        monkeypatch.setattr(brain.llm, "stream", stream)
        monkeypatch.setattr(brain, "ai_song", pick)
        monkeypatch.setattr(brain, "_speak", AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await brain._idle_loop()
        assert pick.await_count == expected
        if expected:
            pick.assert_awaited_once_with("晴天 周杰伦")

    asyncio.run(scenario())


def test_model_probe_usage_is_counted(monkeypatch):
    counters = Metrics()
    monkeypatch.setattr("app.metrics.metrics", counters)
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(200, json={
        "choices": [{"message": {"content": "吞拿鱼"}}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 3}}))
    model = LLM()
    assert not model._probe_dropped_system({"base_url": "https://probe.invalid", "api_key": "test", "model": "probe-usage"})
    assert counters.tokens_in == 15 and counters.tokens_out == 3
    assert counters.llm_calls == 1 and not counters.estimated
