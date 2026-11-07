from __future__ import annotations

import json
import os
import time
import urllib.request

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication

from app.native.audio import AudioController
from app.native.output import VideoOutput, jpeg_frame
from app.native.scene import RenderWorker, Scene
from app.native.state import FrameStore, StageState
from app.native.window import Preview
from app.native.actions import ACTIONS, pose
from app.native.music_view import music_view


@pytest.fixture(scope="module")
def qt():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(app, duration):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def test_reconnect_does_not_replay_old_speech():
    state = StageState()
    state.apply({"type": "hello", "paused": True, "recent": [
        {"type": "voice", "audio": "old"}, {"type": "subtitle", "text": "旧字幕"},
        {"type": "event", "event": {"id": "中文", "kind": "chat", "text": "你好"}}]})
    assert state.paused and len(state.events) == 1
    assert state.subtitle_until == 0
    state.apply({"type": "subtitle", "text": "暂停期间"})
    assert state.subtitle_until == 0


@pytest.mark.parametrize("width,height", [(720, 1280), (1280, 720)])
def test_scene_renders_chinese_and_distinct_intermission(qt, width, height):
    state = StageState()
    state.connected = True
    state.config = {"name": "深蓝 · 中文直播", "stage": {"scale": 0.64, "offset_y": -0.11}}
    state.apply({"type": "event", "event": {"kind": "gift", "userName": "测试观众", "giftName": "小心心", "giftCount": 2}})
    scene = Scene(width, height)
    normal = scene.render(state.snapshot(), 1, time.monotonic())
    state.apply({"type": "state", "paused": True})
    paused = scene.render(state.snapshot(), 1, time.monotonic())
    assert normal.width() == width and normal.height() == height
    assert normal != paused


def test_hidden_preview_keeps_output_frames_advancing(qt):
    state, frames = StageState(), FrameStore()
    worker = RenderWorker(state, frames, 360, 640, 30)
    preview = Preview(frames)
    output = VideoOutput(frames, 0)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    worker.start()
    try:
        preview.show()
        pump(qt, 0.2)
        before = frames.sequence
        preview.showMinimized()
        pump(qt, 1.2)
        assert frames.sequence - before >= 12
        assert not frames.error
        with opener.open(output.url.replace("/live.mjpeg", "/status"), timeout=3) as response:
            assert json.load(response)["frames"] >= frames.sequence - 2
        with opener.open(output.url, timeout=3) as response:
            assert response.readline() == b"--frame\r\n"
            assert b"image/jpeg" in response.readline()
            length = int(response.readline().split(b":")[1])
            assert response.readline() == b"\r\n"
            frame = QImage.fromData(response.read(length))
            assert frame.size() == frames.image.size()
            assert response.read(2) == b"\r\n"
            assert response.readline() == b"--frame\r\n"
        with opener.open(output.url, timeout=3) as response:
            started = time.monotonic()
            for index in range(16):
                assert response.readline() == b"--frame\r\n"
                response.readline()
                length = int(response.readline().split(b":")[1])
                response.readline()
                response.read(length)
                response.read(2)
            elapsed = time.monotonic() - started
            assert 0.45 < elapsed < 2
        assert output.stream_fps == 25
        assert QImage.fromData(jpeg_frame(frames)).width() == 360
    finally:
        preview.close()
        worker.stop()
        output.close()
    assert not worker.is_alive()


def test_pcm_audio_drives_mouth_and_pause_clears_it(qt):
    state = StageState()
    audio = AudioController(state, "http://127.0.0.1:1", muted=True)
    fmt = QAudioFormat()
    fmt.setSampleRate(16000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    samples = QByteArray((4096).to_bytes(2, "little", signed=True) * 512)
    try:
        audio.voice_active = True
        audio._samples(QAudioBuffer(samples, fmt))
        assert state.mouth > 0.5
        message = {"type": "state", "paused": True}
        state.apply(message)
        audio.handle(message)
        assert state.mouth == 0 and not audio.voice_active
    finally:
        audio.close()


def music_state():
    state = StageState()
    state.connected = True
    state.apply({"type": "music", "music": {"playing": True, "elapsed": 90,
                 "current": {"id": "first", "title": "夜空中的鲸鱼", "by": "深蓝", "duration": 180, "requestedBy": "小鱼"},
                 "queue": [{"id": "second", "title": "下一首中文歌曲", "by": "星海"}]}})
    return state


def test_player_uses_actual_audio_clock_and_keeps_next_track():
    state = music_state()
    state.set_playback("first", 12, 180, "playing")
    snapshot = state.snapshot()
    view = music_view(snapshot, snapshot["playback"]["at"] + 0.1)
    assert view["position"] == pytest.approx(12.1)
    assert view["next_title"] == "下一首中文歌曲"
    assert view["requester"] == "小鱼"
    assert view["progress"] == pytest.approx(12.1 / 180)
    state.apply({"type": "state", "paused": True})
    paused = music_view(state.snapshot(), snapshot["playback"]["at"] + 5)
    assert not paused["playing"] and paused["position"] == 12
    assert paused["next_title"] == view["next_title"]


def test_player_freezes_during_buffering_and_handles_unknown_duration():
    state = music_state()
    state.music["current"]["duration"] = 0
    state.set_playback("first", 9, 0, "buffering")
    view = music_view(state.snapshot(), time.monotonic() + 10)
    assert view["position"] == 9 and view["progress"] is None
    assert view["status"] == "正在缓冲" and view["time"] == "00:09 / --:--"
    state.connected = False
    assert music_view(state.snapshot(), time.monotonic())["status"] == "等待服务连接"


def test_player_drops_old_clock_when_track_changes_and_resets():
    state = music_state()
    state.set_playback("first", 120, 180, "playing")
    state.apply({"type": "music", "music": {"playing": True, "elapsed": 0,
                 "current": {"id": "second", "title": "下一首中文歌曲"}, "queue": []}})
    view = music_view(state.snapshot(), time.monotonic())
    assert view["position"] == 0 and not view["playing"]
    assert view["title"] == "下一首中文歌曲" and view["queue_count"] == 0
    state.apply({"type": "reset"})
    view = music_view(state.snapshot(), time.monotonic())
    assert view["status"] == "等待点歌" and view["progress"] is None


def test_automatic_reactions_respect_priority_duplicate_events_and_pause(monkeypatch):
    from app.native import state as state_module
    now = [100.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: now[0])
    state = StageState()
    event = {"type": "event", "event": {"id": "dance", "kind": "chat", "text": "跳个舞吧"}}
    state.apply(event)
    assert state.reaction["motion"] == "dance"
    initial = state.reaction.copy()
    now[0] += 0.2
    state.apply(event)
    state.apply({"type": "motion", "name": "happy"})
    assert state.reaction == initial and state.danmu == 1
    state.apply({"type": "reaction", "motion": "shy", "manual": True})
    assert state.reaction["motion"] == "shy"
    before_pause = state.reaction.copy()
    state.apply({"type": "state", "paused": True})
    state.apply({"type": "reaction", "motion": "dance", "manual": True})
    assert state.reaction == before_pause
    now[0] += 10
    state.apply({"type": "state", "paused": False})
    assert state.reaction["motion"] == "shy"
    assert state.reaction["started"] == before_pause["started"] + 10


def test_reaction_rules_honor_effects_and_disable_switch():
    state = StageState()
    state.apply({"type": "reaction", "motion": "happy", "rule_id": "r-cmt-dance", "effect": "confetti"})
    assert state.reaction["motion"] == "dance" and state.reaction["effect"] == "confetti"
    state.apply({"type": "config_changed", "config": {"interact": {"enabled": False}}})
    state.apply({"type": "event", "event": {"id": "gift", "kind": "gift", "giftValue": 600}})
    assert not state.reaction


@pytest.mark.parametrize("motion", ACTIONS)
def test_actions_ease_back_to_neutral_and_render(qt, motion):
    state = music_state()
    state.config["stage"] = {"scale": 0.64, "offset_y": -0.11}
    state.apply({"type": "reaction", "motion": motion, "manual": True})
    reaction = state.reaction
    started = reaction["started"]
    assert pose(reaction, started)["strength"] == 0
    assert pose(reaction, started + 1)["strength"] > 0
    finished = pose(reaction, started + reaction["duration"])
    assert (finished["x"], finished["y"], finished["tilt"]) == pytest.approx((0, 0, 0), abs=1e-9)
    assert finished["scale_x"] == finished["scale_y"] == 1
    scene = Scene(360, 640)
    animated = scene.render(state.snapshot(), 1, started + 1)
    state.reaction = {}
    state.reaction_until = 0
    neutral = scene.render(state.snapshot(), 1, started + 1)
    assert animated != neutral


def test_config_refresh_does_not_seek_back_to_old_song_snapshot(qt):
    state = music_state()
    audio = AudioController(state, "http://127.0.0.1:1", muted=True)
    calls = []

    class Player:
        def isSeekable(self):
            return True

        def position(self):
            return 90000

        def setPosition(self, value):
            calls.append(value)

        def play(self):
            pass

    original = audio.music
    audio.music = Player()
    audio.track_id = "first"
    audio.pending_music = {**state.music, "elapsed": 0}
    audio._sync_playback = lambda: None
    try:
        audio.handle({"type": "config_changed"})
        assert not calls
        audio.handle({"type": "music", "music": audio.pending_music})
        assert calls == [0]
    finally:
        audio.music = original
        audio.close()
