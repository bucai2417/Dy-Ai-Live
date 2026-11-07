"""离线渲染三维场景、镜头和 16 秒环游，保存画面与耗时报告。"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication
from PIL import Image

from app.like_goal import STUNTS
from app.native.scene import Scene
from app.native.state import StageState
from app.world import SCENES, WEATHERS, WorldDirector


def main():
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    output = ROOT / "logs" / "world-check"
    output.mkdir(parents=True, exist_ok=True)
    state = StageState()
    state.connected = True
    state.config["name"] = "深蓝 Deepy"
    state.config["world"] = {"enabled": True, "scene": "room", "weather": "clear", "camera": "front", "particles": True}
    state.config["stage"] = {"scale": .64, "show_tokens": True}
    state.outfit, state.costume = "starry", "cool"
    director = WorldDirector(state.config["world"], 0)
    samples = []
    for environment in SCENES:
        director.command("scene", environment, manual=True, now=0)
        for weather in WEATHERS:
            director.command("weather", weather, manual=True, now=0)
            state.world = director.snapshot(5)
            state.world_at = 5
            snapshot = state.snapshot()
            snapshot["animation_time"] = 5
            for width, height, orientation in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
                scene = Scene(width, height)
                image = scene.render(snapshot, 5, 5)
                image.save(str(output / f"{environment}-{weather}-{orientation}.png"))
                if orientation == "横屏":
                    samples.append(image.copy(*scene.layout.effects).scaled(624, 438, Qt.AspectRatioMode.KeepAspectRatio))
    sheet = QImage(624 * 4, 438 * len(SCENES), QImage.Format.Format_RGB32)
    sheet.fill(0)
    painter = QPainter(sheet)
    for index, image in enumerate(samples):
        painter.drawImage(index % 4 * 624, index // 4 * 438, image)
    painter.end()
    sheet.save(str(output / "场景天气总览.png"))
    activity_timings = {}
    for activity in ("run", "drive", "sleep", "window", "tea", "lookout", "walk", "park", "dismount"):
        director = WorldDirector({**state.config["world"], "scene": "cloud" if activity in ("run", "drive", "lookout", "walk", "park", "dismount") else "room"}, 0)
        if activity in ("park", "dismount"):
            director.command("activity", "drive", manual=True, now=-4)
        director.command("activity", activity, manual=True, now=0)
        state.world, state.world_at = director.snapshot(0), 0
        timings = []
        for width, height, orientation in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
            scene = Scene(width, height)
            for index in range(90):
                clock = 7 + index / 30
                snapshot = state.snapshot()
                snapshot["animation_time"] = clock
                started = time.perf_counter()
                image = scene.render(snapshot, clock, clock)
                timings.append((time.perf_counter() - started) * 1000)
                if index == 30:
                    image.save(str(output / f"场景互动-{activity}-{orientation}.png"))
        activity_timings[activity] = {"mean_ms": round(statistics.mean(timings), 2), "p95_ms": round(sorted(timings)[int(len(timings) * .95)], 2)}
    director = WorldDirector(state.config["world"], 0)
    director.command("scene", "room", manual=True, now=0)
    director.command("weather", "rain", manual=True, now=0)
    director.command("camera", "right", manual=True, now=0)
    director.command("move", "window", manual=True, now=0)
    state.world, state.world_at = director.snapshot(0), 0
    scene = Scene(1280, 720)
    timings = []
    for index in range(180):
        clock = index / 30
        snapshot = state.snapshot()
        snapshot["animation_time"] = clock
        started = time.perf_counter()
        image = scene.render(snapshot, clock, clock)
        timings.append((time.perf_counter() - started) * 1000)
        if index in (18, 36, 120):
            image.save(str(output / f"镜头移动-{index}.png"))
    scene = Scene(540, 960)
    frames = []
    swim_timings = []
    state.outfit, state.costume = "raincoat", "cool"
    for index in range(128):
        clock = index / 8
        snapshot = state.snapshot()
        snapshot["animation_time"] = clock
        snapshot["like_goal_at"] = clock
        snapshot["like_goal"] = {"active": {**STUNTS["swim"], "motion": "swim", "id": "preview", "elapsed": clock}}
        if 4 <= clock < 7:
            snapshot["reaction"] = {"motion": "heart", "started": 4, "duration": 3, "effect": "hearts"}
            snapshot["reaction_until"] = 7
        snapshot["mouth"] = .7 if 8 <= clock < 11 else 0
        snapshot["mouth_at"] = clock
        started = time.perf_counter()
        image = scene.render(snapshot, clock, clock)
        swim_timings.append((time.perf_counter() - started) * 1000)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        frames.append(Image.open(io.BytesIO(bytes(data))).convert("RGB").quantize(colors=128, method=Image.Quantize.FASTOCTREE))
    frames[0].save(output / "十六秒流光环游.gif", save_all=True, append_images=frames[1:],
                   duration=[120 if index % 2 == 0 else 130 for index in range(len(frames))], loop=0)
    report = {"scene_count": len(SCENES), "weather_count": len(WEATHERS), "swim_seconds": 16, "activities": activity_timings,
              "moving_camera_render_ms_mean": round(statistics.mean(timings), 2),
              "moving_camera_render_ms_p95": round(sorted(timings)[int(len(timings) * .95)], 2),
              "swim_render_ms_mean": round(statistics.mean(swim_timings), 2),
              "swim_render_ms_p95": round(sorted(swim_timings)[int(len(swim_timings) * .95)], 2)}
    (output / "结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
