"""隔离配置的原生窗口、音频、视频流与最小化检查。"""
from __future__ import annotations

import base64
import asyncio
import copy
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import uvicorn
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QTabWidget

from app import settings_store
from app.__main__ import service_ready
from app.config import settings
from app.native.preferences import Preferences
from app.native.scene import Scene
from app.native.music_view import music_view
from app.native.window import StudioWindow
from app.native.display import load_size
from app.settings_store import store
from tools.sample_media import create_sample_video


def wait_until(app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("等待原生事件超时")


def pump(app, seconds):
    deadline = time.monotonic() + seconds
    wait_until(app, lambda: time.monotonic() >= deadline, seconds + 1)


def switch_layout(app, window, orientation):
    window.orientation_buttons[orientation].click()
    wait_until(app, lambda: window._pending_display_save is None)
    width, height = window._displayed_size
    assert (width > height) == (orientation == "landscape")
    assert load_size(window.display_settings_path) == (width, height)


def save_stunt_preview(sample, report_dir):
    try:
        from PIL import Image
    except ImportError:
        return False
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from app.like_goal import STUNTS

    scene = Scene(360, 640)
    frames = []
    durations = []
    sample["gift_senders"] = []
    sample["avatar_images"] = {}
    sample["like_goal"]["pending"] = 0
    for motion, preset in STUNTS.items():
        for frame_index in range(32):
            age = frame_index / 31 * (preset["duration"] - 0.2)
            sample["like_goal"]["active"] = {**preset, "motion": motion, "elapsed": age}
            image = scene.render(sample, age, sample["like_goal_at"])
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            frames.append(Image.open(io.BytesIO(bytes(data))).convert("RGB"))
            durations.append(round(preset["duration"] / 32 * 1000))
    frames[0].save(report_dir / "点赞整活动画预览.gif", save_all=True,
                   append_images=frames[1:], duration=durations, loop=0)
    return True


def main():
    report_dir = ROOT / "logs" / "native-check"
    report_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    with tempfile.TemporaryDirectory(prefix="native-check-") as temporary:
        settings_store.CONFIG_FILE = Path(temporary) / "runtime.json"
        store._data = copy.deepcopy(settings_store.DEFAULTS)
        store._data["gift_show"]["enabled"] = False
        store._data["ingest"]["enabled"] = False
        store._data["tts"]["engine"] = "none"
        store._data["templates"]["enabled"] = False
        store._data["stage"].update(scale=0.64, offset_y=-0.11, ball_max=10)
        store._data["stage"]["video_path"] = str(create_sample_video(Path(temporary) / "透明视频样例.webm"))
        store._data["persona"]["name"] = "深蓝 · 陪你聊天"
        settings.llm_api_key = settings.llm_base_url = settings.llm_model = ""
        settings.demo = False
        avatar_dir = Path(temporary) / "avatars"
        avatar_dir.mkdir()
        for index, color in enumerate(("#73c6c9", "#c5a2e5", "#efb98d", "#e69cbb", "#8baee6", "#9dd8a9")):
            avatar = QImage(128, 128, QImage.Format.Format_RGB32)
            avatar.fill(QColor(color))
            painter = QPainter(avatar)
            painter.setPen(QColor("#173549"))
            painter.setFont(QFont("Microsoft YaHei UI", 40, QFont.Weight.Bold))
            painter.drawText(avatar.rect(), Qt.AlignmentFlag.AlignCenter, "星海鱼月云鲸"[index])
            painter.end()
            avatar.save(str(avatar_dir / f"viewer-{index}.png"))
        from fastapi.staticfiles import StaticFiles
        from app.main import app as service
        service.mount("/_native_check_avatars", StaticFiles(directory=str(avatar_dir)))
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            settings.port = port_socket.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=settings.port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        window = None
        decoder = None
        try:
            wait_until(app, lambda: server.started)
            base = f"http://127.0.0.1:{settings.port}"
            assert service_ready(base)
            display_settings = Path(temporary) / "中文画面设置.json"
            window = StudioWindow(base, 720, 1280, 30, 0, muted=True, display_settings_path=display_settings)
            window.show()
            wait_until(app, lambda: window.state.connected and bool(window.state.config))
            wait_until(app, lambda: window.frames.fps > 0)
            assert window.source_input.currentData() == store._data["ingest"]["source"]
            window.event_text.setText("中文测试：今天也要开开心心！")
            window.inject_chat()
            wait_until(app, lambda: bool(window.state.events))
            window.client.post("/api/inject", {"kind": "gift", "gift": "小心心", "user": "星海旅人", "count": 3})
            pump(app, 0.5)
            names = ["星海旅人", "海盐汽水", "小鱼同学", "月亮邮差", "云朵朵", "鲸落星河",
                     "晚风来信", "小橘子", "蓝色浪花", "听海的人", "星光", "鱼塘常客"]
            for index, name in enumerate(names):
                window.client.post("/api/inject", {"kind": "gift", "gift": "小心心", "user": name,
                                                   "avatar": f"{base}/_native_check_avatars/viewer-{index % 6}.png"})
            wait_until(app, lambda: len(window.state.gift_senders) == 10 and len(window.state.avatar_images) == 6)
            window.client.post("/api/inject", {"kind": "gift", "gift": "小心心", "user": "星海旅人"})
            wait_until(app, lambda: window.state.gift_senders.get("id:inj:星海旅人", {}).get("count") == 5)
            assert "id:inj:鱼塘常客" not in window.state.gift_senders
            window.client.post("/api/inject", {"kind": "gift", "gift": "小心心", "user": "鱼塘常客", "value": 50})
            wait_until(app, lambda: next(iter(window.state.gift_senders)) == "id:inj:鱼塘常客")
            assert len(window.state.gift_senders) == 10 and "id:inj:听海的人" not in window.state.gift_senders
            window.client.post("/api/inject", {"kind": "gift", "gift": "墨镜", "user": "鱼塘常客", "value": 100})
            wait_until(app, lambda: window.state.costume == "cool")
            assert window.state.celebration_until == 0
            for outfit, motion in (("sailor", "wave"), ("raincoat", "stretch"), ("starry", "heart")):
                window.client.send({"cmd": "costume", "name": outfit})
                wait_until(app, lambda: window.state.outfit == outfit)
                assert window.state.costume == "cool"
                window.client.send({"cmd": "reaction", "motion": motion})
                wait_until(app, lambda: window.state.reaction.get("motion") == motion)
                pump(app, .7)
                for width, height, orientation in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
                    model_scene = Scene(width, height)
                    assert model_scene.rig is not None, model_scene.avatar_error
                    model_scene.render(window.state.snapshot(), 1, time.monotonic()).save(
                        str(report_dir / f"骨架换装-{outfit}-{orientation}.png"))
            assert store.section("stage")["outfit"] == "starry"
            wait_until(app, lambda: window.video.clip is not None, 10)
            preferences = Preferences(window.client, window)
            preferences.show()
            wait_until(app, lambda: preferences.save.isEnabled())
            assert preferences.fields[("persona", "name")].text() == "深蓝 · 陪你聊天"
            preferences.fields[("persona", "name")].setText("深蓝 · 中文保存成功")
            preferences.fields[("llm", "price_in")].setValue(2.5)
            preferences.fields[("llm", "price_out")].setValue(10)
            assert preferences.fields[("stage", "show_tokens")].isChecked()
            assert preferences.fields[("stage", "outfit")].currentData() == "starry"
            assert preferences.fields[("stage", "avatar_mode")].currentData() == "layered"
            preferences.tabs.setCurrentIndex(next(index for index in range(preferences.tabs.count())
                                                 if preferences.tabs.tabText(index) == "人物与互动"))
            pump(app, .1)
            preferences.grab().save(str(report_dir / "骨架服装设置.png"))
            preferences.tabs.setCurrentIndex(1)
            pump(app, 0.1)
            preferences.grab().save(str(report_dir / "Token价格设置.png"))
            preferences.fields[("like_goal", "step")].setValue(100)
            preferences.fields[("like_goal", "cooldown")].setValue(1)
            preferences.tabs.setCurrentIndex(next(index for index in range(preferences.tabs.count())
                                                 if preferences.tabs.tabText(index) == "点赞整活"))
            pump(app, 0.1)
            preferences.grab().save(str(report_dir / "点赞整活设置.png"))
            preferences.tabs.setCurrentIndex(next(index for index in range(preferences.tabs.count())
                                                 if preferences.tabs.tabText(index) == "内置话术"))
            editor = preferences.line_editor
            editor.categories.setCurrentIndex(editor.categories.findData("offline_greet"))
            editor.editor.setPlainText("{name}，欢迎来到深蓝的直播间。今天也谢谢你的陪伴，最后一句也会完整播报。")
            pump(app, 0.1)
            preferences.grab().save(str(report_dir / "内置话术编辑.png"))
            preferences.tabs.setCurrentIndex(next(index for index in range(preferences.tabs.count())
                                                 if preferences.tabs.tabText(index) == "冷场互动"))
            pump(app, 0.1)
            preferences.grab().save(str(report_dir / "冷场互动设置.png"))
            preferences._save()
            wait_until(app, lambda: not preferences.isVisible())
            assert store.persona_name == "深蓝 · 中文保存成功"
            assert "最后一句也会完整播报" in store.lines("offline_greet")[0]
            preferences.deleteLater()
            from app.metrics import metrics

            async def seed_tokens():
                metrics.add_tokens(1000000, 250000, estimated=True)
                await service.state.brain._push_stats()

            future = asyncio.run_coroutine_threadsafe(seed_tokens(), service.state.brain_task.get_loop())
            wait_until(app, future.done)
            future.result()
            wait_until(app, lambda: window.state.stats.get("tokens") == 1250000)
            assert window.state.stats["cost"] == 5
            for width, height, orientation in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
                Scene(width, height).render(window.state.snapshot(), 1, time.monotonic()).save(
                    str(report_dir / f"Token计费与墨镜礼物-{orientation}.png"))
            window.client.post("/api/config", {"stage": {"show_tokens": False}})
            wait_until(app, lambda: not window.state.config["stage"].get("show_tokens", True))
            window.client.post("/api/config", {"stage": {"show_tokens": True}})
            wait_until(app, lambda: window.state.config["stage"].get("show_tokens", False))
            audio_data = io.BytesIO()
            with wave.open(audio_data, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                samples = np.sin(np.arange(32000) * 2 * np.pi * 220 / 16000) * 10000
                wav.writeframes(samples.astype(np.int16).tobytes())
            tones = audio_data.getvalue()
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                ffmpeg = shutil.which("ffmpeg")
            voice_data = tones
            if ffmpeg:
                encoded = subprocess.run([ffmpeg, "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
                                          "-f", "mp3", "pipe:1"], input=tones, capture_output=True, timeout=10,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                assert encoded.returncode == 0
                voice_data = encoded.stdout
            from app.main import app as service
            receipts = []
            window.audio.voice_finished.connect(lambda identifier, status: receipts.append((identifier, status)))

            async def sequential_voice_check():
                previous_tts = service.state.brain.speaker.tts
                synthesized = []

                async def synthesize(text):
                    synthesized.append(text)
                    return voice_data

                service.state.brain.speaker.tts = SimpleNamespace(name="none", synthesize=synthesize)
                try:
                    assert await service.state.brain.speaker.say("第一段完整播报。第二段读到最后。第三段也不能丢失。")
                    return synthesized
                finally:
                    service.state.brain.speaker.tts = previous_tts

            future = asyncio.run_coroutine_threadsafe(sequential_voice_check(), service.state.brain_task.get_loop())
            wait_until(app, future.done, 15)
            assert future.result() == ["第一段完整播报。", "第二段读到最后。", "第三段也不能丢失。"]
            assert len([status for identifier, status in receipts if identifier and status == "ended"]) == 3
            assert not window.audio.queue and not service.state.brain.speaker.busy
            for motion in ("heart", "stretch", "peek", "float", "sleepy"):
                sample = window.state.snapshot()
                from app.native.actions import ACTIONS
                sample.update(reaction={**ACTIONS[motion], "motion": motion, "started": 100}, reaction_until=110)
                for width, height, direction in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
                    Scene(width, height).render(sample, 2, 102).save(str(report_dir / f"新增动作-{motion}-{direction}.png"))
            window.client.send({"cmd": "idle_preview"})
            wait_until(app, lambda: window.state.reaction.get("motion") in store._data["idle"]["actions"])
            pcm_buffers = []
            window.audio.samples.audioBufferReceived.connect(lambda value: pcm_buffers.append(value.byteCount()))
            window.audio.handle({"type": "voice", "audio": base64.b64encode(voice_data).decode(), "text": "欢迎来到深蓝的直播间，今天也很高兴见到你。"})
            wait_until(app, lambda: window.state.mouth > 0)
            window.grab().save(str(report_dir / "工作台.png"))
            Scene(720, 1280).render(window.state.snapshot(), 1, time.monotonic()).save(str(report_dir / "竖屏.png"))
            Scene(1280, 720).render(window.state.snapshot(), 1, time.monotonic()).save(str(report_dir / "横屏.png"))
            window.toggle_pause()
            wait_until(app, lambda: window.state.paused)
            assert not window.audio.voice_active
            pump(app, 0.1)
            window.frames.latest()[0].save(str(report_dir / "休息画面.png"))
            window.toggle_pause()
            wait_until(app, lambda: not window.state.paused)
            song_path = Path(temporary) / "中文音乐.wav"
            with wave.open(str(song_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(samples.astype(np.int16).tobytes() * 90)

            async def queue_song():
                from app.main import app as service
                from app.playlist import make_track, queue
                track = make_track({"title": "深蓝的晚间电台 · 测试音乐", "by": "深蓝乐队",
                                    "url": QUrl.fromLocalFile(str(song_path)).toString()}, user="星海旅人")
                queue.add(track)
                queue.next_track()
                queue.add(make_track({"title": "下一首 · 星光与你", "by": "鱼塘合唱团",
                                      "url": QUrl.fromLocalFile(str(song_path)).toString()}, user="小鱼"))
                await service.state.brain._push_music()
                return track.id

            from app.main import app as service
            future = asyncio.run_coroutine_threadsafe(queue_song(), service.state.brain_task.get_loop())
            wait_until(app, future.done)
            future.result()
            wait_until(app, lambda: window.audio.music.position() > 300)
            wait_until(app, lambda: music_view(window.state.snapshot(), time.monotonic())["playing"])
            music_before = music_view(window.state.snapshot(), time.monotonic())
            assert music_before["next_title"] == "下一首 · 星光与你"
            assert music_before["duration"] > 0
            window.client.send({"cmd": "costume", "name": "cool"})
            wait_until(app, lambda: window.state.costume == "cool")
            window.action_input.setCurrentIndex(window.action_input.findData("handstand"))
            window.trigger_action()
            wait_until(app, lambda: window.state.reaction.get("motion") == "handstand")
            pump(app, 0.7)
            window.frames.latest()[0].save(str(report_dir / "人物倒立与装扮.png"))
            window.client.post("/api/inject", {"kind": "chat", "text": "奶蛙", "user": "视频测试"})
            wait_until(app, lambda: window.video.active and window.state.video_image is not None)
            pump(app, 0.2)
            window.frames.latest()[0].save(str(report_dir / "奶蛙透明视频-竖屏.png"))
            Scene(1280, 720).render(window.state.snapshot(), 1, time.monotonic()).save(str(report_dir / "奶蛙透明视频-横屏.png"))
            window.toggle_pause()
            wait_until(app, lambda: window.state.paused and window.video.suspended)
            paused_video = window.video.elapsed
            switch_layout(app, window, "landscape")
            pump(app, 0.25)
            assert window.video.elapsed == paused_video
            assert window.state.paused and window.video.suspended
            switch_layout(app, window, "portrait")
            window.toggle_pause()
            wait_until(app, lambda: not window.state.paused and not window.video.suspended)
            wait_until(app, lambda: not window.video.active)
            window.action_input.setCurrentIndex(window.action_input.findData("dance"))
            window.trigger_action()
            wait_until(app, lambda: window.state.reaction.get("motion") == "dance")
            for index in range(4):
                window.client.post("/api/inject", {"kind": "chat", "text": "奶蛙", "user": "重复视频测试"})
            wait_until(app, lambda: window.video.active and len(window.video.queue) == 3)
            pump(app, 0.7)
            snapshot = window.state.snapshot()
            assert snapshot["reaction"]["effect"] == "confetti"
            window.grab().save(str(report_dir / "工作台音乐.png"))
            Scene(720, 1280).render(snapshot, 1, time.monotonic()).save(str(report_dir / "音乐与互动-竖屏.png"))
            Scene(1280, 720).render(snapshot, 1, time.monotonic()).save(str(report_dir / "音乐与互动-横屏.png"))
            window.frames.latest()[0].save(str(report_dir / "礼物头像碰撞-竖屏.png"))
            original_url = window.output.url
            original_worker, original_audio, original_video = window.worker, window.audio, window.video
            music_position = window.audio.music.position()
            video_started = window.video.started
            queued_videos = list(window.video.queue)
            switch_layout(app, window, "landscape")
            assert window.output.url == original_url and window.worker is original_worker
            assert window.audio is original_audio and window.video is original_video
            assert window.audio.music.position() >= music_position
            remaining_videos = list(window.video.queue)
            assert len(remaining_videos) <= len(queued_videos)
            if window.video.started == video_started:
                assert remaining_videos == queued_videos
            else:
                assert window.video.started > video_started and len(remaining_videos) < len(queued_videos)
                assert remaining_videos == queued_videos[len(queued_videos) - len(remaining_videos):]
            window.show_output()
            pump(app, 0.1)
            assert window.popout.width() > window.popout.height()
            window.grab().save(str(report_dir / "横竖屏切换-工作台.png"))
            window.frames.latest()[0].save(str(report_dir / "横屏实时互动.png"))
            clean_sample = window.state.snapshot()
            window.world_inputs["scene"].setCurrentIndex(window.world_inputs["scene"].findData("terrace"))
            window.world_buttons[0].click()
            wait_until(app, lambda: window.state.world.get("scene") == "terrace")
            window.world_inputs["weather"].setCurrentIndex(window.world_inputs["weather"].findData("snow"))
            window.world_buttons[1].click()
            wait_until(app, lambda: window.state.world.get("weather") == "snow")
            window.client.send({"cmd": "world", "action": "move", "value": "lookout"})
            wait_until(app, lambda: window.state.world["movement"]["to"] == [1.9, -.6])
            window.toggle_pause()
            wait_until(app, lambda: window.state.paused)
            frozen_world = copy.deepcopy(window.state.world)
            pump(app, .2)
            assert window.state.world == frozen_world
            window.toggle_pause()
            wait_until(app, lambda: not window.state.paused)
            window.client.send({"cmd": "world", "action": "camera", "value": "right"})
            wait_until(app, lambda: window.state.world.get("camera") == "right")
            for tabs in window.findChildren(QTabWidget):
                for tab_index in range(tabs.count()):
                    if tabs.tabText(tab_index) == "场景导演":
                        tabs.setCurrentIndex(tab_index)
            pump(app, .25)
            window.grab().save(str(report_dir / "场景导演-原生面板.png"))
            window.frames.latest()[0].save(str(report_dir / "三维场景-雪天露台.png"))
            window.world_inputs["activity"].setCurrentIndex(window.world_inputs["activity"].findData("drive"))
            window.world_buttons[4].click()
            wait_until(app, lambda: window.state.world.get("activity") == "drive" and window.state.world.get("scene") == "cloud")
            pump(app, .4)
            window.toggle_pause()
            wait_until(app, lambda: window.state.paused)
            from app.world import view
            frozen_drive = view(window.state.world)
            pump(app, .3)
            assert view(window.state.world) == frozen_drive
            window.toggle_pause()
            wait_until(app, lambda: not window.state.paused)
            window.frames.latest()[0].save(str(report_dir / "云台驾驶-原生窗口.png"))
            driving_performance = {}
            for direction in ("landscape", "portrait"):
                switch_layout(app, window, direction)
                window.popout.hide()
                window.showMinimized()
                pump(app, .4)
                before_drive = window.frames.sequence
                drive_started = time.monotonic()
                pump(app, 4)
                drive_seconds = time.monotonic() - drive_started
                drive_frames = window.frames.sequence - before_drive
                assert drive_frames >= 72 and not window.frames.error
                driving_performance[direction] = {"frames": drive_frames, "seconds": round(drive_seconds, 3), "fps": round(drive_frames / drive_seconds, 2)}
            window.showNormal()
            window.client.post("/api/inject", {"kind": "chat", "text": "下车", "user": "场景验收"})
            wait_until(app, lambda: window.state.world.get("activity") == "dismount")
            assert window.state.world.get("parked_vehicle")
            pump(app, 1.2)
            assert not service.state.brain.runtime_context()["world"]["in_vehicle"]
            window.frames.latest()[0].save(str(report_dir / "下车后车辆保留.png"))
            window.client.post("/api/config", {"world": {"cooldown": 0}})
            wait_until(app, lambda: window.state.config["world"]["cooldown"] == 0)
            for text, expected in (("换个场景", "terrace"), ("换个场景", "room"), ("换个场景", "cloud"),
                                   ("回家", "room"), ("出门", "cloud"), ("回家", "room")):
                window.client.post("/api/inject", {"kind": "chat", "text": text, "user": "场景验收"})
                wait_until(app, lambda target=expected: window.state.world.get("scene") == target, 15)
            for outfit in ("sailor", "raincoat", "starry", "sailor", "raincoat", "starry"):
                window.client.send({"cmd": "costume", "name": "next_outfit"})
                wait_until(app, lambda expected=outfit: window.state.outfit == expected)
            window.client.send({"cmd": "world", "action": "activity", "value": "sleep"})
            wait_until(app, lambda: window.state.world.get("activity") == "sleep" and window.state.world.get("scene") == "room")
            wait_until(app, lambda: "躺在沙发上睡觉" in window.ai_state_label.text(), 12)
            switch_layout(app, window, "landscape")
            window.grab().save(str(report_dir / "AI状态感知-睡眠.png"))
            window.client.send({"cmd": "reaction", "motion": "heart"})
            wait_until(app, lambda: "小心心" in window.ai_state_label.text(), 8)
            window.grab().save(str(report_dir / "AI状态感知-叠加动作.png"))
            assert service.state.history.path.is_file()
            clean_sample.update(video_image=None, reaction={}, reaction_until=0, gift_senders=[])
            Scene(1280, 720).render(clean_sample, 1, time.monotonic()).save(str(report_dir / "横屏布局-总览.png"))
            window.client.post("/api/inject", {"kind": "like", "count": 99})
            wait_until(app, lambda: window.state.like_goal.get("total") == 99)
            assert not window.state.like_goal.get("active")
            window.client.post("/api/inject", {"kind": "like", "count": 1})
            wait_until(app, lambda: (window.state.like_goal.get("active") or {}).get("motion") == "swim")
            window.toggle_pause()
            wait_until(app, lambda: window.state.paused)
            from app.native.stunts import stunt_age

            paused_stunt = stunt_age(window.state.snapshot(), time.monotonic())[1]
            switch_layout(app, window, "portrait")
            pump(app, 0.3)
            assert stunt_age(window.state.snapshot(), time.monotonic())[1] == paused_stunt
            window.toggle_pause()
            wait_until(app, lambda: not window.state.paused)
            from app.like_goal import STUNTS

            for motion, preset in STUNTS.items():
                sample = window.state.snapshot()
                sample["video_image"] = None
                sample["like_goal"]["active"] = {**preset, "motion": motion, "elapsed": 2.4, "milestones": 1}
                sample["like_goal_at"] = time.monotonic()
                for width, height, orientation in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
                    Scene(width, height).render(sample, 1, sample["like_goal_at"]).save(
                        str(report_dir / f"点赞整活-{motion}-{orientation}.png"))
            window.toggle_pause()
            wait_until(app, lambda: window.state.paused)
            pump(app, 0.15)
            music_position = window.audio.music.position()
            paused_view = music_view(window.state.snapshot(), time.monotonic())
            paused_reaction = window.state.reaction.copy()
            paused_animation = window.state.snapshot()["animation_time"]
            pump(app, 0.3)
            assert abs(window.audio.music.position() - music_position) < 80
            assert abs(music_view(window.state.snapshot(), time.monotonic())["position"] - paused_view["position"]) < 0.1
            assert window.state.reaction == paused_reaction
            assert window.state.snapshot()["animation_time"] == paused_animation
            window.toggle_pause()
            wait_until(app, lambda: not window.state.paused and window.audio.music.position() > music_position + 200)
            window.trigger_action()
            wait_until(app, lambda: window.state.reaction.get("motion") == "dance")
            if ffmpeg:
                decoder = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "mpjpeg", "-i", window.output.url,
                                            "-frames:v", "60", "-f", "null", "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            pump(app, 0.4)
            switch_layout(app, window, "landscape")
            window.popout.hide()
            window.showMinimized()
            pump(app, 0.3)
            before = window.frames.sequence
            started = time.monotonic()
            pump(app, 4)
            elapsed = time.monotonic() - started
            count = window.frames.sequence - before
            assert count >= 80 and not window.frames.error
            report = {"resolution": "1280x720", "target_fps": 30, "minimized_frames": count,
                      "seconds": round(elapsed, 3), "minimized_fps": round(count / elapsed, 2),
                      "render_ms": round(window.frames.render_ms, 2), "pcm_buffers": len(pcm_buffers),
                      "chinese_config_saved": True, "pause_resume": True}
            report["music_pause_resume"] = "passed"
            report["world_scene_weather_camera_and_pause"] = "passed"
            report["cloud_driving_minimized"] = driving_performance
            report["driving_pause_and_indoor_sleep_controls"] = "passed"
            report["danmu_scene_cycle_home_exit_and_dismount"] = "passed"
            report["ai_state_panel_sleep_and_overlay_action"] = "passed"
            report["six_consecutive_outfit_cycles"] = "passed"
            report["measured_with_3d_scene_and_snow"] = True
            report["swim_milestone_duration_seconds"] = STUNTS["swim"]["duration"]
            report["three_voice_segments_complete_with_playback_receipts"] = "passed"
            report["editable_chinese_lines_saved"] = "passed"
            report["idle_action_preview_and_five_new_actions"] = "passed"
            report["voice_format"] = "mp3" if ffmpeg else "wav"
            report["mjpeg_fps"] = window.output.stream_fps
            report["music_ui_current_next_progress"] = "passed"
            report["manual_character_action"] = "passed"
            report["measured_with_music_and_particles"] = True
            report["gift_bubbles"] = len(window.state.gift_senders)
            report["downloaded_avatars"] = len(window.state.avatar_images)
            report["repeat_gift_updates_count"] = "passed"
            report["ranking_overtake_replaces_bubble"] = "passed"
            report["video_alpha"] = window.video.clip.frames[0].hasAlphaChannel()
            report["video_audio_bytes"] = len(window.video.clip.audio)
            report["video_pause_resume"] = "passed"
            report["repeated_video_queued"] = "passed"
            report["handstand_and_costume"] = "passed"
            report["layered_skeleton_three_outfits_preserve_sunglasses"] = "passed"
            report["sunglasses_gift"] = "passed"
            report["token_prices_and_live_toggle"] = "passed"
            report["token_cost"] = window.state.stats["cost"]
            report["like_threshold_exact"] = "passed"
            report["stunt_pause_resume"] = "passed"
            report["stunt_frames_both_layouts"] = "passed"
            report["live_orientation_switch_keeps_audio_video_queue"] = "passed"
            report["switch_during_video_and_stunt_pause"] = "passed"
            report["same_renderer_and_output_url"] = "passed"
            if decoder:
                stdout, stderr = decoder.communicate(timeout=8)
                assert decoder.returncode == 0, stderr.decode("utf-8", errors="replace")
                report["ffmpeg_mjpeg_60_frames"] = "passed"
                report["ffmpeg_decode_across_orientation_change"] = "passed"
            switch_layout(app, window, "portrait")
            before = window.frames.sequence
            started = time.monotonic()
            pump(app, 4)
            portrait_elapsed = time.monotonic() - started
            portrait_frames = window.frames.sequence - before
            assert portrait_frames >= 80 and not window.frames.error
            report["portrait_minimized_frames"] = portrait_frames
            report["portrait_minimized_fps"] = round(portrait_frames / portrait_elapsed, 2)
            switch_layout(app, window, "landscape")
            window.showNormal()
            window.client.post("/api/music/next")
            wait_until(app, lambda: music_view(window.state.snapshot(), time.monotonic())["title"] == "下一首 · 星光与你")
            wait_until(app, lambda: music_view(window.state.snapshot(), time.monotonic())["playing"])
            switched = music_view(window.state.snapshot(), time.monotonic())
            assert switched["queue_count"] == 0 and switched["position"] < 2
            report["next_track_updates_ui"] = "passed"
            persisted_stats = service.state.history.metrics.snapshot()
            persisted_events = dict(service.state.history.hub.stats)
            persisted_likes = service.state.brain.likes.total
            reconnected = []
            window.client.message.connect(lambda message: reconnected.append(message) if message.get("type") == "hello" else None)
            server.should_exit = True
            wait_until(app, lambda: not thread.is_alive())
            wait_until(app, lambda: not window.state.connected)
            from app import main as service_module
            from app.brain import scheduler
            from app.hub import Hub
            from app import metrics as metrics_module
            service_module.metrics = scheduler.metrics = metrics_module.metrics = metrics_module.Metrics()
            service_module.hub = scheduler.hub = Hub()
            server = uvicorn.Server(uvicorn.Config("app.main:app", host="127.0.0.1", port=settings.port, log_level="warning"))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            wait_until(app, lambda: window.state.connected and bool(reconnected), 12)
            assert service_ready(base)
            assert window.state.stats["tokens"] == persisted_stats["tokens"]
            assert window.state.stats["gifts"] == persisted_stats["gifts"]
            assert window.state.stats["giftRanking"] == persisted_stats["giftRanking"]
            assert window.state.danmu == persisted_events["danmu_total"]
            assert window.state.like_goal["total"] == persisted_likes
            assert not window.state.like_goal.get("active")
            report["daily_statistics_restore_from_disk_with_fresh_memory"] = "passed"
            assert window.worker.size == (1280, 720)
            assert window.state.outfit == "starry" and window.state.costume == "cool"
            report["outfit_and_accessory_survive_reconnect"] = "passed"
            report["reconnect"] = "passed"
            report["orientation_survives_reconnect"] = "passed"
            window.close()
            pump(app, 0.1)
            window = StudioWindow(base, output_port=0, muted=True, display_settings_path=display_settings)
            window.show()
            wait_until(app, lambda: window.state.connected and window.frames.sequence > 0)
            assert window.worker.size == (1280, 720) and window.orientation_buttons["landscape"].isChecked()
            assert window.state.outfit == "starry" and window.state.costume == "cool"
            report["outfit_and_accessory_restore_after_restart"] = "passed"
            report["orientation_restored_after_restart"] = "passed"
            report["stunt_gif_export"] = save_stunt_preview(sample, report_dir)
            (report_dir / "结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            if decoder and decoder.poll() is None:
                decoder.terminate()
                decoder.wait(timeout=5)
            if window:
                window.close()
                pump(app, 0.2)
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
