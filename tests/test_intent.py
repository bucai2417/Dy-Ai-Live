from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app.brain import scheduler
from app.brain.intent import Intent, decode_intent, local_intent
from app.brain.llm import LLM
from app.events import Event
from app.settings_store import store
from tests.test_runtime import isolated_runtime


@pytest.mark.parametrize("text,action,value", [
    ("播放晴天", "song", "晴天"),
    ("能放周杰伦的晴天吗", "song", "周杰伦的晴天"),
    ("帮我播放《稻香》吧", "song", "稻香"),
    ("请暂停音乐", "stop", ""),
    ("继续播放", "resume", ""),
    ("能不能下一首", "next", ""),
    ("现在什么歌", "what", ""),
    ("跳个舞吧", "motion", "dance"),
    ("给大家鞠个躬", "motion", "bow"),
    ("请挥挥手", "motion", "wave"),
])
def test_offline_natural_requests(text, action, value):
    result = local_intent(text)
    assert result.action == action
    assert (result.query or result.motion) == value


@pytest.mark.parametrize("text", [
    "别播放晴天", "不要跳舞", "我不想听稻香", "你刚才跳舞了吗", "如果能播放晴天就好了",
    "他说播放晴天", "放心吧", "这首歌真好听", "暂停直播", "下一首是什么", "播放什么歌",
])
def test_offline_does_not_execute_negation_or_discussion(text):
    assert local_intent(text).action == "chat"


@pytest.mark.parametrize("payload", [
    {}, [], {"action": "delete", "confidence": 1},
    {"action": "motion", "motion": "shell", "confidence": 1},
    {"action": "song", "query": "", "confidence": 1},
    {"action": "song", "query": "歌\n名", "confidence": 1},
    {"action": "song", "query": "好歌", "confidence": True},
    {"action": "song", "query": "好歌", "confidence": float("nan")},
])
def test_model_output_is_validated(payload):
    assert decode_intent(json.dumps(payload)) is None


def test_ambiguous_model_output_is_chat():
    assert decode_intent('{"action":"song","query":"晴天","confidence":0.6}').action == "chat"
    assert decode_intent('这是一段非结构化输出') is None


def test_classifier_uses_configured_model_without_persona_or_history(monkeypatch):
    async def scenario():
        model = LLM()
        config = {"base_url": "https://model.invalid/v1", "api_key": "isolated-test", "model": "test-model",
                  "prompt_mode": "user"}
        monkeypatch.setattr(model, "_cfg", lambda: config)

        def respond(request):
            payload = json.loads(request.content)
            assert request.url.path == "/v1/chat/completions"
            assert payload["model"] == "test-model" and payload["stream"] is False
            assert payload["messages"][0]["role"] == "user"
            assert "直播弹幕意图分类器" in payload["messages"][0]["content"]
            assert json.loads(payload["messages"][1]["content"])["弹幕"] == "耳朵想听稻香了，安排一下"
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
                {"action": "song", "query": "稻香", "confidence": 0.98})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 25}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(model, "_ensure_client", lambda: client)
            result = await model.classify_intent("耳朵想听稻香了，安排一下")
            assert result == Intent("song", "稻香", source="ai")

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["timeout", "invalid", "auth"])
def test_classifier_failure_returns_local_fallback(monkeypatch, failure):
    async def scenario():
        model = LLM()
        monkeypatch.setattr(model, "_cfg", lambda: {
            "base_url": "https://model.invalid/v1", "api_key": "test", "model": "test"})

        def respond(request):
            if failure == "timeout":
                raise httpx.ReadTimeout("test", request=request)
            return httpx.Response(401 if failure == "auth" else 200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            monkeypatch.setattr(model, "_ensure_client", lambda: client)
            assert await model.classify_intent("播放晴天") is None
            assert model.tripped == (failure == "auth")

    asyncio.run(scenario())


def test_repeated_chat_requests_each_execute_once(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        classify = AsyncMock(return_value=Intent("song", "稻香", source="ai"))
        enqueue = AsyncMock(return_value=(True, "好，放《稻香》"))
        speak = AsyncMock()
        monkeypatch.setattr(brain.llm, "classify_intent", classify)
        monkeypatch.setattr(brain, "_enqueue", enqueue)
        monkeypatch.setattr(brain, "_speak", speak)
        for user in ("小鱼", "小鱼", "蓝鲸"):
            await brain.handle(Event(kind="chat", user_name=user, text="耳朵想听稻香了，安排一下"))
        assert classify.await_count == enqueue.await_count == speak.await_count == 3

    asyncio.run(scenario())


def test_repeated_speech_is_not_suppressed(monkeypatch):
    async def scenario():
        store._data["fallback"]["offline_voice"] = True
        brain = scheduler.Brain()
        speak = AsyncMock()
        monkeypatch.setattr(brain.speaker, "say", speak)
        for index in range(2):
            await brain._speak("已经为你安排", Event(kind="chat", text="播放晴天"), 1)
        assert speak.await_count == 2

    asyncio.run(scenario())


def test_ai_chat_decision_does_not_run_keyword_actions(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        monkeypatch.setattr(brain.llm, "classify_intent", AsyncMock(return_value=Intent(source="ai")))
        chat, enqueue = AsyncMock(), AsyncMock()
        monkeypatch.setattr(brain, "_chat", chat)
        monkeypatch.setattr(brain, "_enqueue", enqueue)
        for text in ("不要跳舞", "他说播放晴天", "这首晴天真好听"):
            await brain.handle(Event(kind="chat", text=text))
        assert chat.await_count == 3
        enqueue.assert_not_called()

    asyncio.run(scenario())


def test_pause_cancels_intent_before_action(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        started, release = asyncio.Event(), asyncio.Event()

        async def classify(text):
            started.set()
            await release.wait()
            return Intent("song", "晴天", source="ai")

        enqueue = AsyncMock()
        monkeypatch.setattr(brain.llm, "classify_intent", classify)
        monkeypatch.setattr(brain, "_enqueue", enqueue)
        task = asyncio.create_task(brain.handle(Event(kind="chat", text="播放晴天")))
        await started.wait()
        await brain.set_paused(True)
        release.set()
        await task
        enqueue.assert_not_called()

    asyncio.run(scenario())


def test_repeated_ai_motion_and_failed_song_have_real_feedback(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        broadcast, speak = AsyncMock(), AsyncMock()
        monkeypatch.setattr(scheduler.hub, "broadcast", broadcast)
        monkeypatch.setattr(brain, "_speak", speak)
        monkeypatch.setattr(brain.llm, "classify_intent", AsyncMock(return_value=Intent("motion", motion="bow", source="ai")))
        for index in range(2):
            await brain.handle(Event(kind="chat", text="向大家行个礼吧"))
        reactions = [call.args[0] for call in broadcast.await_args_list if call.args[0]["type"] == "reaction"]
        assert len(reactions) == 2 and all(item["motion"] == "bow" for item in reactions)
        assert reactions[0]["event_id"] != reactions[1]["event_id"]
        monkeypatch.setattr(brain.llm, "classify_intent", AsyncMock(return_value=Intent("song", "找不到的歌", source="ai")))
        monkeypatch.setattr(brain, "_enqueue", AsyncMock(return_value=(False, "没找到能放的版本")))
        await brain.handle(Event(kind="chat", text="播放找不到的歌"))
        assert speak.await_args.args[0] == "没找到能放的版本"

    asyncio.run(scenario())


def test_old_repeated_pending_messages_are_processed(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        responded, finished = [], asyncio.Event()

        async def respond(event):
            responded.append(event.id)
            if len(responded) == 3:
                finished.set()

        monkeypatch.setattr(brain, "_respond", respond)
        events = [Event(kind="chat", text="一样的弹幕", ts=time.time() - 90) for index in range(3)]
        brain._pending_replies.extend(events)
        brain._reply_ready.set()
        task = asyncio.create_task(brain._reply_loop())
        try:
            await asyncio.wait_for(finished.wait(), 1)
            assert responded == [event.id for event in events]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
