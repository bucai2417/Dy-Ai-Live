from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

from app.fishing import FISH, FishingGame
from app.fishing_board import FishingBoard
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import DEFAULTS
from app.world import WorldDirector


def main():
    application = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    output = ROOT / "logs" / "fishing-check"
    output.mkdir(parents=True, exist_ok=True)
    state = StageState()
    state.connected = True
    state.config.update(name="深蓝 · 云汐钓台", stage=copy.deepcopy(DEFAULTS["stage"]),
                        ticker=copy.deepcopy(DEFAULTS["ticker"]), music={"show_player": True})
    state.outfit, state.costume = "sailor", "cool"
    board = FishingBoard()
    for index, (name, value, kind) in enumerate((("星海旅人", 6480, "moon"), ("小鱼同学", 3820, "gold"),
                                               ("晚风来信", 2160, "tuna"), ("海盐汽水", 760, "koi"), ("月亮邮差", 420, "perch"))):
        board.add(str(index), {"name": FISH[kind]["name"], "kind": kind, "value": value}, {"name": name})
    state.stats.update(tokens=12345, cost=.0358, replies=128, fishingRanking=board.snapshot())
    state.room = {"online": 3456}
    state.danmu = 789
    game = FishingGame({"enabled": True, "escape_chance": 0, "likes_per_pull": 10,
                        "bite_min": 4, "bite_max": 8}, random.Random(3))
    game.start("海风会客厅", 0)
    samples = [("等待咬钩", game.snapshot(2))]
    game.tick(8)
    samples.append(("随机咬钩", game.snapshot(8.5)))
    game.pull(user={"id": "viewer-1", "name": "星海旅人"}, event_id="first", now=9)
    game.pull(event_id="second", now=9.5)
    samples.append(("协力拉钩", game.snapshot(9.6)))
    game.add_likes(1000, "likes", now=10)
    samples.append(("鱼跃出水", game.snapshot(10.8)))
    game.tick(12)
    samples.append(("恭喜贡献冠军", game.snapshot(13)))
    report = {}
    for width, height, name in ((720, 1280, "竖屏"), (1280, 720, "横屏")):
        scene = Scene(width, height)
        for phase, game_state in samples:
            snapshot = state.snapshot()
            snapshot.update(fishing=game_state, fishing_at=0, animation_time=game_state["elapsed"])
            image = scene.render(snapshot, 0, 0)
            image.save(str(output / f"{name}-{phase}.png"))
        timings = []
        snapshot = state.snapshot()
        snapshot.update(fishing=samples[2][1], fishing_at=0)
        for index in range(120):
            clock = index / 30
            snapshot["animation_time"] = clock
            started = time.perf_counter()
            scene.render(snapshot, clock, clock)
            timings.append((time.perf_counter() - started) * 1000)
        report[name] = {"mean_ms": round(statistics.mean(timings), 2), "p95_ms": round(sorted(timings)[114], 2)}
        for kind, image in scene.fishing.fish_images.items():
            image.save(str(output / (FISH[kind]["name"] + ".png")))
        for place, label in (("room", "会客厅"), ("cloud", "云上天镜")):
            snapshot = state.snapshot()
            snapshot["config"]["world"] = {**DEFAULTS["world"], "scene": place}
            director = WorldDirector(snapshot["config"]["world"], 0)
            snapshot.update(world=director.snapshot(5), world_at=5, animation_time=5)
            snapshot["gift_senders"] = [{"key": str(index), "name": nickname, "rank": index + 1, "count": 2, "at": 0}
                                         for index, nickname in enumerate(("星海旅人", "小鱼同学", "晚风来信", "海盐汽水", "月亮邮差",
                                                                           "云朵朵", "鲸落星河", "听海的人", "小橘子", "鱼塘常客"))]
            snapshot["events"] = [{"kind": "chat", "userName": "星海旅人", "text": "今天想去钓鱼！"},
                                  {"kind": "chat", "userName": "小鱼同学", "text": "换个场景看看"}]
            scene.render(snapshot, 5, 5).save(str(output / f"{name}-大场景-{label}.png"))
    (output / "结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
