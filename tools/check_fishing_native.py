"""用隔离配置验收原生钓鱼、贡献归属、暂停恢复和滚动公告。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from PySide6.QtWidgets import QApplication

from app import settings_store
from app.config import settings
from app.native.fishing_scene import fishing_view
from app.native.preferences import Preferences
from app.native.window import StudioWindow
from app.settings_store import store
from app.world import view
from tools.check_native import pump, switch_layout, wait_until


def main():
    report_dir = ROOT / "logs" / "fishing-native-check"
    report_dir.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    application.setStyle("Fusion")
    application.setQuitOnLastWindowClosed(False)
    report = {}
    with tempfile.TemporaryDirectory(prefix="fishing-native-") as temporary:
        settings_store.CONFIG_FILE = Path(temporary) / "runtime.json"
        store._data = copy.deepcopy(settings_store.DEFAULTS)
        store._data["ingest"]["enabled"] = False
        store._data["tts"]["engine"] = "none"
        store._data["templates"]["enabled"] = False
        store._data["idle"]["actions_enabled"] = False
        store._data["fishing"].update(escape_chance=0, bite_min=3, bite_max=5)
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
                                  display_settings_path=Path(temporary) / "画面设置.json")
            window.show()
            wait_until(application, lambda: window.state.connected and bool(window.state.config))
            wait_until(application, lambda: window.frames.fps > 0)
            preferences = Preferences(window.client, window)
            preferences.show()
            wait_until(application, lambda: preferences.save.isEnabled())
            text = "协力钓鱼：咬钩后发送拉钩，点赞也能帮忙！\n贡献冠军获得捕获祝贺 · 收竿返回原场景"
            preferences.fields[("ticker", "text")].setPlainText(text)
            preferences.fields[("ticker", "speed")].setValue(75)
            for tab_name in ("钓鱼玩法", "滚动公告"):
                preferences.tabs.setCurrentIndex(next(index for index in range(preferences.tabs.count())
                                                      if preferences.tabs.tabText(index) == tab_name))
                pump(application, .1)
                preferences.grab().save(str(report_dir / f"设置-{tab_name}.png"))
            preferences._save()
            wait_until(application, lambda: not preferences.isVisible())
            wait_until(application, lambda: window.state.config.get("ticker", {}).get("speed") == 75)
            assert store.section("ticker")["text"] == text
            preferences.deleteLater()
            report["中文公告及速度保存"] = "通过"

            window.client.send({"cmd": "world", "action": "scene", "value": "cloud"})
            wait_until(application, lambda: window.state.world.get("scene") == "cloud")
            original_position = view(window.state.world)["position"]
            window.client.post("/api/inject", {"kind": "chat", "text": "去钓鱼", "user": "观众甲"})
            wait_until(application, lambda: window.state.fishing.get("active"))
            wait_until(application, lambda: window.state.fishing.get("phase") == "hooked", 10)
            assert service.state.brain.world.paused_at is not None
            window.client.post("/api/inject", {"kind": "chat", "text": "拉钩", "user": "星海旅人"})
            wait_until(application, lambda: bool(window.state.fishing.get("winner")))
            assert window.state.fishing["winner"]["name"] == "星海旅人"
            progress = window.state.fishing["progress"]
            window.client.post("/api/inject", {"kind": "chat", "text": "拉钩", "user": "小鱼同学"})
            wait_until(application, lambda: window.state.fishing["progress"] > progress)
            assert window.state.fishing["winner"]["name"] == "星海旅人"
            report["重复弹幕推进且同贡献先参与者优先"] = "通过"

            window.toggle_pause()
            wait_until(application, lambda: window.state.paused and window.state.fishing.get("paused"))
            frozen = fishing_view(window.state.snapshot(), time.monotonic())
            pump(application, .5)
            assert fishing_view(window.state.snapshot(), time.monotonic()) == frozen
            round_id = window.state.fishing["round_id"]
            window.client.socket.abort()
            wait_until(application, lambda: not window.state.connected)
            wait_until(application, lambda: window.state.connected and window.state.fishing.get("round_id") == round_id)
            assert window.state.fishing["winner"]["name"] == "星海旅人"
            assert window.state.fishing["paused"]
            window.toggle_pause()
            wait_until(application, lambda: not window.state.paused and not window.state.fishing.get("paused"))
            report["暂停与断线重连保留进度"] = "通过"

            for orientation, name in (("portrait", "竖屏"), ("landscape", "横屏")):
                switch_layout(application, window, orientation)
                pump(application, .15)
                window.frames.latest()[0].save(str(report_dir / f"{name}-协力收线.png"))
                if window.popout:
                    window.popout.hide()
                window.showMinimized()
                before = window.frames.sequence
                started = time.monotonic()
                pump(application, 3)
                elapsed = time.monotonic() - started
                rendered = window.frames.sequence - before
                assert rendered >= 65 and not window.frames.error
                report[f"{name}最小化"] = {"帧数": rendered, "秒数": round(elapsed, 2), "帧率": round(rendered / elapsed, 2)}
                window.showNormal()
            window.client.post("/api/inject", {"kind": "like", "count": 100})
            wait_until(application, lambda: window.state.fishing.get("phase") == "caught")
            pump(application, .2)
            window.frames.latest()[0].save(str(report_dir / "横屏-恭喜贡献冠军.png"))
            assert window.state.fishing["winner"]["name"] == "测试观众"
            assert window.state.fishing["fish"]["value"] > 0
            wait_until(application, lambda: bool(window.state.stats.get("fishingRanking", {}).get("ranking")))
            winner_score = window.state.stats["fishingRanking"]["ranking"][0]
            assert winner_score["name"] == "测试观众" and winner_score["count"] == 1
            assert winner_score["value"] == window.state.fishing["fish"]["value"]
            report["贡献冠军累计鱼获排行"] = "通过"
            context_responses = []
            window.client.response.connect(lambda path, result: context_responses.append(result) if path == "GET /api/ai/context" else None)
            window.client.get("/api/ai/context")
            wait_until(application, lambda: bool(context_responses))
            assert context_responses[-1]["context"]["fishing"]["phase"] == "caught"
            assert context_responses[-1]["context"]["fishing"]["leaderboard"]["ranking"][0]["value"] == winner_score["value"]
            wait_until(application, lambda: not window.state.fishing.get("active"), 8)
            assert window.state.world["scene"] == "cloud"
            assert view(window.state.world)["position"] == original_position
            assert service.state.brain.world.paused_at is None
            report["点赞钓获展示并返回原场景"] = "通过"
            report["AI同步钓鱼状态"] = "通过"

            window.client.send({"cmd": "fishing", "action": "start"})
            wait_until(application, lambda: window.state.fishing.get("active"))
            window.client.post("/api/inject", {"kind": "chat", "text": "收竿", "user": "观众乙"})
            wait_until(application, lambda: not window.state.fishing.get("active"))
            window.client.send({"cmd": "fishing", "action": "start"})
            wait_until(application, lambda: window.state.fishing.get("active"))
            window.client.post("/api/config", {"fishing": {"enabled": False}, "ticker": {"enabled": False}})
            wait_until(application, lambda: not window.state.fishing.get("active") and not window.state.config["ticker"]["enabled"])
            assert window.state.world["scene"] == "cloud"
            report["收竿与关闭玩法返回"] = "通过"
            from app.hub import Hub
            from app.like_goal import LikeGoal
            from app.live_history import LiveHistory
            from app.metrics import Metrics
            pump(application, 1.1)
            restored = LiveHistory(Path(temporary) / "live-stats.sqlite3", Metrics(), Hub(), LikeGoal())
            assert restored.restore()
            assert restored.metrics.fishing_ranking()["ranking"][0]["value"] == winner_score["value"]
            report["鱼获排行榜存盘恢复"] = "通过"
            assert not window.frames.error
            (report_dir / "结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            if window:
                window.close()
                pump(application, .2)
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
