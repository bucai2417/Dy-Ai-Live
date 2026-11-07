from __future__ import annotations

import base64
import time
from collections import deque
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaPlayer

from ..paths import ROOT
from .state import StageState


class AudioController(QObject):
    notice = Signal(str)
    music_ended = Signal(str)
    voice_finished = Signal(str, str)

    def __init__(self, state: StageState, base_url: str, muted=False):
        super().__init__()
        self.state, self.base_url, self.muted = state, base_url, muted
        self.queue = deque()
        self.voice = QMediaPlayer(self)
        self.voice_output = QAudioOutput(self)
        self.voice.setAudioOutput(self.voice_output)
        self.voice_output.setMuted(muted)
        self.samples = QAudioBufferOutput(self)
        self.voice.setAudioBufferOutput(self.samples)
        self.samples.audioBufferReceived.connect(self._samples)
        self.voice.mediaStatusChanged.connect(self._voice_status)
        self.voice.errorOccurred.connect(self._voice_error)
        self.buffer = None
        self.voice_active = False
        self.voice_started = 0.0
        self.current_text = ""
        self.voice_id = ""
        self.voice_position = 0
        self.voice_progress_at = 0.0
        self.music = QMediaPlayer(self)
        self.music_output = QAudioOutput(self)
        self.music.setAudioOutput(self.music_output)
        self.music_output.setMuted(muted)
        self.music.mediaStatusChanged.connect(self._music_status)
        self.music.errorOccurred.connect(self._music_error)
        self.music.positionChanged.connect(lambda _: self._sync_playback())
        self.music.durationChanged.connect(lambda _: self._sync_playback())
        self.music.playbackStateChanged.connect(lambda _: self._sync_playback())
        self.track_id = ""
        self.ended_track = ""
        self.seek_to = 0
        self.pending_music = {}
        self.bgm = QMediaPlayer(self)
        self.bgm_output = QAudioOutput(self)
        self.bgm.setAudioOutput(self.bgm_output)
        self.bgm.setLoops(QMediaPlayer.Loops.Infinite)
        self.bgm_output.setMuted(muted)
        self.bgm_output.setVolume(0)
        self.bgm.errorOccurred.connect(self._bgm_error)
        self.bgm_path = ""
        self.bgm_failed = False
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self._tick)
        self.watchdog.start(100)

    def handle(self, message):
        kind = message.get("type")
        if kind == "voice" and not self.state.paused:
            identifier = str(message.get("id") or "")
            try:
                audio = base64.b64decode(message.get("audio") or "", validate=True)
            except (ValueError, TypeError):
                self.voice_finished.emit(identifier, "error")
                return
            if audio:
                self.queue.append((audio, str(message.get("text") or ""), identifier))
                if not self.voice_active:
                    self._next_voice()
            else:
                self.voice_finished.emit(identifier, "error")
        elif kind == "voice":
            self.voice_finished.emit(str(message.get("id") or ""), "interrupted")
        elif kind in ("speech_interrupt", "reset"):
            self.stop_voice()
            if kind == "reset":
                self.music.stop()
                self.track_id = ""
                self.pending_music = {}
        elif kind == "state":
            if message.get("paused"):
                self.stop_voice()
                self.music.pause()
            else:
                self.apply_music(self.pending_music, synchronize=False)
        elif kind in ("hello", "music"):
            if kind == "hello" and self.state.paused:
                self.stop_voice()
            self.apply_music(message.get("music") or {})
        elif kind == "config_changed":
            self.apply_music(self.pending_music, synchronize=False)
        if kind in ("hello", "config_changed"):
            self.configure_bgm()
        if kind in ("hello", "config_changed", "state", "music", "reset"):
            self.apply_bgm()

    def configure_bgm(self):
        cfg = self.state.config.get("bgm") or {}
        path = str(cfg.get("path") or "").strip()
        if not cfg.get("enabled") or not path:
            self.bgm.stop()
            self.bgm.setSource(QUrl())
            self.bgm_path = ""
            self.bgm_failed = False
            return
        source = Path(path).expanduser()
        if not source.is_absolute():
            source = ROOT / source
        path = str(source)
        if path == self.bgm_path and not self.bgm_failed:
            return
        self.bgm.stop()
        self.bgm.setSource(QUrl())
        self.bgm_path = path
        self.bgm_failed = not source.is_file()
        if self.bgm_failed:
            self.notice.emit("背景 BGM 文件不存在，请在设置中重新选择：" + path)
            return
        self.bgm_output.setVolume(float(cfg.get("volume", .2)))
        self.bgm.setSource(QUrl.fromLocalFile(path))

    def apply_bgm(self):
        cfg = self.state.config.get("bgm") or {}
        song_playing = self.pending_music.get("playing") and self.state.config.get("music", {}).get("enabled", True)
        should_play = (cfg.get("enabled") and self.bgm_path and not self.bgm_failed
                       and self.state.connected and not self.state.paused and not song_playing)
        if should_play:
            if self.bgm.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.bgm.play()
        elif self.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.bgm.pause()

    def _bgm_error(self, error, message):
        self.bgm_failed = True
        self.notice.emit("背景 BGM 无法播放，请重新选择音频文件：" + message)

    def _next_voice(self):
        self.voice_active = False
        self.voice.stop()
        self.voice.setSource(QUrl())
        if self.buffer:
            self.buffer.deleteLater()
            self.buffer = None
        if not self.queue or self.state.paused:
            self.voice_active = False
            self.state.set_mouth(0)
            return
        audio, self.current_text, self.voice_id = self.queue.popleft()
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(audio))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.voice_active = True
        self.voice_started = time.monotonic()
        self.voice_progress_at = self.voice_started
        self.voice_position = 0
        self.state.apply({"type": "subtitle", "text": self.current_text})
        self.voice.setSourceDevice(self.buffer, QUrl("voice.mp3"))
        self.voice.play()

    def _voice_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.voice_active:
            self.voice_active = False
            self.voice_finished.emit(self.voice_id, "ended")
            self.voice_id = ""
            QTimer.singleShot(0, self._advance_voice)

    def _voice_error(self, error, message):
        if self.voice_active:
            self.notice.emit("语音播放失败，已保留字幕：" + message)
            self.voice_active = False
            self.voice_finished.emit(self.voice_id, "error")
            self.voice_id = ""
            QTimer.singleShot(0, self._advance_voice)

    def _advance_voice(self):
        if not self.voice_active:
            self._next_voice()

    def _samples(self, buffer):
        if not buffer.isValid() or not self.voice_active or self.state.paused:
            self.state.set_mouth(0)
            return
        formats = {QAudioFormat.SampleFormat.Int16: (np.int16, 32768),
                   QAudioFormat.SampleFormat.Int32: (np.int32, 2147483648),
                   QAudioFormat.SampleFormat.Float: (np.float32, 1),
                   QAudioFormat.SampleFormat.UInt8: (np.uint8, 128)}
        dtype, divisor = formats.get(buffer.format().sampleFormat(), (None, 1))
        if dtype is None:
            return
        samples = np.frombuffer(buffer.constData(), dtype=dtype, count=buffer.sampleCount()).astype(np.float32)
        if dtype == np.uint8:
            samples -= 128
        if samples.size:
            rms = float(np.sqrt(np.mean((samples / divisor) ** 2)))
            self.state.set_mouth(min(1, rms * 7))

    def stop_voice(self):
        identifiers = [item[2] for item in self.queue]
        if self.voice_active:
            identifiers.insert(0, self.voice_id)
        self.queue.clear()
        self.voice_active = False
        self.voice_id = ""
        self.voice.stop()
        self.voice.setSource(QUrl())
        if self.buffer:
            self.buffer.deleteLater()
            self.buffer = None
        self.state.set_mouth(0)
        for identifier in identifiers:
            self.voice_finished.emit(identifier, "interrupted")

    def apply_music(self, message, synchronize=True):
        self.pending_music = message
        current = message.get("current") or {}
        identifier = current.get("id") or ""
        if identifier != self.track_id:
            self.music.stop()
            self.track_id = identifier
            self.ended_track = ""
            source = str(current.get("url") or "")
            self.seek_to = max(0, int(float(message.get("elapsed") or 0) * 1000))
            if identifier and source:
                self.music.setSource(QUrl(self.base_url + source if source.startswith("/") else source))
        if not identifier:
            self.music.stop()
            self.music.setSource(QUrl())
        elif message.get("playing") and not self.state.paused and self.state.config.get("music", {}).get("enabled", True):
            elapsed = max(0, int(float(message.get("elapsed") or 0) * 1000))
            if synchronize and self.music.isSeekable() and abs(self.music.position() - elapsed) > 1500:
                self.music.setPosition(elapsed)
            self.music.play()
        else:
            self.music.pause()
        self._sync_playback()

    def _sync_playback(self):
        media = self.music.mediaStatus()
        if media == QMediaPlayer.MediaStatus.InvalidMedia:
            status = "error"
        elif media == QMediaPlayer.MediaStatus.EndOfMedia:
            status = "ended"
        elif media in (QMediaPlayer.MediaStatus.BufferingMedia, QMediaPlayer.MediaStatus.StalledMedia):
            status = "buffering"
        elif media in (QMediaPlayer.MediaStatus.NoMedia, QMediaPlayer.MediaStatus.LoadingMedia):
            status = "loading"
        elif self.music.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            status = "playing"
        else:
            status = "paused"
        self.state.set_playback(self.track_id, self.music.position() / 1000, self.music.duration() / 1000, status)

    def _music_status(self, status):
        self._sync_playback()
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self.seek_to:
            self.music.setPosition(self.seek_to)
            self.seek_to = 0
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._report_ended()

    def _music_error(self, error, message):
        self.notice.emit("歌曲无法播放，正在切换：" + message)
        self._report_ended()

    def _report_ended(self):
        if self.track_id and self.track_id != self.ended_track:
            self.ended_track = self.track_id
            self.music_ended.emit(self.track_id)

    def _tick(self):
        self._sync_playback()
        if self.voice_active:
            position = self.voice.position()
            if position != self.voice_position:
                self.voice_position = position
                self.voice_progress_at = time.monotonic()
            elif time.monotonic() - self.voice_progress_at > 30:
                self._voice_error(None, "语音连续 30 秒没有播放进展，请检查音频设备")
        if self.voice_active:
            with self.state.lock:
                self.state.subtitle = self.current_text
                self.state.subtitle_until = time.monotonic() + 2
        cfg = self.state.config.get("music") or {}
        duck = self.voice_active or self.state.video_image is not None
        target = float(cfg.get("volume", 0.6)) * (float(cfg.get("duck", 0.35)) if duck else 1)
        current = self.music_output.volume()
        self.music_output.setVolume(current + (target - current) * 0.35)
        self.apply_bgm()
        bgm = self.state.config.get("bgm") or {}
        target = float(bgm.get("volume", .2)) * (float(bgm.get("duck", .25)) if duck else 1)
        current = self.bgm_output.volume()
        self.bgm_output.setVolume(current + (target - current) * .25)

    def mute(self, enabled):
        self.muted = enabled
        self.voice_output.setMuted(enabled)
        self.music_output.setMuted(enabled)
        self.bgm_output.setMuted(enabled)

    def close(self):
        self.watchdog.stop()
        self.stop_voice()
        self.music.stop()
        self.music.setSource(QUrl())
        self.bgm.stop()
        self.bgm.setSource(QUrl())
