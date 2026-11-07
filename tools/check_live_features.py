from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import socket
import statistics
import sys
import tempfile
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import uvicorn
from PySide6.QtGui import QFontDatabase
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from app import settings_store
from app.config import settings
from app.gift_show import SHOWS
from app.native.fishing_scene import fishing_view
from app.native.gift_scene import gift_view
from app.native.preferences import Preferences
from app.native.scene import Scene
from app.native.state import StageState
from app.native.window import StudioWindow
from app.settings_store import DEFAULTS, store
from tools.check_native import pump, wait_until


def render_samples(output):
    report = {}
    for size, orientation in (((720, 1280), "竖屏"), ((1280, 720), "横屏")):
        scene = Scene(*size)
        state = StageState()
        state.connected = True
        state.config.update(name="深蓝 · 心意放映厅", stage=copy.deepcopy(DEFAULTS["stage"]),
                            world={"enabled": False}, ticker=copy.deepcopy(DEFAULTS["ticker"]))
        state.outfit = "sailor"
        for effect, (label, duration) in SHOWS.items():
            state.gift_show = {"active": {"id": effect, "effect": effect, "label": label, "duration": duration,
                                          "user": {"id": "guest", "name": "星海旅人", "avatar": ""},
                                          "gift": {"tea": "大啤酒", "flowers": "鲜花", "lollipop": "棒棒糖", "sunglasses": "墨镜"}.get(effect, label),
                                          "count": 5}, "elapsed": 5.5, "paused": True, "pending": 2}
            for age, phase in ((1.5, "接礼"), (5.5, "表演"), (duration - 1.1, "谢幕")):
                state.gift_show["elapsed"] = age
                scene.render(state.snapshot(), age, 100).save(str(output / f"{orientation}-{label}-{phase}.png"))
        snapshot = state.snapshot()
        snapshot["gift_show"]["paused"] = False
        snapshot["gift_show"]["elapsed"] = 0
        snapshot["gift_show_at"] = 0
        durations = []
        for index in range(90):
            age = index / 30
            started = time.perf_counter()
            scene.render(snapshot, age, age)
            durations.append((time.perf_counter() - started) * 1000)
        report[orientation] = {"mean_ms": round(statistics.mean(durations), 2), "p95_ms": round(sorted(durations)[85], 2)}
    return report


def main():
    output = ROOT / "logs" / "live-features-check"
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    report = {"礼物绘制": render_samples(output)}
    if "--screens-only" not in sys.argv:
        with tempfile.TemporaryDirectory(prefix="live-features-") as temporary:
            folder = Path(temporary)
            settings_store.CONFIG_FILE = folder / "runtime.json"
            store._data = copy.deepcopy(DEFAULTS)
            store._data["ingest"]["enabled"] = False
            store._data["tts"]["engine"] = "none"
            store._data["templates"]["enabled"] = False
            store._data["idle"]["actions_enabled"] = False
            store._data["music"]["gift_priority"] = False
            store._data["gift_show"]["beer_video"] = False
            store._data["fishing"].update(escape_chance=0, bite_min=3, bite_max=5)
            store._data["like_goal"]["enabled"] = False
            settings.llm_api_key = settings.llm_base_url = settings.llm_model = ""
            settings.demo = False
            music_path = folder / "循环背景测试.wav"
            with wave.open(str(music_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8000)
                audio.writeframes(b"\x00\x00" * 8000)
            from app.main import app as service
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(service, host="127.0.0.1", port=port, log_level="warning"))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            window = None
            try:
                wait_until(application, lambda: server.started)
                window = StudioWindow(f"http://127.0.0.1:{port}", 1280, 720, 30, 0, muted=True,
                                      display_settings_path=folder / "画面设置.json")
                window.show()
                wait_until(application, lambda: window.state.connected and bool(window.state.config.get("fishing")))
                preferences = Preferences(window.client, window)
                preferences.show()
                wait_until(application, lambda: preferences.save.isEnabled())
                preferences.fields[("fishing", "dedicated")].setChecked(True)
                preferences.fields[("bgm", "enabled")].setChecked(True)
                preferences.fields[("bgm", "path")].setText(str(music_path))
                preferences.fields[("bgm", "volume")].setValue(.25)
                for label in ("钓鱼玩法", "礼物演出", "背景 BGM", "冷场互动"):
                    preferences.tabs.setCurrentIndex(next(index for index in range(preferences.tabs.count()) if preferences.tabs.tabText(index) == label))
                    pump(application, .1)
                    preferences.grab().save(str(output / f"设置-{label}.png"))
                preferences._save()
                wait_until(application, lambda: not preferences.isVisible())
                wait_until(application, lambda: window.state.fishing.get("active"))
                wait_until(application, lambda: window.audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
                report["设置保存、常驻开局、中文路径BGM"] = "通过"
                round_id = window.state.fishing["round_id"]
                wait_until(application, lambda: window.state.fishing.get("phase") == "hooked", 12)
                window.client.post("/api/inject", {"kind": "chat", "text": "拉钩", "user": "首位鱼友"})
                wait_until(application, lambda: bool(window.state.fishing.get("winner")))
                for _ in range(2):
                    window.client.post("/api/inject", {"kind": "chat", "text": "拉钩", "user": "拉钩冠军"})
                wait_until(application, lambda: window.state.fishing.get("winner", {}).get("name") == "拉钩冠军")
                window.client.post("/api/inject", {"kind": "like", "count": 300, "user": "点赞冠军"})
                wait_until(application, lambda: window.state.fishing.get("phase") == "caught")
                assert window.state.fishing["winner"]["name"] == "点赞冠军"
                wait_until(application, lambda: bool(window.state.stats.get("fishingRanking", {}).get("ranking")))
                pump(application, .3)
                window.frames.latest()[0].save(str(output / "实名点赞冠军.png"))
                wait_until(application, lambda: window.state.fishing.get("round_id") != round_id, 10)
                assert window.state.fishing["active"]
                report["多次拉钩累计、实名点赞获奖、自动续竿"] = "通过"
                window.client.post("/api/inject", {"kind": "gift", "gift": "鲜花", "user": "送花的鱼友"})
                wait_until(application, lambda: bool(window.state.gift_show.get("active")))
                frozen = fishing_view(window.state.snapshot(), time.monotonic())
                window.client.post("/api/inject", {"kind": "gift", "gift": "棒棒糖", "user": "糖果鱼友", "count": 3})
                wait_until(application, lambda: window.state.gift_show.get("pending") == 1)
                pump(application, 3)
                frozen_after = fishing_view(window.state.snapshot(), time.monotonic())
                assert all(frozen_after[key] == frozen[key] for key in ("phase_elapsed", "remaining", "progress", "round_id"))
                window.toggle_pause()
                wait_until(application, lambda: window.state.paused and window.state.gift_show.get("paused"))
                elapsed = gift_view(window.state.snapshot(), time.monotonic())["elapsed"]
                pump(application, .6)
                assert gift_view(window.state.snapshot(), time.monotonic())["elapsed"] == elapsed
                assert window.audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PausedState
                hellos = []
                window.client.message.connect(lambda message: hellos.append(message) if message.get("type") == "hello" else None)
                window.client.socket.abort()
                wait_until(application, lambda: bool(hellos), 8)
                assert window.state.gift_show["active"]["user"]["name"] == "送花的鱼友"
                assert window.state.gift_show["elapsed"] == elapsed
                window.toggle_pause()
                wait_until(application, lambda: not window.state.paused)
                wait_until(application, lambda: (window.state.gift_show.get("active") or {}).get("effect") == "lollipop", 14)
                pump(application, 4)
                window.frames.latest()[0].save(str(output / "原生棒棒糖演出.png"))
                window.showMinimized()
                before, started = window.frames.sequence, time.monotonic()
                pump(application, 2)
                fps = (window.frames.sequence - before) / (time.monotonic() - started)
                assert fps > 20 and not window.frames.error
                report["礼物排队、暂停重连、钓鱼冻结"] = "通过"
                report["最小化演出FPS"] = round(fps, 2)
                wait_until(application, lambda: not window.state.gift_show.get("active"), 10)
                assert not window.state.fishing["paused"]
                window.client.post("/api/config", {"fishing": {"dedicated": False}})
                wait_until(application, lambda: not window.state.fishing.get("active"))
                window.client.send({"cmd": "world", "action": "activity", "value": "sleep"})
                wait_until(application, lambda: service.state.brain._dreaming(), 12)
                subtitles = []
                window.client.message.connect(lambda message: subtitles.append(message.get("text", "")) if message.get("type") == "subtitle" else None)
                wait_until(application, lambda: any("醒" in line for line in subtitles), 12)
                assert service.state.brain._dreaming()
                report["演出恢复、关闭常驻返回、睡姿梦话"] = "通过"
                assert window.audio.bgm.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                report["BGM持续循环与恢复"] = "通过"
            finally:
                if window:
                    window.close()
                    pump(application, .2)
                server.should_exit = True
                thread.join(timeout=8)
                assert not thread.is_alive()
    (output / "结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
