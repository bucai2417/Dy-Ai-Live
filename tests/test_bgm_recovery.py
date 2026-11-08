from __future__ import annotations

import wave
import time

import pytest
from PySide6.QtMultimedia import QMediaPlayer

from app.native.audio import AudioController
from app.native.preferences import Preferences
from app.native.state import StageState
from app.settings_store import store
from tests.test_renderer import qt, pump
from tests.test_runtime import isolated_runtime
from tests.test_speech_idle import FakeClient


def make_audio(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 16000)


def wait_for(qt, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        pump(qt, .03)
    assert condition()


def test_configured_file_with_disabled_switch_is_explained_and_picker_enables(qt, tmp_path, monkeypatch):
    path = tmp_path / "中文背景音乐.wav"
    make_audio(path)
    store.update({"bgm": {"path": str(path), "enabled": False}})
    state = StageState()
    state.connected = True
    state.config["bgm"] = store.section("bgm")
    audio = AudioController(state, "http://127.0.0.1:1", muted=True)
    client = FakeClient()
    dialog = Preferences(client, audio=audio)
    try:
        audio.handle({"type": "hello"})
        client.response.emit("GET /api/config", store.get_all())
        assert audio.bgm.source().isEmpty() and "未开启" in dialog.bgm_status.text()
        monkeypatch.setattr("app.native.preferences.QFileDialog.getOpenFileName", lambda *args: (str(path), ""))
        dialog._choose_bgm()
        assert dialog.fields[("bgm", "enabled")].isChecked()
        dialog._save()
        patch = client.posts[-1][1]
        assert patch["bgm"]["path"] == str(path) and patch["bgm"]["enabled"]
        state.config["bgm"] = patch["bgm"]
        audio.handle({"type": "config_changed"})
        pump(qt, .15)
        assert audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        assert "静音" in dialog.bgm_status.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        audio.close()


def test_stale_failed_and_finished_song_state_does_not_silence_bgm(qt, tmp_path):
    path = tmp_path / "中文 BGM.wav"
    make_audio(path)
    state = StageState()
    state.connected = True
    state.config["bgm"] = {"enabled": True, "path": str(path), "volume": .2}
    audio = AudioController(state, "http://127.0.0.1:1", muted=True)
    try:
        audio.handle({"type": "hello", "music": {"playing": True}})
        pump(qt, .15)
        assert audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        audio.handle({"type": "music", "music": {"playing": True, "current": {"id": "missing", "url": (tmp_path / "missing.wav").as_uri()}}})
        wait_for(qt, lambda: audio.ended_track == "missing")
        audio.apply_bgm()
        assert audio.ended_track == "missing"
        assert audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        audio.handle({"type": "music", "music": {"playing": True, "current": {"id": "real", "url": path.as_uri()}}})
        wait_for(qt, lambda: audio._song_audible() and audio.music.isSeekable())
        audio.apply_bgm()
        assert audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PausedState
        assert "点歌" in audio.bgm_status()
        audio.music.setPosition(1990)
        wait_for(qt, lambda: audio.ended_track == "real")
        audio.apply_bgm()
        assert audio.ended_track == "real"
        assert audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    finally:
        audio.close()


def test_missing_file_retry_and_quoted_chinese_path(qt, tmp_path):
    path = tmp_path / "稍后出现的音乐.wav"
    state = StageState()
    state.connected = True
    state.config["bgm"] = {"enabled": True, "path": '"' + str(path) + '"'}
    audio = AudioController(state, "http://127.0.0.1:1", muted=True)
    try:
        audio.handle({"type": "hello"})
        assert audio.bgm_failed and "文件不存在" in audio.bgm_status()
        make_audio(path)
        audio.retry_bgm()
        pump(qt, .15)
        assert not audio.bgm_failed
        assert audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        assert audio.bgm.source().toLocalFile() == str(path).replace("\\", "/")
    finally:
        audio.close()
