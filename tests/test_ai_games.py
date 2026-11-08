from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from fastapi.testclient import TestClient

import httpx
import pytest

from app import main
from app.brain import scheduler
from app.brain.companion import finish_text
from app.brain.intent import decode_intent, local_intent
from app.brain.llm import LLM
from app.events import Event
from app.minigames import MiniGame, idiom_text, validate_drawing, validate_question
from app.settings_store import DEFAULTS, Effective, store
from tests.test_runtime import isolated_runtime


def drawing():
    return {"answer": "雨伞", "aliases": ["伞"], "hints": ["下雨天会用到", "可以撑开也可以收起"],
            "strokes": [{"shape": "line", "points": [[12, 40], [25, 17], [50, 10], [75, 17], [88, 40]], "color": "#689ec7"},
                        {"shape": "line", "points": [[12, 40], [88, 40]], "color": "#689ec7"},
                        {"shape": "line", "points": [[50, 40], [50, 85], [43, 91], [36, 85]], "color": "#24364b"}]}


def question_brain(kind="idiom"):
    brain = scheduler.Brain()
    brain.games.start(kind, Effective.games(), "owner")
    brain.games.install(validate_question(kind, {"word": "心想事成", "meaning": "心中所想的事情得以实现"} if kind == "idiom" else drawing()))
    brain.sync_activity_pauses()
    return brain


def quiet_brain(bus):
    brain = scheduler.Brain()
    brain.last_activity_at = brain.last_speak_at = bus.stats["started_at"] = time.time() - 1000
    brain._audience_at = time.monotonic() - 1000
    return brain


@pytest.mark.parametrize("text,expected", [("是非曲直", "是非曲直"), ("我猜是：成千上万！", "成千上万"), ("成语接龙", "成语接龙"), ("非常的开心", "")])
def test_idiom_normalization_keeps_first_character(text, expected):
    assert idiom_text(text) == expected


@pytest.mark.parametrize("text,action", [("成语接龙", "idiom"), ("来玩成语接龙吧", "idiom"), ("小鱼你画我猜", "drawing"),
                                         ("我画你猜", "drawing"), ("结束小游戏", "stop"), ("下一题", "skip")])
def test_game_commands(text, action):
    assert local_intent(text).action == "game"
    assert local_intent(text).motion == action


@pytest.mark.parametrize("text", ["不要玩成语接龙", "我说的是‘下一题’", "我昨天玩了你画我猜", "别结束小游戏"])
def test_game_negation_and_quotes_are_not_commands(text):
    assert local_intent(text).action == "chat"


def test_ai_game_intent_is_allowlisted():
    assert decode_intent(json.dumps({"action": "game", "motion": "drawing", "confidence": .95})).motion == "drawing"
    assert decode_intent(json.dumps({"action": "game", "motion": "execute", "confidence": .99})) is None


@pytest.mark.parametrize("change", [
    {"shape": "script"}, {"points": [[0, 0], [float("nan"), 5]]}, {"points": [[0, 0], [101, 5]]},
    {"points": [[True, 0], [1, 5]]}, {"points": [[0, 0]] * 41}, {"color": "url(secret)"}, {"fill": "red"},
])
def test_drawing_rejects_invalid_geometry(change):
    strokes = drawing()["strokes"]
    strokes[0].update(change)
    with pytest.raises(ValueError):
        validate_drawing(strokes)


def test_drawing_hides_secret_and_rejects_leaked_hints():
    game = MiniGame()
    game.start("drawing", Effective.games(), now=100)
    game.install(validate_question("drawing", drawing()), now=100)
    for now in (100, 125, 150):
        public = game.snapshot(now)
        assert "answer" not in public["question"] and "aliases" not in public["question"]
        assert "雨伞" not in json.dumps(public, ensure_ascii=False)
    game.finish("时间到了", now=160)
    assert game.snapshot(160)["question"]["answer"] == "雨伞"
    leaked = drawing()
    leaked["hints"][0] = "是一把雨伞"
    with pytest.raises(ValueError):
        validate_question("drawing", leaked)


def test_game_pause_and_timeout_resume_original_clock():
    game = MiniGame()
    game.start("idiom", Effective.games(), now=100)
    game.install({"word": "心想事成", "meaning": "愿望实现"}, now=100)
    game.pause(True, now=110)
    assert game.snapshot(900)["remaining"] == 50
    assert game.tick(900) == ""
    game.pause(False, now=900)
    assert game.snapshot(920)["remaining"] == 30
    game.tick(950)
    assert game.phase == "result" and game.final
    assert game.tick(958) == "stopped"


def test_idiom_rounds_and_scores_are_session_only():
    game = MiniGame()
    game.start("idiom", {**Effective.games(), "rounds": 2}, now=100)
    game.install({"word": "心想事成", "meaning": "愿望实现"}, now=100)
    game.award({"id": "one", "name": "中文鱼友"}, "成千上万", "数量很多", now=110)
    snapshot = game.snapshot(110)
    snapshot["scores"][0]["score"] = -10
    assert game.scores["one"]["score"] == 10
    game.tick(118)
    assert game.round == 2 and game.snapshot(118)["question"]["required"] == "万"
    game.award({"id": "two", "name": "第二位"}, "万事如意", now=120)
    assert game.final and len(game.scores) == 2
    game.tick(128)
    game.start("idiom", Effective.games(), now=150)
    assert not game.scores


def test_idiom_judge_repeated_messages_and_first_winner(monkeypatch):
    async def scenario():
        brain = question_brain()
        judge = AsyncMock(return_value={"word": "成千上万", "valid": True, "meaning": "数量非常多"})
        monkeypatch.setattr(brain.llm, "structured", judge)
        for name in ("第一位", "第二位"):
            event = Event(kind="chat", user_id=name, user_name=name, text="成千上万")
            assert await brain._publish_event(event)
            assert await brain.game_chat(event)
        assert len(brain._game_answers) == 2
        await brain.judge_answer(*brain._game_answers.popleft())
        assert brain.games.winner["name"] == "第一位"
        assert not brain._game_answers and judge.await_count == 1
        await brain.close_games()
    asyncio.run(scenario())


def test_wrong_first_character_costs_no_ai_tokens(monkeypatch):
    async def scenario():
        brain = question_brain()
        judge = AsyncMock()
        monkeypatch.setattr(brain.llm, "structured", judge)
        event = Event(kind="chat", text="万事如意")
        await brain.game_chat(event)
        await brain.judge_answer(*brain._game_answers.popleft())
        assert "成" in brain.games.feedback and not brain.games.scores
        judge.assert_not_awaited()
        await brain.close_games()
    asyncio.run(scenario())


@pytest.mark.parametrize("stale", [True, False])
def test_late_judgment_cannot_win_or_overwrite_new_round(monkeypatch, stale):
    async def scenario():
        brain = question_brain()
        entered, release = asyncio.Event(), asyncio.Event()
        async def judge(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"word": "成千上万", "valid": True, "meaning": "数量非常多"}
        monkeypatch.setattr(brain.llm, "structured", judge)
        event = Event(kind="chat", text="成千上万")
        task = asyncio.create_task(brain.judge_answer(brain.games.question_id, event.text, event))
        await entered.wait()
        if stale:
            brain.games.start("idiom", Effective.games())
            brain.games.install({"word": "一路平安", "meaning": "祝愿旅途平安"})
        else:
            brain.games.deadline = time.monotonic() - 1
        expected = brain.games.feedback
        release.set()
        await task
        assert not brain.games.winner
        if stale:
            assert brain.games.feedback == expected
        await brain.close_games()
    asyncio.run(scenario())


def test_gift_during_judgment_freezes_then_resumes_and_keeps_fishing(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        brain.fishing.start("会客厅")
        brain.games.start("idiom", Effective.games(), "owner")
        brain.games.install({"word": "心想事成", "meaning": "愿望实现"})
        brain.sync_activity_pauses()
        fishing_id = brain.fishing.round_id
        entered, release = asyncio.Event(), asyncio.Event()
        async def judge(*args, **kwargs):
            entered.set()
            await release.wait()
            return {"word": "成千上万", "valid": True, "meaning": "数量非常多"}
        monkeypatch.setattr(brain.llm, "structured", judge)
        event = Event(kind="chat", user_id="one", text="成千上万")
        task = asyncio.create_task(brain.judge_answer(brain.games.question_id, event.text, event))
        await entered.wait()
        await brain._publish_event(Event(kind="gift", user_id="donor", user_name="送礼者", gift_name="鲜花", gift_count=1, gift_value=10))
        assert brain.gift_show.active and brain.games.paused_at is not None
        release.set()
        await task
        assert not brain.games.winner and len(brain._game_answers) == 1
        brain.gift_show.clear()
        await brain.sync_gift_show()
        assert brain.games.paused_at is None and brain.fishing.paused_at is not None
        await brain.judge_answer(*brain._game_answers.popleft())
        assert brain.games.winner["id"] == "one"
        await brain.close_games()
        assert brain.fishing.active and brain.fishing.paused_at is None and brain.fishing.round_id == fishing_id
    asyncio.run(scenario())


def test_draw_guess_aliases_and_no_per_guess_ai_call(monkeypatch):
    async def scenario():
        brain = question_brain("drawing")
        judge = AsyncMock()
        monkeypatch.setattr(brain.llm, "structured", judge)
        assert await brain.game_chat(Event(kind="chat", text="苹果"))
        assert not brain._game_answers
        event = Event(kind="chat", user_name="小鱼", text="我猜是：伞！")
        assert await brain._publish_event(event)
        await brain.judge_answer(*brain._game_answers.popleft())
        assert brain.games.winner["name"] == "小鱼"
        judge.assert_not_awaited()
        assert brain.games.snapshot()["question"]["answer"] == "雨伞"
        await brain.close_games()
    asyncio.run(scenario())


def test_paused_judge_keeps_every_already_queued_answer(monkeypatch):
    async def scenario():
        brain = question_brain()
        inflight = Event(kind="chat", text="成千上万", user_id="first")
        queued = []
        async def judge(*args, **kwargs):
            brain.games.pause(True)
            for index in range(35):
                event = Event(kind="chat", text="成千上万", user_id=str(index))
                await brain.game_chat(event)
                if event.id in brain.games.seen:
                    queued.append(event.id)
            return {"word": "成千上万", "valid": True, "meaning": "数量非常多"}
        monkeypatch.setattr(brain.llm, "structured", judge)
        await brain.judge_answer(brain.games.question_id, inflight.text, inflight)
        assert len(queued) == 31
        assert [entry[2].id for entry in brain._game_answers] == [inflight.id, *queued]
        await brain.close_games()
    asyncio.run(scenario())


def test_game_stop_permissions_and_shutdown_cancellation(monkeypatch):
    async def scenario():
        brain = question_brain()
        with pytest.raises(ValueError, match="管理员"):
            await brain.game_command("stop", Event(kind="chat", user_name="管理员", user_id="outsider"))
        wait = asyncio.create_task(asyncio.sleep(100))
        brain._game_task = wait
        await brain.game_command("stop", Event(kind="chat", user_id="owner"))
        assert wait.cancelled() and not brain.games.active and brain.world.paused_at is None
    asyncio.run(scenario())


def test_start_from_ai_response_and_disable_games(monkeypatch):
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    async def scenario():
        brain = scheduler.Brain()
        monkeypatch.setattr(brain.llm, "structured", AsyncMock(return_value=drawing()))
        brain._response_task = asyncio.current_task()
        await brain.game_command("drawing", Event(kind="chat", user_id="owner"))
        await brain._game_task
        assert brain.games.phase == "question" and brain.world.paused_at is not None
        context = brain.runtime_context()
        assert "雨伞" not in json.dumps(context["game"], ensure_ascii=False)
        assert "strokes" not in context["game"]["question"]
        store._data["games"]["enabled"] = False
        await brain.configure_live_features()
        assert not brain.games.active
    asyncio.run(scenario())


def test_auto_game_is_opt_in_and_ends_if_no_one_answers(monkeypatch):
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    async def scenario():
        brain = scheduler.Brain()
        brain._audience_at = brain._game_last_auto = time.monotonic() - 1000
        monkeypatch.setattr(brain.llm, "structured", AsyncMock(return_value={"word": "心想事成", "meaning": "愿望实现"}))
        await brain.games_tick()
        assert not brain.games.active
        store._data["games"]["auto_start"] = True
        await brain.games_tick()
        await brain._game_task
        brain.games.deadline = time.monotonic() - 1
        await brain.games_tick()
        assert brain.games.phase == "result" and brain.games.final
        brain.games.deadline = time.monotonic() - 1
        await brain.games_tick()
        assert not brain.games.active and brain._game_task is None
        await brain.games_tick()
        assert not brain.games.active
    asyncio.run(scenario())


def test_companion_uses_ai_without_viewers_or_templates(isolated_runtime, monkeypatch):
    brain = quiet_brain(isolated_runtime)
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    generator = AsyncMock(return_value={"topic": "云朵的形状", "speech": "如果云朵有自己的收藏夹，里面会不会全是天空的照片？我想给今天这片云起个软乎乎的名字。"})
    spoken = AsyncMock()
    monkeypatch.setattr(brain.llm, "structured", generator)
    monkeypatch.setattr(brain, "_speak", spoken)
    assert asyncio.run(brain._idle_once())
    assert "云朵" in spoken.await_args.args[0]
    assert len(brain.topic_memory) == 1
    assert generator.await_args.args[1]["recent_topics"] == []
    brain.companion_retry_at = 0
    assert not asyncio.run(brain._idle_once())
    assert len(generator.await_args.args[1]["recent_topics"]) == 1
    assert spoken.await_count == 1


@pytest.mark.parametrize("available,response", [(False, None), (True, None), (True, {"speech": "缺少主题"}), (True, {"topic": "不完整", "speech": "这句话只有逗号，" * 30})])
def test_companion_never_falls_back_to_canned_content(isolated_runtime, monkeypatch, available, response):
    brain = quiet_brain(isolated_runtime)
    store._data["templates"].update(enabled=True, idle=True)
    store._data["fallback"]["offline_reply"] = True
    monkeypatch.setattr(LLM, "active", property(lambda self: available))
    monkeypatch.setattr(brain.llm, "structured", AsyncMock(return_value=response))
    spoken = AsyncMock()
    monkeypatch.setattr(brain, "_speak", spoken)
    assert not asyncio.run(brain._idle_once())
    spoken.assert_not_awaited()
    assert not brain._idle_preparing


def test_companion_discards_topic_when_viewer_arrives(isolated_runtime, monkeypatch):
    brain = quiet_brain(isolated_runtime)
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    async def generate(*args, **kwargs):
        brain.note_audience()
        return {"topic": "新话题", "speech": "这是一段已经过时的聊天。现在先照顾刚来的观众。"}
    monkeypatch.setattr(brain.llm, "structured", generate)
    spoken = AsyncMock()
    monkeypatch.setattr(brain, "_speak", spoken)
    assert not asyncio.run(brain._idle_once())
    spoken.assert_not_awaited()
    assert "互动优先" in brain.companion_status


def test_companion_respects_fishing_and_game_phase(isolated_runtime):
    brain = quiet_brain(isolated_runtime)
    brain.fishing.start("会客厅")
    brain.fishing.phase = "hooked"
    assert not brain.companion_available()
    brain.fishing.cancel()
    brain.games.start("drawing", Effective.games())
    assert not brain.companion_available()


def test_ai_companion_keeps_reviewed_auto_music(isolated_runtime, monkeypatch):
    brain = quiet_brain(isolated_runtime)
    store._data["music"].update(enabled=True, ai_dj=True, ai_allowlist="晴天 | 周杰伦")
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    picked = AsyncMock(return_value=(True, "已安排"))
    generated = AsyncMock()
    spoken = AsyncMock()
    monkeypatch.setattr(brain, "ai_song", picked)
    monkeypatch.setattr(brain.llm, "structured", generated)
    monkeypatch.setattr(brain, "_speak", spoken)
    assert asyncio.run(brain._idle_once())
    picked.assert_awaited_once_with("晴天 周杰伦")
    generated.assert_not_awaited()
    spoken.assert_not_awaited()


def test_unchanged_like_totals_do_not_starve_idle_ai(isolated_runtime, monkeypatch):
    async def scenario():
        brain = quiet_brain(isolated_runtime)
        monkeypatch.setattr(LLM, "active", property(lambda self: True))
        monkeypatch.setattr(brain.llm, "structured", AsyncMock(return_value={"topic": "云", "speech": "今天的云像一只小绵羊。我想给它起个轻轻软软的名字。"}))
        monkeypatch.setattr(brain, "_speak", AsyncMock())
        await brain._publish_event(Event(kind="like", like_total=0, like_count=0))
        assert await brain._idle_once()
    asyncio.run(scenario())


@pytest.mark.parametrize("speaking", [False, True])
def test_gift_immediately_interrupts_companion_without_killing_idle_loop(isolated_runtime, monkeypatch, speaking):
    async def scenario():
        brain = quiet_brain(isolated_runtime)
        monkeypatch.setattr(LLM, "active", property(lambda self: True))
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        generated = AsyncMock(return_value={"topic": "海风", "speech": "海风吹过来的时候，会不会顺便带来小鱼的悄悄话？今天想聊一聊这个。"})
        monkeypatch.setattr(brain.llm, "structured", generated if speaking else blocked)
        monkeypatch.setattr(brain, "_speak", blocked if speaking else AsyncMock())
        run = asyncio.create_task(brain.companion_once())
        await asyncio.wait_for(entered.wait(), 1)
        await brain._publish_event(Event(kind="gift", user_id="donor", gift_name="鲜花", gift_count=1))
        assert await asyncio.wait_for(run, 1) is False
        assert not brain._idle_preparing and brain._companion_task is None
        assert brain.gift_show.active
    asyncio.run(scenario())


def test_cancel_idle_loop_cancels_ai_request(isolated_runtime, monkeypatch):
    async def scenario():
        brain = quiet_brain(isolated_runtime)
        monkeypatch.setattr(LLM, "active", property(lambda self: True))
        entered, closed = asyncio.Event(), asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()
        monkeypatch.setattr(brain.llm, "structured", blocked)
        run = asyncio.create_task(brain.companion_once())
        await asyncio.wait_for(entered.wait(), 1)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert closed.is_set() and not brain._idle_preparing and brain._companion_task is None
    asyncio.run(scenario())


def test_sentence_cut_keeps_a_complete_ending():
    assert finish_text("云朵真像一朵棉花糖。这里的风也很温柔。还有半句", 12) == "云朵真像一朵棉花糖。这里的风也很温柔。"
    assert finish_text("没有标点" * 100, 40) == ""


@pytest.mark.parametrize("content,finish,expected", [('{"topic":"中文"}', "stop", {"topic": "中文"}),
    ('```json\n{"topic":"中文"}\n```', "stop", {"topic": "中文"}), ("[]", "stop", None), ("invalid", "stop", None), ('{"topic":"中文"}', "length", None)])
def test_structured_response_parsing_and_usage(monkeypatch, content, finish, expected):
    import importlib
    metrics_module = importlib.import_module("app.metrics")
    usage = Mock()
    monkeypatch.setattr(metrics_module, "metrics", SimpleNamespace(add_usage=usage))
    store._data["llm"].update(api_key="test-key", base_url="https://example.invalid/v1", model="test", prompt_mode="user")
    async def scenario():
        def respond(request):
            payload = json.loads(request.content)
            assert payload["stream"] is False and payload["max_tokens"] == 6000
            assert payload["messages"][0]["role"] == "user"
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = LLM()
            monkeypatch.setattr(model, "_ensure_client", lambda: client)
            assert await model.structured("用中文聊聊", {"主题": "天空"}, max_tokens=99999) == expected
        assert usage.call_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("patch", [{"games": {"rounds": 0}}, {"games": {"round_seconds": 10}}, {"games": {"auto_start": "yes"}},
                                   {"idle": {"chat_chars": 5}}, {"idle": {"topic_preferences": "云" * 1001}}])
def test_new_settings_reject_invalid_values(patch):
    with pytest.raises(ValueError):
        store.update(patch)


def test_game_ws_start_reconnect_stop_without_browser(monkeypatch):
    monkeypatch.setattr(LLM, "active", property(lambda self: True))
    monkeypatch.setattr(LLM, "structured", AsyncMock(return_value=drawing()))
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as socket:
            assert not socket.receive_json()["game"]["active"]
            socket.send_json({"cmd": "game", "action": "drawing"})
            for attempt in range(30):
                message = socket.receive_json()
                if message.get("type") == "game" and message["game"].get("phase") == "question":
                    break
            else:
                pytest.fail("小游戏没有完成出题")
            assert "answer" not in message["game"]["question"]
            with client.websocket_connect("/ws") as reconnect:
                hello = reconnect.receive_json()
                assert hello["game"]["active"]
                assert "answer" not in hello["game"]["question"]
            socket.send_json({"cmd": "game", "action": "stop"})
            for attempt in range(30):
                message = socket.receive_json()
                if message.get("type") == "game" and not message["game"].get("active"):
                    break
            else:
                pytest.fail("小游戏没有退出")
        assert not client.get("/api/ai/context").json()["context"]["game"]["active"]


def test_answer_can_match_an_existing_action_command(monkeypatch):
    from app.brain import intent
    monkeypatch.setattr(intent, "local_intent", lambda text: intent.Intent("motion", motion="peek"))
    async def scenario():
        brain = question_brain()
        event = Event(kind="chat", text="成千上万", user_name="猜中者")
        assert await brain._publish_event(event)
        assert brain._game_answers[0][1] == "成千上万"
        await brain.close_games()
    asyncio.run(scenario())
