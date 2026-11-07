from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


ROOT = Path(__file__).resolve().parents[2]
FRAME_WIDTH, FRAME_HEIGHT, FRAME_RATE = 320, 512, 30
MAX_SECONDS = 8


@dataclass(frozen=True)
class VideoClip:
    frames: tuple
    audio: bytes
    fps: int = FRAME_RATE

    @property
    def duration(self):
        return len(self.frames) / self.fps


class ClipDecoder:
    def __init__(self):
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def _run(self, arguments, check=True):
        import imageio_ffmpeg
        if self.cancelled.is_set():
            raise RuntimeError("视频加载已取消")
        with self.lock:
            if self.cancelled.is_set():
                raise RuntimeError("视频加载已取消")
            self.process = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", *arguments],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            process = self.process
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("视频解码超时，请使用较短的本地视频") from None
        finally:
            with self.lock:
                self.process = None
        if self.cancelled.is_set():
            raise RuntimeError("视频加载已取消")
        if check and process.returncode:
            raise RuntimeError("视频无法解码，请检查视频文件或编码格式")
        return stderr.decode("utf-8", errors="replace"), process.returncode

    def decode(self, source: Path) -> VideoClip:
        if not source.is_file():
            raise RuntimeError("找不到互动视频，请在设置中选择本地文件")
        if source.stat().st_size > 64 * 1024 * 1024:
            raise RuntimeError("互动视频不能超过 64 MB")
        probe, status = self._run(["-i", str(source)], check=False)
        match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", probe)
        if not match:
            raise RuntimeError("无法读取互动视频时长")
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
        if duration <= 0 or duration > MAX_SECONDS:
            raise RuntimeError(f"互动短片需在 {MAX_SECONDS} 秒以内")
        codec = ["-c:v", "libvpx-vp9"] if re.search(r"Video: vp9\b", probe) else []
        with tempfile.TemporaryDirectory(prefix="live-clip-") as temporary:
            frame_path, audio_path = Path(temporary) / "frames.rgba", Path(temporary) / "audio.wav"
            filter_graph = (f"fps={FRAME_RATE},scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=decrease,"
                            f"format=rgba,pad={FRAME_WIDTH}:{FRAME_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black@0")
            self._run(["-v", "error", "-threads", "1", *codec, "-i", str(source), "-t", str(MAX_SECONDS),
                       "-filter_threads", "1", "-an", "-vf", filter_graph, "-frames:v", str(MAX_SECONDS * FRAME_RATE),
                       "-pix_fmt", "rgba", "-f", "rawvideo", "-y", str(frame_path)])
            frames = []
            frame_bytes = FRAME_WIDTH * FRAME_HEIGHT * 4
            with frame_path.open("rb") as stream:
                while not self.cancelled.is_set():
                    data = stream.read(frame_bytes)
                    if not data:
                        break
                    if len(data) != frame_bytes:
                        raise RuntimeError("视频帧数据不完整")
                    image = QImage(data, FRAME_WIDTH, FRAME_HEIGHT, FRAME_WIDTH * 4, QImage.Format.Format_RGBA8888)
                    frames.append(image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
            if not frames or self.cancelled.is_set():
                raise RuntimeError("视频加载已取消或没有有效画面")
            audio = b""
            if "Audio:" in probe:
                self._run(["-v", "error", "-i", str(source), "-t", str(MAX_SECONDS), "-vn", "-ac", "2", "-ar", "48000",
                           "-c:a", "pcm_s16le", "-y", str(audio_path)])
                audio = audio_path.read_bytes()
            return VideoClip(tuple(frames), audio)


class VideoController(QObject):
    notice = Signal(str)
    decoded = Signal(int, object, str)

    def __init__(self, state, muted=False):
        super().__init__()
        self.state = state
        self.clip = None
        self.signature = None
        self.generation = 0
        self.jobs = []
        self.loading = False
        self.queue = deque()
        self.seen = deque(maxlen=2048)
        self.active = False
        self.started = 0.0
        self.elapsed = 0.0
        self.suspended = False
        self.closed = False
        self.last_frame = -1
        self.gift_id = ""
        self.gift_played = ""
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.output.setMuted(muted)
        self.player.setAudioOutput(self.output)
        self.player.errorOccurred.connect(lambda error, message: self.notice.emit("视频声音播放失败：" + message))
        self.player.mediaStatusChanged.connect(self._audio_loaded)
        self.buffer = None
        self.decoded.connect(self._loaded)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def configure(self):
        stage = self.state.config.get("stage") or {}
        self.output.setVolume(float(stage.get("video_volume", 0.8)))
        if not stage.get("video_enabled", True):
            self.stop()
            self._cancel_loading()
            self.clip = None
            self.signature = None
            return
        source = Path(str(stage.get("video_path") or "data/interaction-media/奶蛙.webm"))
        if not source.is_absolute():
            source = ROOT / source
        try:
            stamp = source.stat().st_mtime_ns
        except OSError:
            stamp = 0
        signature = (str(source), stamp)
        if signature == self.signature:
            return
        self.stop()
        self._cancel_loading()
        self.signature = signature
        self.clip = None
        self.loading = True
        generation = self.generation
        decoder = ClipDecoder()

        def load():
            try:
                clip = decoder.decode(source)
                if not decoder.cancelled.is_set():
                    self.decoded.emit(generation, clip, "")
            except Exception as exc:
                if not decoder.cancelled.is_set():
                    self.decoded.emit(generation, None, str(exc))

        thread = threading.Thread(target=load, name="native-video-loader", daemon=True)
        self.jobs = [(worker, job) for worker, job in self.jobs if job.is_alive()]
        self.jobs.append((decoder, thread))
        thread.start()

    def _cancel_loading(self):
        self.generation += 1
        for decoder, thread in self.jobs:
            if thread.is_alive():
                decoder.cancel()
        self.loading = False

    def _loaded(self, generation, clip, error):
        if self.closed or generation != self.generation:
            return
        self.loading = False
        self.clip = clip
        if error:
            self.queue.clear()
            self.state.set_video(None)
            self.notice.emit("互动视频未就绪：" + error)
        else:
            self.notice.emit(f"奶蛙视频已就绪 · {clip.duration:.2f} 秒 · 透明叠加")
            self._next()

    def handle(self, message):
        kind = message.get("type")
        if kind in ("hello", "config_changed"):
            self.configure()
        if kind in ("hello", "gift_show", "config_changed"):
            self.sync_gift()
        if kind == "video" and message.get("clip") == "milk-frog":
            self.trigger(message.get("event_id"))
        elif kind == "reset":
            self.gift_id = self.gift_played = ""
            self.stop()
            self.seen.clear()
        elif kind in ("state", "hello"):
            self.set_paused(self.state.paused)

    def trigger(self, identifier=None):
        if self.closed or self.gift_id or self.state.paused or not self.state.config.get("stage", {}).get("video_enabled", True):
            return False
        if identifier and identifier in self.seen:
            return False
        if len(self.queue) >= 10:
            self.notice.emit("互动视频已排满 10 次，请稍后再试")
            return False
        if self.clip is None and not self.loading:
            self.signature = None
            self.configure()
        if identifier:
            self.seen.append(identifier)
        self.queue.append(identifier or "manual")
        if not self.active:
            self._next()
        else:
            self.state.set_video(self.state.video_image, len(self.queue))
        return True

    def _next(self):
        if self.active or self.clip is None or self.state.paused:
            return
        offset = 0.0
        if self.gift_id:
            from .gift_scene import gift_view
            show = gift_view(self.state.snapshot(), time.monotonic())
            if (show.get("active") or {}).get("effect") != "video" or self.gift_played == self.gift_id:
                return
            offset = show["elapsed"]
            self.gift_played = self.gift_id
            if offset >= self.clip.duration:
                return
        elif self.queue:
            self.queue.popleft()
        else:
            return
        self.active = True
        self.suspended = False
        self.elapsed = offset
        self.started = time.monotonic() - offset
        self.last_frame = -1
        if self.clip.audio:
            self.buffer = QBuffer(self)
            self.buffer.setData(QByteArray(self.clip.audio))
            self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self.player.setSourceDevice(self.buffer, QUrl("interaction.wav"))
            self.player.play()
        self._tick()

    def sync_gift(self):
        show = (self.state.gift_show or {}).get("active") or {}
        identifier = show.get("id", "")
        if identifier != self.gift_id:
            if identifier or self.gift_id:
                self.stop()
            self.gift_id = identifier
            self.gift_played = ""
        if self.gift_id and self.active:
            from .gift_scene import gift_view
            self.elapsed = gift_view(self.state.snapshot(), time.monotonic())["elapsed"]
            self.started = time.monotonic() - self.elapsed
            self.player.setPosition(round(self.elapsed * 1000))
        self._next()

    def _audio_loaded(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self.gift_id and self.active:
            self.player.setPosition(round(self.elapsed * 1000))

    def _tick(self):
        if not self.active or self.suspended or self.clip is None:
            return
        self.elapsed = time.monotonic() - self.started
        if self.elapsed >= self.clip.duration:
            self._finish()
            return
        index = min(len(self.clip.frames) - 1, int(self.elapsed * self.clip.fps))
        if index != self.last_frame:
            self.last_frame = index
            self.state.set_video(self.clip.frames[index], len(self.queue))

    def set_paused(self, paused):
        if paused and self.active and not self.suspended:
            self.elapsed = time.monotonic() - self.started
            self.suspended = True
            self.player.pause()
        elif not paused:
            if self.active and self.suspended:
                self.started = time.monotonic() - self.elapsed
                self.suspended = False
                if self.clip and self.clip.audio:
                    self.player.play()
            self._next()

    def _release_audio(self):
        self.player.stop()
        self.player.setSource(QUrl())
        if self.buffer:
            self.buffer.deleteLater()
            self.buffer = None

    def _finish(self):
        self._release_audio()
        self.active = False
        self.state.set_video(None, len(self.queue))
        self._next()

    def stop(self):
        self.queue.clear()
        self._release_audio()
        self.active = False
        self.suspended = False
        self.state.set_video(None)

    def mute(self, muted):
        self.output.setMuted(muted)

    def close(self):
        self.closed = True
        self.timer.stop()
        self.stop()
        self._cancel_loading()
        for decoder, thread in self.jobs:
            thread.join(timeout=3)
        self.jobs.clear()
        self.clip = None
