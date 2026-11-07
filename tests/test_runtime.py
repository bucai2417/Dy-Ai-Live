from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from contextlib import aclosing

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, playlist, settings_store
from app.brain import scheduler
from app.brain.llm import LLM
from app.config import settings
from app.events import Event
from app.hub import Hub
from app.ingest.base import OffIngestor
from app.metrics import Metrics
from app.settings_store import Effective, SettingsStore, store


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    data = copy.deepcopy(settings_store.DEFAULTS)
    data["ingest"]["enabled"] = False
    data["tts"]["engine"] = "none"
    data["templates"]["enabled"] = False
    data["pace"]["min_speak_gap"] = 0
    monkeypatch.setattr(store, "_data", data)
    monkeypatch.setattr(settings_store, "CONFIG_FILE", tmp_path / "runtime.json")
    for key in ("llm_api_key", "llm_base_url", "llm_model"):
        monkeypatch.setattr(settings, key, "")
    monkeypatch.setattr(settings, "demo", False)
    bus = Hub()
    monkeypatch.setattr(main, "hub", bus)
    monkeypatch.setattr(scheduler, "hub", bus)
    counters = Metrics()
    monkeypatch.setattr(main, "metrics", counters)
    monkeypatch.setattr(scheduler, "metrics", counters)
    songs = playlist.Queue()
    monkeypatch.setattr(playlist, "queue", songs)
    monkeypatch.setattr(scheduler, "queue", songs)
    return bus


@pytest.mark.parametrize("patch", [
    {"tts": None}, {"pace": []}, {"tts": {"engine": "unknown"}},
    {"tts": {"rate": "fast"}}, {"ingest": {"bridge_port": 0}},
    {"ingest": {"bridge_port": 1.5}}, {"pace": {"min_speak_gap": "abc"}},
    {"pace": {"min_speak_gap": -1}}, {"filters": {"banned_words": "你好"}},
    {"templates": {"enabled": "false"}}, {"interact": {"rules": [None]}},
    {"lines": {"welcome": ["欢迎{wrong}"]}}, {"llm": {"max_tokens": float("nan")}},
])
def test_invalid_config_does_not_modify_state_or_disk(patch):
    before = store.get_all()
    with pytest.raises(ValueError):
        store.update(patch)
    assert store.get_all() == before
    assert not settings_store.CONFIG_FILE.exists()


def test_save_failure_rolls_back_memory(monkeypatch):
    before = store.get_all()

    def fail():
        raise OSError("磁盘不可写")

    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(OSError):
        store.update({"persona": {"name": "新名字"}})
    assert store.get_all() == before
    with pytest.raises(OSError):
        store.reset()
    assert store.get_all() == before


def test_chinese_config_roundtrip_and_bom():
    store.update({"persona": {"name": "深蓝·测试鱼 🐟"}})
    path = settings_store.CONFIG_FILE
    text = path.read_text(encoding="utf-8")
    assert "深蓝·测试鱼 🐟" in text
    path.write_text(text, encoding="utf-8-sig")
    assert SettingsStore().persona_name == "深蓝·测试鱼 🐟"


def test_invalid_config_file_is_preserved():
    path = settings_store.CONFIG_FILE
    path.write_text('{"persona":', encoding="utf-8")
    original = path.read_bytes()
    loaded = SettingsStore()
    assert loaded.persona_name
    assert path.read_bytes() == original


def test_demo_overrides_disabled_ingest_without_saving(monkeypatch):
    original = store.get_all()
    monkeypatch.setattr(settings, "demo", True)
    assert Effective.ingest()["enabled"] is True
    assert Effective.ingest()["source"] == "mock"
    assert store.get_all() == original


def test_first_launch_uses_environment_defaults(monkeypatch):
    monkeypatch.setattr(settings, "ingest", "mock")
    monkeypatch.setattr(settings, "tts_engine", "none")
    monkeypatch.setattr(settings, "persona_name", "中文初始名字")
    fresh = SettingsStore()
    assert fresh.section("ingest")["source"] == "mock"
    assert fresh.section("tts")["engine"] == "none"
    assert fresh.persona_name == "中文初始名字"


def test_explicit_clear_removes_pending_session_state():
    with TestClient(main.app) as client:
        main.app.state.brain.paused = True
        main.app.state.brain.pending_enter.append("待欢迎观众")
        main.app.state.brain._pending_replies.append(Event(kind="chat", text="旧消息"))
        playlist.queue.add(playlist.make_track({"title": "旧歌曲", "url": "test"}))
        assert client.post("/api/session/boot").json()["ok"]
        assert not main.app.state.brain._pending_replies
        assert not main.app.state.brain.pending_enter
        assert not playlist.queue.pending()


def test_api_validates_config_and_keeps_zero_volume():
    with TestClient(main.app) as client:
        assert client.post("/api/config", json={"pace": None}).status_code == 400
        response = client.post("/api/music/volume", json={"volume": 0})
        assert response.status_code == 200
        assert Effective.music()["volume"] == 0
        assert client.post("/api/music/volume", json={"volume": "bad"}).status_code == 400


def test_config_hot_reload_skips_unchanged_ingest():
    with TestClient(main.app) as client:
        original = main.app.state.ingest_task
        assert client.post("/api/config", json={"ingest": Effective.ingest()}).status_code == 200
        assert main.app.state.ingest_task is original
        assert client.post("/api/config", json={"tts": {"engine": "edge"}}).status_code == 200
        assert main.app.state.brain.speaker.tts.name == "edge"
        assert client.post("/api/config", json={"tts": {"engine": "none"}}).status_code == 200
        assert main.app.state.brain.speaker.tts.name == "none"


def test_reset_rebuilds_ingest_and_notifies_clients(monkeypatch):
    monkeypatch.setattr(main, "build_ingestor", OffIngestor)
    with TestClient(main.app) as client:
        original = main.app.state.ingest_task
        with client.websocket_connect("/ws") as connection:
            assert connection.receive_json()["type"] == "hello"
            assert connection.receive_json()["type"] == "online"
            response = client.post("/api/config/reset")
            assert response.status_code == 200
            assert connection.receive_json()["type"] == "config_changed"
        assert main.app.state.ingest_task is not original
        assert original.done()


def test_ws_ignores_non_object_messages_and_keeps_pause_state():
    with TestClient(main.app) as client:
        main.app.state.brain.paused = True
        with client.websocket_connect("/ws") as connection:
            hello = connection.receive_json()
            assert hello["paused"] is True
            assert hello["config"]["ingest"] == "off"
            assert connection.receive_json()["type"] == "online"
            for malformed in ("null", "[]", "123", "{"):
                connection.send_text(malformed)
            connection.send_json({"cmd": "ping"})
            assert connection.receive_json()["type"] == "pong"
    assert not main.hub.clients


def test_manual_speech_updates_console_count():
    with TestClient(main.app) as client:
        response = client.post("/api/say", json={"text": "中文人工播报测试"})
        assert response.json()["ok"] is True
        assert client.get("/api/state").json()["stats"]["replies"] == 1


def test_manual_character_action_is_broadcast_and_blocked_during_pause():
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as connection:
            connection.receive_json()
            connection.receive_json()
            connection.send_json({"cmd": "reaction", "motion": "dance"})
            message = connection.receive_json()
            performance = message.pop("performance")
            assert message == {"type": "reaction", "motion": "dance", "manual": True}
            assert performance["motion"] == "dance" and performance["remaining"] > 0
            connection.send_json({"cmd": "reaction", "motion": "invalid"})
            assert connection.receive_json()["type"] == "error"
            main.app.state.brain.paused = True
            connection.send_json({"cmd": "reaction", "motion": "dance"})
            assert "暂停" in connection.receive_json()["error"]
        assert not any(item["type"] == "reaction" for item in main.hub.recent)


def test_live_pause_stops_music_and_rejects_speech_until_resumed():
    with TestClient(main.app) as client:
        track = playlist.make_track({"title": "中文测试曲", "url": "test"})
        playlist.queue.add(track)
        playlist.queue.next_track()
        brain = main.app.state.brain
        brain._pending_replies.append(Event(kind="chat", text="暂停前的消息"))
        brain.pending_enter.append("待欢迎")
        assert client.post("/api/live/pause").json()["paused"]
        elapsed = playlist.queue.snapshot()["elapsed"]
        assert not playlist.queue.playing
        assert playlist.queue.current is track
        assert not brain._pending_replies and not brain.pending_enter
        assert not brain._ambient_tasks
        assert client.post("/api/say", json={"text": "不可播放"}).status_code == 409
        assert client.post("/api/music/resume").status_code == 409
        assert client.post("/api/live/pause").status_code == 200
        assert not client.post("/api/live/resume").json()["paused"]
        assert playlist.queue.playing
        assert playlist.queue.snapshot()["elapsed"] >= elapsed
        assert {task.get_name() for task in brain._ambient_tasks} == {
            "brain:_idle_loop", "brain:_idle_action_loop", "brain:_enter_flush_loop"}


def test_live_resume_does_not_restart_manually_paused_music():
    with TestClient(main.app) as client:
        playlist.queue.add(playlist.make_track({"title": "暂停曲目", "url": "test"}))
        playlist.queue.next_track()
        client.post("/api/music/stop")
        client.post("/api/live/pause")
        client.post("/api/live/resume")
        assert not playlist.queue.playing
        assert playlist.queue.paused


def test_stop_during_intermission_prevents_music_resume():
    with TestClient(main.app) as client:
        playlist.queue.add(playlist.make_track({"title": "测试", "url": "test"}))
        playlist.queue.next_track()
        client.post("/api/live/pause")
        client.post("/api/music/stop")
        client.post("/api/live/resume")
        assert not playlist.queue.playing


def test_renderer_rejects_invalid_dimensions():
    with TestClient(main.app) as client:
        for patch in ({"width": "bad"}, {"height": 0}, {"fps": 1000}):
            assert client.post("/api/renderer/start", json=patch).status_code == 400


def test_pause_cancels_inflight_ambient_generation():
    async def check():
        brain = scheduler.Brain()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending():
            started.set()
            try:
                await asyncio.sleep(20)
            finally:
                cancelled.set()

        brain._ambient_tasks = [asyncio.create_task(pending())]
        await started.wait()
        try:
            await brain.set_paused(True)
            assert cancelled.is_set()
            assert not brain._ambient_tasks
        finally:
            await brain.llm.aclose()

    asyncio.run(check())


def test_refresh_pages_preserves_music_queue():
    with TestClient(main.app) as client:
        playlist.queue.add(playlist.make_track({"title": "中文歌曲", "url": "test"}))
        for page in ("/stage", "/console", "/settings", "/stage"):
            assert client.get(page).status_code == 200
        assert len(playlist.queue.pending()) == 1


def test_ingest_restarts_are_serial_and_shutdown_cleans_current(monkeypatch):
    instances = []

    class Ingestor:
        name = "mock"

        def __init__(self):
            self.running = False
            self.stopped = False
            instances.append(self)

        async def run(self, emit):
            self.running = True
            try:
                await asyncio.Future()
            finally:
                self.running = False

        async def stop(self):
            await asyncio.sleep(0)
            self.stopped = True

    monkeypatch.setattr(main, "build_ingestor", Ingestor)

    async def scenario():
        async with main.lifespan(main.app):
            await asyncio.gather(main._rebuild_ingest(), main._rebuild_ingest())
            await asyncio.sleep(0)
            assert sum(item.running for item in instances) == 1
            assert all(item.stopped for item in instances[:-1])
        assert all(item.stopped and not item.running for item in instances)
        assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("brain:")]

    asyncio.run(scenario())


def test_health_detects_dead_ingest():
    async def scenario():
        async with main.lifespan(main.app):
            main.app.state.ingest_task.cancel()
            await asyncio.gather(main.app.state.ingest_task, return_exceptions=True)
            assert (await main.health()).status_code == 503
            status = json.loads((await main.api_ingest_status()).body)
            assert status["ingest"]["alive"] is False

    asyncio.run(scenario())


def test_speaker_cancellation_releases_busy_and_gate():
    async def scenario():
        started = asyncio.Event()

        async def synthesize(text):
            started.set()
            await asyncio.Future()

        speaker = scheduler.Speaker()
        speaker.tts = SimpleNamespace(name="none", synthesize=synthesize)
        task = asyncio.create_task(speaker.say("中文播报"))
        await started.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not speaker.busy
        assert speaker.current_score == 0
        assert not speaker._gate.locked()

    asyncio.run(scenario())


def test_speaker_failure_does_not_stick_busy():
    async def fail(text):
        raise RuntimeError("合成失败")

    async def scenario():
        speaker = scheduler.Speaker()
        speaker.tts = SimpleNamespace(name="none", synthesize=fail)
        await speaker.say("测试语音失败")
        assert not speaker.busy
        assert not speaker._gate.locked()

    asyncio.run(scenario())


def test_interrupted_synthesis_does_not_send_late_voice(monkeypatch):
    messages = []

    async def broadcast(payload, remember=True):
        messages.append(payload)

    monkeypatch.setattr(scheduler.hub, "broadcast", broadcast)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def synthesize(text):
            started.set()
            await release.wait()
            return b"late audio"

        speaker = scheduler.Speaker()
        speaker.tts = SimpleNamespace(name="none", synthesize=synthesize)
        task = asyncio.create_task(speaker.say("正在合成"))
        await started.wait()
        speaker.interrupt()
        release.set()
        assert not await task
        assert not speaker.busy
        assert not any(message["type"] == "voice" for message in messages)

    asyncio.run(scenario())


def test_viewers_can_repeat_messages_without_cooldown():
    brain = scheduler.Brain()
    assert brain._allow(Event(kind="chat", user_name="小鱼", text="你好呀"))
    assert brain._allow(Event(kind="chat", user_name="蓝鲸", text="早上好"))
    assert brain._allow(Event(kind="chat", user_name="小鱼", text="再说一句"))
    assert brain._allow(Event(kind="chat", user_name="小鱼", text="你好呀"))
    assert brain._allow(Event(kind="chat", user_name="蓝鲸", text="你好呀"))


def test_hub_does_not_replay_audio_and_drops_slow_client():
    async def scenario():
        bus = Hub(send_timeout=0.02)
        received = []

        class Client:
            def __init__(self, slow=False):
                self.slow = slow

            async def send_json(self, payload):
                if self.slow:
                    await asyncio.Future()
                received.append(payload)

            async def close(self, code):
                pass

        fast, slow = Client(), Client(True)
        bus.clients.update((fast, slow))
        await bus.broadcast({"type": "voice", "audio": "test"})
        assert received
        assert not bus.recent
        assert bus.clients == {fast}
        assert bus.stats["online"] == 1
        await bus.broadcast({"type": "event", "event": {"text": "中文"}})
        assert len(bus.recent) == 1

    asyncio.run(scenario())


def test_full_queue_keeps_stats_and_join_consistent():
    async def scenario():
        bus = Hub()
        bus.queue = asyncio.Queue(maxsize=1)
        bus.feed(Event(kind="chat", text="第一条"))
        bus.feed(Event(kind="chat", text="第二条"))
        assert bus.stats["danmu_total"] == 2
        assert bus.queue.get_nowait().text == "第二条"
        bus.queue.task_done()
        await asyncio.wait_for(bus.queue.join(), timeout=0.1)

    asyncio.run(scenario())


def test_llm_http_error_respects_disabled_fallback(monkeypatch):
    store._data["fallback"]["offline_reply"] = False
    store._data["llm"]["prompt_mode"] = "system"
    store._data["llm"]["base_url"] = "https://model.test/v1"

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
        model = LLM()
        monkeypatch.setattr(model, "_ensure_client", lambda: client)
        try:
            assert [chunk async for chunk in model.stream("你好")] == []
            assert model.tripped
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_early_llm_close_releases_response_and_records_usage(monkeypatch):
    from app import metrics as metrics_module

    counters = Metrics()
    monkeypatch.setattr(metrics_module, "metrics", counters)
    store._data["llm"]["prompt_mode"] = "system"
    store._data["llm"]["base_url"] = "https://model.test/v1"

    class ResponseStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield ('data: ' + json.dumps({"choices": [{"delta": {"content": "欢迎小鱼"}}]}) + '\n\n').encode()
            await asyncio.Future()

        async def aclose(self):
            self.closed = True

    async def scenario():
        response_stream = ResponseStream()
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=response_stream)))
        model = LLM()
        monkeypatch.setattr(model, "_ensure_client", lambda: client)
        try:
            async with aclosing(model.stream("你好")) as stream:
                assert await anext(stream) == "欢迎小鱼"
            assert response_stream.closed
            assert counters.llm_calls == 1
            assert counters.tokens_out > 0
            assert counters.estimated
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_chat_closes_generator_before_speaking(monkeypatch):
    closed = []

    async def stream(*args, **kwargs):
        try:
            yield "这是一条用于测试的完整中文回复。" * 4
            await asyncio.Future()
        finally:
            closed.append(True)

    async def speak(*args, **kwargs):
        assert closed == [True]

    brain = scheduler.Brain()
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    monkeypatch.setattr(brain.llm, "stream", stream)
    monkeypatch.setattr(brain, "_speak", speak)
    asyncio.run(brain._chat(Event(kind="chat", text="你好"), 1))


def test_music_pause_retains_song_and_prevents_autoplay(monkeypatch):
    current_time = [100.0]
    monkeypatch.setattr(playlist.time, "time", lambda: current_time[0])
    songs = playlist.queue
    songs.add(playlist.make_track({"title": "第一首", "url": "one"}))
    songs.add(playlist.make_track({"title": "第二首", "url": "two"}))
    current = songs.next_track()
    current_time[0] = 110.0
    songs.pause()
    assert songs.current is current
    assert songs.snapshot()["elapsed"] == 10
    asyncio.run(scheduler.Brain().music_autoplay())
    assert songs.current is current
    assert not songs.playing
    current_time[0] = 210.0
    songs.resume()
    assert songs.current is current
    assert songs.snapshot()["elapsed"] == 10
    assert songs.playing


def test_music_stop_and_resume_api_keeps_same_track():
    with TestClient(main.app) as client:
        playlist.queue.add(playlist.make_track({"title": "当前歌曲", "url": "one"}))
        playlist.queue.add(playlist.make_track({"title": "下一首", "url": "two"}))
        current = playlist.queue.next_track()
        stopped = client.post("/api/music/stop").json()["music"]
        assert stopped["current"]["id"] == current.id
        assert not stopped["playing"]
        client.post("/api/music/played", json={"id": current.id})
        resumed = client.post("/api/music/resume").json()["music"]
        assert resumed["current"]["id"] == current.id
        assert resumed["playing"]


def test_skip_discards_pending_model_reply(monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        spoken = []

        async def stream(*args, **kwargs):
            started.set()
            await release.wait()
            yield "这条回复已经被用户跳过"

        async def speak(*args, **kwargs):
            spoken.append(args)

        brain = scheduler.Brain()
        monkeypatch.setattr(brain.llm, "stream", stream)
        monkeypatch.setattr(brain, "_speak", speak)
        task = asyncio.create_task(brain._chat(Event(kind="chat", text="问题"), 1))
        await started.wait()
        await brain.stop_speech()
        release.set()
        await task
        assert not spoken

    asyncio.run(scenario())


def test_slow_reply_does_not_delay_gift_display_or_drop_chat(monkeypatch):
    store._data["music"]["enabled"] = False
    store._data["interact"]["enabled"] = False
    store._data["templates"]["enabled"] = True

    async def scenario():
        started, gift_visible, gift_replied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release, chat_replied = asyncio.Event(), asyncio.Event()
        brain = scheduler.Brain()

        async def stream(*args, **kwargs):
            started.set()
            await release.wait()
            yield "这条弹幕也会回复"

        async def broadcast(payload, remember=True):
            if payload["type"] == "event" and payload["event"]["kind"] == "gift":
                gift_visible.set()

        async def speak(text, event, *args, **kwargs):
            if event and event.kind == "gift":
                gift_replied.set()
            elif event and event.kind == "chat":
                chat_replied.set()

        monkeypatch.setattr(brain.llm, "stream", stream)
        monkeypatch.setattr(brain, "_speak", speak)
        monkeypatch.setattr(scheduler.hub, "broadcast", broadcast)
        task = asyncio.create_task(brain.run())
        try:
            scheduler.hub.feed(Event(kind="chat", user_name="小鱼", text="主播你好"))
            await asyncio.wait_for(started.wait(), timeout=1)
            scheduler.hub.feed(Event(kind="gift", user_name="蓝鲸", gift_name="玫瑰", gift_value=100))
            await asyncio.wait_for(gift_visible.wait(), timeout=1)
            assert not gift_replied.is_set()
            release.set()
            await asyncio.wait_for(gift_replied.wait(), timeout=1)
            assert chat_replied.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
