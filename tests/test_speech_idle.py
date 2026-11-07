from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from app import main, playlist
from app.brain import scheduler
from app.brain.intent import local_intent
from app.brain.llm import LLM, offline_reply
from app.brain.utterance import FallbackText, complete_utterance
from app.events import Event
from app.native.actions import ACTIONS, pose
from app.native.audio import AudioController
from app.native.line_editor import LineEditor
from app.native.preferences import Preferences
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS, Effective, store
from tests.test_runtime import isolated_runtime


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def test_stream_waits_past_comma_and_target_for_complete_sentence():
    expected = "谢谢你的礼物，今天也很高兴见到你，祝你今晚有个好心情。"

    async def scenario():
        closed = []

        async def stream():
            try:
                for character in expected:
                    yield character
                await asyncio.Future()
            finally:
                closed.append(True)

        assert await complete_utterance(stream(), 12) == expected
        assert closed == [True]

    asyncio.run(scenario())


def test_unfinished_stream_is_not_disguised_as_complete_sentence():
    async def scenario():
        async def stream():
            yield "这是一直没有句号的内容，" * 20
            await asyncio.Future()

        assert await complete_utterance(stream(), 20) == ""

    asyncio.run(scenario())


def test_model_failure_replaces_partial_output_with_complete_template():
    expected = "这是完整的内置话术。" * 20 + "最后一句也会读完。"

    async def stream():
        yield "模型刚说到一半，"
        yield FallbackText(expected)

    assert asyncio.run(complete_utterance(stream(), 20)) == expected


def test_long_offline_template_reads_all_characters(monkeypatch):
    expected = "谢谢你愿意留在这里，" * 15 + "这句话的结尾也必须读出来。"
    store._data["lines"]["offline_intro"] = [expected]
    store._data["pace"]["max_reply_chars"] = 20
    assert offline_reply("你是谁") == expected
    spoken = []
    brain = scheduler.Brain()

    async def speak(text, *args, **kwargs):
        spoken.append(text)

    monkeypatch.setattr(brain, "_speak", speak)
    asyncio.run(brain._chat(Event(kind="chat", text="你是谁"), 1))
    assert spoken == [expected]


def test_speaker_waits_for_actual_playback_before_sending_next(isolated_runtime, monkeypatch):
    bus = isolated_runtime
    owner, other = object(), object()
    bus.clients.update((owner, other))
    bus.playback.register(owner)
    messages = []

    async def broadcast(payload, remember=True):
        messages.append(payload)

    monkeypatch.setattr(bus, "broadcast", broadcast)

    async def scenario():
        async def synthesize(text):
            return b"test audio"

        speaker = scheduler.Speaker()
        speaker.tts = SimpleNamespace(name="none", synthesize=synthesize)
        task = asyncio.create_task(speaker.say("第一句。第二句。"))
        for index in range(100):
            await asyncio.sleep(0)
            voices = [item for item in messages if item["type"] == "voice"]
            if voices:
                break
        first = voices[0]["id"]
        assert speaker.busy and len(voices) == 1
        bus.playback.finish(other, first, "ended")
        await asyncio.sleep(0.01)
        assert len([item for item in messages if item["type"] == "voice"]) == 1
        bus.playback.finish(owner, first, "ended")
        for index in range(100):
            await asyncio.sleep(0)
            voices = [item for item in messages if item["type"] == "voice"]
            if len(voices) == 2:
                break
        assert [item["text"] for item in voices] == ["第一句。", "第二句。"]
        bus.playback.finish(owner, voices[1]["id"], "ended")
        assert await asyncio.wait_for(task, 1)
        assert not speaker.busy and not bus.playback.pending

    asyncio.run(scenario())


@pytest.mark.parametrize("release", ["interrupt", "disconnect", "error", "cancel"])
def test_speech_receipt_cleanup_on_interrupt_failure_and_disconnect(isolated_runtime, monkeypatch, release):
    bus = isolated_runtime
    owner = object()
    bus.clients.add(owner)
    bus.playback.register(owner)
    voices = []

    async def broadcast(payload, remember=True):
        if payload["type"] == "voice":
            voices.append(payload)

    monkeypatch.setattr(bus, "broadcast", broadcast)

    async def scenario():
        async def synthesize(text):
            return b"audio"

        speaker = scheduler.Speaker()
        speaker.tts = SimpleNamespace(name="none", synthesize=synthesize)
        task = asyncio.create_task(speaker.say("完整的一句话。"))
        for index in range(100):
            await asyncio.sleep(0)
            if voices:
                break
        assert voices and speaker.busy
        if release == "interrupt":
            speaker.interrupt()
        elif release == "disconnect":
            bus.playback.remove(owner)
        elif release == "error":
            bus.playback.finish(owner, voices[0]["id"], "error")
        else:
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert not speaker.busy and not speaker._gate.locked() and not bus.playback.pending

    asyncio.run(scenario())


def test_native_queue_keeps_every_segment_and_reports_completion(qt, monkeypatch):
    state = StageState()
    audio = AudioController(state, "http://127.0.0.1:1", muted=True)
    receipts = []
    audio.voice_finished.connect(lambda identifier, status: receipts.append((identifier, status)))
    audio.voice_active = True
    audio.voice_id = "current"
    try:
        for index in range(24):
            audio.handle({"type": "voice", "id": str(index), "text": f"第{index}段中文", "audio": base64.b64encode(b"sample").decode()})
        assert len(audio.queue) == 24 and audio.queue[0][2] == "0"
        audio._voice_status(QMediaPlayer.MediaStatus.EndOfMedia)
        assert receipts == [("current", "ended")]
        audio.stop_voice()
        assert len(receipts) == 25
        assert all(status == "interrupted" for _, status in receipts[1:])
        audio.handle({"type": "voice", "id": "bad", "audio": "not base64!"})
        assert receipts[-1] == ("bad", "error")
    finally:
        audio.close()


def test_long_voice_continues_while_position_advances(qt, monkeypatch):
    audio = AudioController(StageState(), "http://127.0.0.1:1", muted=True)
    errors = []
    audio.notice.connect(errors.append)
    audio.voice_active = True
    audio.voice_started = time.monotonic() - 100
    audio.voice_progress_at = time.monotonic()
    audio.voice_position = -1
    try:
        audio._tick()
        assert audio.voice_active and not errors
        audio.voice_progress_at = time.monotonic() - 31
        audio._tick()
        assert not audio.voice_active and "没有播放进展" in errors[-1]
    finally:
        audio.close()


class FakeClient(QObject):
    response = Signal(str, dict)

    def __init__(self):
        super().__init__()
        self.sent = []
        self.posts = []

    def send(self, payload):
        self.sent.append(payload)

    def get(self, route):
        pass

    def post(self, route, payload):
        self.posts.append((route, payload))


def test_preferences_edit_preview_save_and_reload_chinese(qt):
    client = FakeClient()
    dialog = Preferences(client)
    try:
        client.response.emit("GET /api/config", store.get_all())
        editor = dialog.line_editor
        editor.categories.setCurrentIndex(editor.categories.findData("offline_greet"))
        editor.editor.setPlainText("{name}，欢迎来到深蓝直播间，完整读到最后。\n今天也要开心。")
        editor.editor.moveCursor(editor.editor.textCursor().MoveOperation.Start)
        editor._play()
        assert client.sent[-1]["text"] == "小鱼，欢迎来到深蓝直播间，完整读到最后。"
        editor.categories.setCurrentIndex(editor.categories.findData("welcome"))
        editor.editor.setPlainText("欢迎{wrong}")
        assert not editor.play.isEnabled()
        editor.editor.setPlainText("欢迎{a}，今天也谢谢你的陪伴")
        dialog.idle_actions["heart"].setChecked(True)
        dialog._save()
        patch = client.posts[-1][1]
        assert isinstance(patch["lines"]["offline_greet"], list)
        assert patch["interact"]["enabled"] == DEFAULTS["interact"]["enabled"]
        store.update(patch)
        assert store.lines("welcome") == ["欢迎{a}，今天也谢谢你的陪伴"]
        assert len(store.lines("offline_greet")) == 2
        assert "heart" in Effective.idle()["actions"]
        client.response.emit("GET /api/config", store.get_all())
        editor.categories.setCurrentIndex(editor.categories.findData("offline_greet"))
        assert "完整读到最后" in editor.editor.toPlainText()
    finally:
        dialog.close()


@pytest.mark.parametrize("patch", [{"lines": {"offline_greet": ["{name.__class__}"]}},
                                  {"lines": {"idle_topics": ["{name}"]}},
                                  {"idle": {"action_seconds": 0}}, {"idle": {"actions": ["unknown"]}},
                                  {"idle": {"action_mode": "unknown"}},
                                  {"interact": {"rules": [{"reply": "{wrong}"}]}}])
def test_bad_line_or_idle_config_rejected_without_changes(patch):
    previous = store.get_all()
    with pytest.raises(ValueError):
        store.update(patch)
    assert store.get_all() == previous


@pytest.mark.parametrize("text,motion", [("比个心", "heart"), ("伸个懒腰吧", "stretch"),
                                       ("探个头", "peek"), ("飘起来", "float"), ("打个盹", "sleepy")])
def test_new_actions_accept_requests_render_and_return_to_rest(qt, text, motion):
    assert local_intent(text).motion == motion
    assert local_intent("不要" + text).action == "chat"
    reaction = {"motion": motion, "started": 100, **ACTIONS[motion]}
    assert pose(reaction, 102)["strength"] > 0
    assert pose(reaction, 110)["strength"] == 0
    state = StageState()
    state.reaction, state.reaction_until = reaction, 110
    for width, height in ((360, 640), (640, 360)):
        frame = Scene(width, height).render(state.snapshot(), 2, 102)
        assert (frame.width(), frame.height()) == (width, height)


def quiet_brain(bus):
    brain = scheduler.Brain()
    brain.last_activity_at = time.time() - 100
    bus.stats["started_at"] = time.time() - 100
    return brain


def test_idle_actions_rotate_and_defer_to_viewer_activity(isolated_runtime, monkeypatch):
    bus = isolated_runtime
    events = []

    async def broadcast(payload, remember=True):
        events.append(payload)

    monkeypatch.setattr(bus, "broadcast", broadcast)
    store._data["idle"]["actions"] = ["heart", "peek"]
    brain = quiet_brain(bus)

    async def scenario():
        assert await brain.idle_action()
        assert events[-1]["motion"] == "heart"
        assert not await brain.idle_action()
        brain._last_idle_action_at = 0
        assert await brain.idle_action()
        assert events[-1]["motion"] == "peek"
        brain._last_idle_action_at = 0
        await brain._publish_event(Event(kind="like", gift_count=1))
        assert not await brain.idle_action()
        brain.last_activity_at -= 100
        brain.speaker.busy = True
        assert not await brain.idle_action()
        brain.speaker.busy = False
        brain._pending_replies.append(Event(kind="chat", text="你好"))
        assert not await brain.idle_action()
        brain._pending_replies.clear()
        brain.likes.active = {"motion": "bicycle"}
        assert not await brain.idle_action()
        brain.likes.active = None
        brain.paused = True
        assert not await brain.idle_action(manual=True)

    asyncio.run(scenario())


def test_idle_speech_uses_full_editable_lines_and_respects_music(isolated_runtime, monkeypatch):
    store._data["templates"].update(enabled=True, idle=True, prefer_llm=False)
    store._data["idle"]["gameplay_invites"] = False
    expected = "我想邀请潜水的朋友一起聊聊天，先说说今天发生的开心事，完整的一句话会说到最后。"
    store._data["lines"]["idle_topics"] = [expected]
    store._data["lines"]["idle_invites"] = ["想看我探头还是比心？发弹幕试试吧。"]
    brain = quiet_brain(isolated_runtime)
    spoken = []

    async def speak(text, *args, **kwargs):
        spoken.append(text)

    monkeypatch.setattr(brain, "_speak", speak)

    async def scenario():
        assert await brain._idle_once()
        assert spoken == [expected]
        brain.last_speak_at = 0
        assert await brain._idle_once()
        assert spoken[-1] == store.lines("idle_invites")[0]
        brain.last_speak_at = 0
        playlist.queue.add(playlist.make_track({"title": "中文歌曲", "url": "local"}))
        playlist.queue.next_track()
        assert not await brain._idle_once()
        store._data["idle"]["speech_during_music"] = True
        assert await brain._idle_once()

    asyncio.run(scenario())


def test_arriving_chat_cancels_pending_idle_utterance(isolated_runtime, monkeypatch):
    store._data["templates"].update(enabled=True, idle=True, prefer_llm=True)
    brain = quiet_brain(isolated_runtime)
    monkeypatch.setattr(LLM, "active", property(lambda self: True))

    async def generate(*args, **kwargs):
        brain.last_activity_at = time.time()
        return "这条冷场话题已经过时了。"

    async def unexpected(*args, **kwargs):
        raise AssertionError("有新消息时不应插入冷场话题")

    monkeypatch.setattr(brain, "_llm_line", generate)
    monkeypatch.setattr(brain, "_speak", unexpected)
    assert not asyncio.run(brain._idle_once())


def test_ambient_animation_does_not_replace_video_or_manual_action():
    state = StageState()
    state.apply({"type": "reaction", "motion": "dance", "manual": True})
    state.apply({"type": "reaction", "motion": "sleepy", "ambient": True})
    assert state.reaction["motion"] == "dance"
    state.reaction = {}
    state.reaction_until = 0
    state.like_goal = {"active": {"motion": "bicycle"}}
    state.apply({"type": "reaction", "motion": "sleepy", "ambient": True})
    assert not state.reaction
