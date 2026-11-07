from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import random
import socket
import statistics
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import uvicorn
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app import settings_store
from app.config import settings
from app.fishing import FISH, FishingGame
from app.fishing_board import FishingBoard
from app.hub import Hub
from app.like_goal import LikeGoal
from app.live_history import LiveHistory
from app.metrics import Metrics
from app.native.fishing_scene import fishing_view
from app.native.scene import Scene
from app.native.state import StageState
from app.native.window import StudioWindow
from app.settings_store import DEFAULTS, store
from tools.check_native import pump, switch_layout, wait_until


def samples(output):
    report = {}
    game = FishingGame({"dedicated": True, "escape_chance": 0}, random.Random(7))
    game.start("会客厅", 0)
    game.tick(game.deadline)
    game.fish.update(kind="whale", name=FISH["whale"]["name"], pulls=18, weight=1680, value=12800, category="fish", description="")
    whale = copy.deepcopy(game.fish)
    board = FishingBoard()
    for index, kind in enumerate(FISH):
        board.encounter(str(index), {"kind": kind})
        if index % 4 == 0:
            board.encounter(str(index) + "more", {"kind": kind})
    board.add("champion", game.fish, {"name": "星海钓鱼冠军"})
    for size, orientation in (((720, 1280), "竖屏"), ((1280, 720), "横屏")):
        scene, state = Scene(*size), StageState()
        state.connected = True
        state.config.update(name="深蓝 · 云汐钓台", stage=copy.deepcopy(DEFAULTS["stage"]),
                            ticker=copy.deepcopy(DEFAULTS["ticker"]))
        state.outfit = "sailor"
        state.stats["fishingRanking"] = board.snapshot()
        state.room = {"online": 1234}
        game._phase("hooked", 40, 35)
        game.fish = copy.deepcopy(whale)
        snapshot = state.snapshot()
        snapshot.update(fishing=game.snapshot(43), fishing_at=100, animation_time=43)
        snapshot.update(subtitle="咬钩啦！一起发拉钩，看看今天能不能捞到鳗鱼过滤器。", subtitle_at=98, subtitle_until=115)
        scene.render(snapshot, 43, 100).save(str(output / (orientation + "-大鲸鱼与今日图鉴.png")))
        for item_kind in ("seawater_filter", "eel_filter"):
            item_game = copy.deepcopy(game.snapshot(43))
            item_game.update(phase="caught", phase_elapsed=1, remaining=7, winner={"name": "星海钓鱼冠军"})
            item_game["fish"] = {**FISH[item_kind], "kind": item_kind, "weight": 4.25}
            scene.render({**snapshot, "fishing": item_game}, 43, 100).save(str(output / (orientation + "-" + FISH[item_kind]["name"] + ".png")))
        game.start_dive(44)
        game.dive.update(style="tuck", laps=3, splash=1.35)
        sheet = QImage(1280, 360 if size[0] > size[1] else 1138, QImage.Format.Format_ARGB32_Premultiplied)
        sheet.fill(QColor("#102a3b"))
        painter = QPainter(sheet)
        for index, (age, label) in enumerate(((1, "放竿"), (3, "到台边"), (4.8, "腾空翻转"), (6.3, "入水水花"),
                                             (9.2, "游圈惊鱼"), (15.4, "水中转向"), (19.3, "跃回钓台"), (23, "重新持竿"))):
            snapshot.update(fishing=game.snapshot(44 + age), fishing_at=100, animation_time=age)
            image = scene.render(snapshot, age, 100)
            image.save(str(output / f"{orientation}-跳水-{label}.png"))
            cell_width, cell_height = 320, sheet.height() / 2
            painter.drawImage(QRectF(index % 4 * cell_width, index // 4 * cell_height, cell_width, cell_height), image)
        painter.end()
        sheet.save(str(output / (orientation + "-跳水流程.png")))
        timings = []
        snapshot.update(fishing=game.snapshot(44), fishing_at=0)
        for index in range(160):
            age = index / 160 * 24
            started = time.perf_counter()
            scene.render(snapshot, age, age)
            timings.append((time.perf_counter() - started) * 1000)
        report[orientation] = {"mean_ms": round(statistics.mean(timings), 2),
                               "p95_ms": round(sorted(timings)[151], 2)}
        if orientation == "横屏":
            atlas = QImage(1200, ((len(FISH) + 3) // 4) * 190, QImage.Format.Format_ARGB32_Premultiplied)
            atlas.fill(QColor("#143647"))
            painter = QPainter(atlas)
            painter.setFont(QFont("Microsoft YaHei", 13))
            painter.setPen(QColor("#d8ede9"))
            for index, (kind, image) in enumerate(scene.fishing.fish_images.items()):
                left, top = index % 4 * 300, index // 4 * 190
                painter.drawImage(QPointF(left, top), image)
                painter.drawText(QRectF(left + 18, top + 155, 280, 30), FISH[kind]["name"])
            painter.end()
            atlas.save(str(output / "水族与奇物图鉴.png"))
    return report


def native_check(application, output):
    report = {}
    with tempfile.TemporaryDirectory(prefix="fishing-expansion-") as temporary:
        folder = Path(temporary)
        settings_store.CONFIG_FILE = folder / "runtime.json"
        store._data = copy.deepcopy(DEFAULTS)
        store._data["ingest"]["enabled"] = False
        store._data["tts"]["engine"] = "none"
        store._data["templates"]["enabled"] = False
        store._data["idle"]["actions_enabled"] = False
        store._data["like_goal"]["enabled"] = False
        store._data["gift_show"].update(voice=False, beer_video=False)
        store._data["fishing"].update(dedicated=True, voice=False, escape_chance=0, bite_min=3, bite_max=5)
        settings.llm_api_key = settings.llm_base_url = settings.llm_model = ""
        settings.demo = False
        from app.main import app as service
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(service, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        window = None
        try:
            wait_until(application, lambda: server.started)
            window = StudioWindow(f"http://127.0.0.1:{port}", 720, 1280, 30, 0, muted=True,
                                  display_settings_path=folder / "中文画面设置.json")
            window.show()
            wait_until(application, lambda: window.state.connected and window.state.fishing.get("active"))
            round_id = window.state.fishing["round_id"]
            window.client.send({"cmd": "reaction", "motion": "handstand"})
            wait_until(application, lambda: window.state.fishing.get("phase") == "interacting")
            pump(application, 1)
            window.frames.latest()[0].save(str(output / "原生常驻倒立.png"))
            wait_until(application, lambda: window.state.fishing.get("phase") != "interacting", 10)
            assert window.state.fishing["round_id"] == round_id
            report["常驻动作中断后继续原竿"] = "通过"
            window.client.post("/api/inject", {"kind": "gift", "gift": "鲜花", "user": "礼物贡献冠军", "count": 20})
            wait_until(application, lambda: bool(window.state.gift_show.get("active")))
            wait_until(application, lambda: window.state.fishing.get("phase") == "caught", 22)
            assert window.state.fishing["winner"]["name"] == "礼物贡献冠军"
            assert window.state.stats["fishingRanking"]["encounters"] >= 1
            window.frames.latest()[0].save(str(output / "原生礼物拉钩冠军.png"))
            report["礼物助力、冠军归属、今日图鉴"] = "通过"
            wait_until(application, lambda: window.state.fishing.get("round_id") != round_id, 10)
            round_id = window.state.fishing["round_id"]
            window.client.post("/api/inject", {"kind": "chat", "text": "花式跳水", "user": "跳水鱼友"})
            wait_until(application, lambda: window.state.fishing.get("phase") == "diving")
            wait_until(application, lambda: fishing_view(window.state.snapshot(), time.monotonic())["phase_elapsed"] > 7, 10)
            paused_games = []
            window.client.message.connect(lambda message: paused_games.append(message) if message.get("type") == "fishing" and (message.get("fishing") or {}).get("paused") else None)
            window.toggle_pause()
            wait_until(application, lambda: window.state.paused and bool(paused_games))
            frozen = fishing_view(window.state.snapshot(), time.monotonic())
            pump(application, .4)
            assert fishing_view(window.state.snapshot(), time.monotonic()) == frozen
            hellos = []
            window.client.message.connect(lambda message: hellos.append(message) if message.get("type") == "hello" else None)
            window.client.socket.abort()
            wait_until(application, lambda: bool(hellos), 8)
            assert window.state.fishing["dive"] == frozen["dive"]
            assert window.state.fishing["phase_elapsed"] == frozen["phase_elapsed"]
            window.toggle_pause()
            wait_until(application, lambda: not window.state.paused)
            switch_layout(application, window, "landscape")
            window.showMinimized()
            before, started = window.frames.sequence, time.monotonic()
            pump(application, 2)
            fps = (window.frames.sequence - before) / (time.monotonic() - started)
            assert fps > 20 and not window.frames.error
            window.frames.latest()[0].save(str(output / "原生横屏水中游圈.png"))
            report["最小化跳水游圈FPS"] = round(fps, 2)
            wait_until(application, lambda: window.state.fishing.get("round_id") != round_id, 22)
            assert window.state.fishing["active"] and window.state.fishing["phase"] == "waiting"
            report["完整跳水、暂停重连、回台重抛"] = "通过"
            pump(application, 1.2)
            restored = LiveHistory(service.state.history.path, Metrics(), Hub(), LikeGoal())
            assert restored.restore()
            restored_board = restored.metrics.fishing_ranking()
            assert restored_board["species"] or restored_board["items"]
            report["今日鱼种落盘与恢复"] = "通过"
            window.client.post("/api/inject", {"kind": "chat", "text": "退出钓鱼界面", "user": "钓台管理员", "is_admin": True})
            wait_until(application, lambda: not window.state.fishing.get("active") and not window.state.config["fishing"]["dedicated"])
            saved = json.loads(settings_store.CONFIG_FILE.read_text(encoding="utf-8"))
            assert saved["fishing"]["dedicated"] is False
            window.client.post("/api/config", {"fishing": {"rank_control": "fishing"}})
            wait_until(application, lambda: window.state.config["fishing"]["rank_control"] == "fishing")
            window.client.post("/api/inject", {"kind": "chat", "text": "切换钓鱼界面", "user": "礼物贡献冠军", "user_id": "spoof"})
            wait_until(application, lambda: "仅限管理员" in window.state.subtitle)
            assert not window.state.fishing.get("active")
            window.client.post("/api/inject", {"kind": "chat", "text": "切换钓鱼界面", "user": "礼物贡献冠军"})
            wait_until(application, lambda: window.state.fishing.get("active") and window.state.config["fishing"]["dedicated"])
            identifier = window.state.fishing["round_id"]
            window.client.post("/api/inject", {"kind": "chat", "text": "切换钓鱼界面", "user": "礼物贡献冠军"})
            wait_until(application, lambda: "已经在常驻" in window.state.subtitle)
            assert window.state.fishing["round_id"] == identifier
            window.client.post("/api/inject", {"kind": "chat", "text": "退出钓鱼界面", "user": "礼物贡献冠军"})
            wait_until(application, lambda: not window.state.fishing.get("active"))
            window.client.send({"cmd": "fishing", "action": "enter_dedicated"})
            wait_until(application, lambda: window.state.fishing.get("active"))
            report["管理员与榜单弹幕切换、拒绝同名冒用、重复进入、设置持久化"] = "通过"
        finally:
            if window:
                window.close()
                pump(application, .2)
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive()
    return report


def main():
    output = ROOT / "logs" / "fishing-expansion-check"
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    report = {"原生跳水绘制": samples(output)}
    if "--screens-only" not in sys.argv:
        report.update(native_check(application, output))
    (output / "结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
