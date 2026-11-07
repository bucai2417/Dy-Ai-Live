"""FastAPI 应用：静态站点 + WebSocket 实时总线 + 控制台 API。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, models_store, music, playlist
from .brain.scheduler import Brain
from .config import LOG_DIR, ROOT, WEB_DIR, settings
from .events import Event
from .paths import renderer_command
from .hub import hub
from .ingest.base import build_ingestor
from .metrics import metrics
from .settings_store import Effective, store

MODELS_DIR = models_store.MODELS_DIR
MAX_MODEL_ZIP = 200 * 1024 * 1024  # 200MB，够放绝大多数 Live2D 模型

# Windows 控制台默认 GBK，中文日志会乱码；统一成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# 无人值守必须能事后排查：滚动日志文件，单文件 5MB，保留 5 份
_file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
)
logging.getLogger().addHandler(_file_handler)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ingest_lock = asyncio.Lock()
    app.state.config_lock = asyncio.Lock()
    app.state.live_lock = asyncio.Lock()
    app.state.renderer_lock = asyncio.Lock()
    app.state.command_tasks = set()
    brain = Brain()
    brain.config_lock = app.state.config_lock
    from .live_history import LiveHistory
    from . import settings_store
    history = LiveHistory(settings_store.CONFIG_FILE.parent / "live-stats.sqlite3", metrics, hub, brain.likes)
    await asyncio.to_thread(history.restore)
    history_stop = asyncio.Event()
    history_task = asyncio.create_task(history.run(history_stop), name="daily-statistics")
    app.state.history = history
    ingestor = build_ingestor()

    async def emit(ev: Event) -> None:
        hub.feed(ev)

    ingest_task = asyncio.create_task(ingestor.run(emit), name=f"ingest:{ingestor.name}")
    brain_task = asyncio.create_task(brain.run(), name="brain")
    app.state.brain_task = brain_task
    app.state.brain = brain
    app.state.ingestor = ingestor
    brain.ingestor = ingestor          # 统计推送要读抖音真实在线人数
    app.state.ingest_task = ingest_task
    log.info("接入源=%s | 页面 http://127.0.0.1:%s/stage", ingestor.name, settings.port)
    try:
        yield
    finally:
        async with app.state.ingest_lock:
            await _stop_ingest(app.state.ingestor, app.state.ingest_task)
        brain_task.cancel()
        for task in list(app.state.command_tasks):
            task.cancel()
        await asyncio.gather(*app.state.command_tasks, return_exceptions=True)
        await asyncio.gather(brain_task, return_exceptions=True)
        history_stop.set()
        await history_task
        await brain.llm.aclose()
        await _stop_renderer()


app = FastAPI(title="大肥鱼 AI 直播", version=__version__, lifespan=lifespan)


# ------------------------------------------------------------------ 页面
@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "stage.html")


@app.get("/stage")
async def stage():
    return FileResponse(WEB_DIR / "stage.html")


@app.get("/console")
async def console():
    return FileResponse(WEB_DIR / "console.html")


@app.get("/settings")
async def settings_page():
    return FileResponse(WEB_DIR / "settings.html")


@app.get("/whale-lab")
async def whale_lab():
    """程序化形象调试台：试动作、表情、口型，调好再上直播。"""
    return FileResponse(WEB_DIR / "whale-lab.html")


# ------------------------------------------------------------------ WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    hub.stats["online"] = len(hub.clients)

    try:
        await ws.send_json({
            "type": "hello", "config": _public_config(),
            "recent": list(hub.recent)[-30:],
            "stats": metrics.snapshot(Effective.pricing()),
            "eventStats": dict(hub.stats),
            "music": playlist.queue.snapshot(), "paused": app.state.brain.paused,
            "likeGoal": app.state.brain.likes.snapshot(),
            "world": app.state.brain.world.snapshot(),
            "performance": hub.performance.snapshot(),
            "fishing": app.state.brain.fishing.snapshot(),
            "giftShow": app.state.brain.gift_show.snapshot(),
        })
        await hub.broadcast({"type": "online", "count": len(hub.clients)}, remember=False)
        while True:
            raw = await ws.receive_text()
            await handle_client_msg(raw, ws)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("ws 异常：%s", exc)
    finally:
        hub.clients.discard(ws)
        hub.playback.remove(ws)
        hub.stats["online"] = len(hub.clients)
        await hub.broadcast({"type": "online", "count": len(hub.clients)}, remember=False)


async def handle_client_msg(raw: str, ws: WebSocket) -> None:
    """控制台 → 服务端指令。"""
    brain: Brain = app.state.brain
    try:
        msg = json.loads(raw)
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    cmd = msg.get("cmd")

    if cmd == "renderer_ready":
        hub.playback.register(ws)
    elif cmd == "voice_finished":
        identifier = msg.get("id")
        status = msg.get("status")
        if isinstance(identifier, str) and status in ("ended", "error", "interrupted"):
            hub.playback.finish(ws, identifier, status)
    elif cmd == "say":  # 人工插话
        if brain.paused:
            await ws.send_json({"type": "error", "error": "直播已暂停，请先恢复直播"})
            return
        text = str(msg.get("text", "")).strip()
        if text:
            if len(app.state.command_tasks) >= 4:
                await ws.send_json({"type": "error", "error": "人工播报处理中，请稍后再试"})
                return
            task = asyncio.create_task(_manual_say(text), name="manual:say")
            app.state.command_tasks.add(task)
            task.add_done_callback(app.state.command_tasks.discard)
    elif cmd == "pause":
        await _set_live_paused(True)
    elif cmd == "resume":
        await _set_live_paused(False)
    elif cmd == "skip":
        await brain.stop_speech()
    elif cmd == "reaction":
        from .native.actions import ACTIONS
        motion = str(msg.get("motion") or "")
        if brain.paused:
            await ws.send_json({"type": "error", "error": "直播已暂停，请先恢复直播"})
        elif motion not in ACTIONS:
            await ws.send_json({"type": "error", "error": "未知人物动作"})
        else:
            if brain.fishing.active:
                try:
                    await brain.interrupt_fishing(ACTIONS[motion]["label"], ACTIONS[motion]["duration"] + .5)
                except ValueError as error:
                    await ws.send_json({"type": "error", "error": str(error)})
                    return
            await hub.broadcast({"type": "reaction", "motion": motion, "manual": True}, remember=False)
    elif cmd == "world":
        opened_interruption = False
        try:
            if brain.gift_show.active:
                raise ValueError("礼物演出结束后再切换场景")
            if not brain.paused:
                if brain.dedicated_fishing and brain.fishing.active:
                    opened_interruption = brain.fishing.phase != "interacting"
                    await brain.interrupt_fishing("场景互动", 25)
                else:
                    await brain.cancel_fishing()
            brain.world.command(msg.get("action"), msg.get("value"), manual=True)
            if brain.fishing.phase == "interacting":
                brain.fishing.interrupt("场景互动", max(25, brain.world.describe()["remaining_seconds"] + 10))
                await brain._push_fishing()
        except ValueError as error:
            if opened_interruption and brain.fishing.resume():
                await brain._sync_fishing()
            await ws.send_json({"type": "error", "error": str(error)})
        else:
            await hub.broadcast({"type": "world", "world": brain.world.snapshot()}, remember=False)
    elif cmd == "fishing":
        try:
            await brain.fishing_command(msg.get("action"), manual=True)
        except (ValueError, OSError) as error:
            await ws.send_json({"type": "error", "error": str(error)})
    elif cmd == "idle_preview":
        if not await brain.idle_action(manual=True):
            await ws.send_json({"type": "error", "error": "直播已暂停或未选择冷场动作"})
    elif cmd == "stunt":
        try:
            await brain.play_stunt(str(msg.get("motion") or ""))
        except ValueError as error:
            await ws.send_json({"type": "error", "error": str(error)})
    elif cmd == "fake_danmu":  # 演示用：直接注入一条弹幕
        text = str(msg.get("text", "")).strip()
        if text:
            hub.feed(Event(kind="chat", user_id=f"demo{int(time.time())}", user_name="测试观众", text=text))
    elif cmd in ("costume", "video"):
        from .native.actions import COSTUMES
        if brain.paused:
            await ws.send_json({"type": "error", "error": "直播已暂停，请先恢复直播"})
        elif cmd == "costume" and isinstance(msg.get("name"), str) and msg["name"] in (*COSTUMES, "next_outfit", "next_accessory"):
            from .wardrobe import wear
            try:
                await wear(msg["name"], hub, manual=True)
            except (ValueError, OSError) as error:
                await ws.send_json({"type": "error", "error": str(error)})
        elif cmd == "video" and msg.get("clip") == "milk-frog" and Effective.stage()["video_enabled"]:
            try:
                await brain.interrupt_fishing("奶蛙短片", 9)
            except ValueError as error:
                await ws.send_json({"type": "error", "error": str(error)})
                return
            await hub.broadcast({"type": "video", "clip": "milk-frog", "manual": True}, remember=False)
        else:
            await ws.send_json({"type": "error", "error": "未知装扮，或互动视频未启用"})
    elif cmd == "ping":
        await ws.send_json({"type": "pong", "ts": time.time()})


async def _manual_say(text: str) -> None:
    try:
        await app.state.brain.stop_speech()
        await app.state.brain._speak(text, Event(kind="system", user_name="主播"), 99.0)
    except Exception:
        log.exception("人工播报失败")


async def _set_live_paused(paused: bool) -> None:
    async with app.state.live_lock:
        brain = app.state.brain
        if paused:
            tasks = list(app.state.command_tasks)
            for task in tasks:
                task.cancel()
        await brain.set_paused(paused)
        if paused:
            await asyncio.gather(*tasks, return_exceptions=True)


@app.post("/api/live/{action}")
async def api_live_action(action: str):
    if action not in ("pause", "resume"):
        return JSONResponse({"ok": False, "error": "未知操作"}, status_code=400)
    await _set_live_paused(action == "pause")
    return JSONResponse({"ok": True, "paused": app.state.brain.paused})


# ------------------------------------------------------------------ API
@app.get("/api/state")
async def api_state():
    brain: Brain = app.state.brain
    return JSONResponse({
        "paused": brain.paused,
        "busy": brain.speaker.busy,
        "llm": brain.llm.active,
        "llm_configured": brain.llm.enabled,
        "llm_status": brain.llm.status_text,
        "tts": brain.speaker.tts.name,
        "ingest": app.state.ingestor.name,
        "stats": hub.stats,
    })


# ------------------------------------------------------------------ 配置（后台可编辑）
@app.get("/api/config")
async def api_config_get():
    """返回运营可编辑配置；其中留空但已由 .env 兜底的项，回填实际生效值。"""
    cfg = store.get_all()
    cfg["ingest"] = Effective.ingest()
    return JSONResponse(cfg)


@app.post("/api/config")
async def api_config_update(patch: dict):
    async with app.state.config_lock:
        before = Effective.ingest()
        previous = store.get_all()
        try:
            updated = store.update(patch)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        result = await _apply_config(before, previous)
        log.info("配置已更新：%s", ", ".join(sorted(patch.keys())))
        return JSONResponse({"ok": True, "config": updated, **result})


def _public_config() -> dict:
    ingest = Effective.ingest()
    return {
        "name": store.persona_name,
        "ingest": ingest["source"] if ingest["enabled"] else "off",
        "ingestConfiguredSource": ingest["source"],
        "room": ingest["douyin_room"],
        "tts": Effective.tts()["engine"],
        "stage": Effective.stage(),
        "world": Effective.world(),
        "fishing": {key: value for key, value in Effective.fishing().items() if key != "admin_ids"},
        "giftShow": Effective.gift_show(),
        "bgm": Effective.bgm(),
        "ticker": Effective.ticker(),
        "likeGoal": Effective.like_goal(),
        "fallback": Effective.fallback(),
        "templates": Effective.templates(),
        "music": _music_pub(),
        "interact": {"enabled": Effective.interact()["enabled"], "intent_routing": True},
        "llm": app.state.brain.llm.active,
        "llmStatus": app.state.brain.llm.status_text,
    }


async def _apply_config(before: dict, previous: dict) -> dict:
    from .tts.edge import build_engine

    result = {}
    if before != Effective.ingest():
        result["ingest"] = await _rebuild_ingest()
    after = Effective.ingest()
    if (before.get("source"), before.get("douyin_room")) != (after.get("source"), after.get("douyin_room")):
        app.state.brain.likes.pending.clear()
        app.state.brain.likes.active = None
    if previous.get("tts") != store.section("tts"):
        app.state.brain.speaker.tts = build_engine()
    if previous.get("llm") != store.section("llm"):
        app.state.brain.llm.reset_breaker()
    if previous.get("music") != store.section("music"):
        music.clear_cache()
        if playlist.queue.enforce_policy():
            await app.state.brain._push_music()
    app.state.brain.likes.configure(Effective.like_goal(), Effective.interact()["enabled"])
    app.state.brain.world.configure(Effective.world())
    await hub.broadcast({"type": "config_changed", "config": _public_config()}, remember=False)
    await app.state.brain.configure_live_features(previous.get("fishing"))
    await hub.broadcast({"type": "world", "world": app.state.brain.world.snapshot()}, remember=False)
    await app.state.brain._push_likes()
    await app.state.brain._push_stats()
    return result


@app.post("/api/config/reset")
async def api_config_reset():
    async with app.state.config_lock:
        before = Effective.ingest()
        previous = store.get_all()
        try:
            updated = store.reset()
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        result = await _apply_config(before, previous)
        return JSONResponse({"ok": True, "config": updated, **result})


@app.get("/api/voices")
async def api_voices():
    """列出可用的中文语音，供后台下拉选择。"""
    try:
        import edge_tts

        voices = await edge_tts.list_voices()
        zh = [
            {"id": v["ShortName"], "gender": v.get("Gender", ""), "locale": v.get("Locale", "")}
            for v in voices
            if str(v.get("Locale", "")).startswith("zh-")
        ]
        return JSONResponse({"ok": True, "voices": zh})
    except Exception as exc:
        log.warning("获取语音列表失败：%s", exc)
        return JSONResponse({"ok": False, "voices": [], "error": str(exc)})


@app.get("/api/stats")
async def api_stats():
    """今日 token 消耗 / 对话次数 / 礼物榜，后台和直播画面共用。"""
    return JSONResponse({"ok": True, "stats": metrics.snapshot(Effective.pricing())})


# ------------------------------------------------------------------ 音乐
@app.get("/api/music")
async def api_music_state():
    return JSONResponse({"ok": True, "music": playlist.queue.snapshot(),
                         "conf": _music_pub()})


def _music_pub() -> dict:
    """给前端的音乐配置（不含 cookie）。"""
    c = Effective.music()
    c.pop("cookie", None)
    return c


@app.get("/api/music/search")
async def api_music_search(q: str = ""):
    cfg = Effective.music()
    try:
        rows = await music.search(q, cookie=cfg["cookie"], limit=8)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "results": rows})


@app.post("/api/music/request")
async def api_music_request(body: dict | None = None):
    """点歌。body: {query, user?}"""
    b = body or {}
    query = str(b.get("query") or "").strip()
    user = str(b.get("user") or "主播")
    if not query:
        return JSONResponse({"ok": False, "error": "歌名不能为空"}, status_code=400)
    ok, line = await app.state.brain._enqueue(query, user)
    await app.state.brain._push_music()
    return JSONResponse({"ok": ok, "msg": line, "music": playlist.queue.snapshot()})


@app.post("/api/music/{action}")
async def api_music_action(action: str, body: dict | None = None):
    """next / stop / resume / clear / remove / seek-to-id"""
    b = body or {}
    if app.state.brain.paused and action in ("next", "resume", "ai"):
        return JSONResponse({"ok": False, "error": "直播已暂停，请先恢复直播"}, status_code=409)
    if action == "next":
        playlist.queue.next_track()
    elif action == "stop":
        app.state.brain._resume_music_id = None
        app.state.brain._music_was_paused = True
        playlist.queue.pause()
    elif action == "resume":
        playlist.queue.resume()
    elif action == "clear":
        playlist.queue.clear()
    elif action == "remove":
        playlist.queue.remove(str(b.get("id") or ""))
    elif action == "played":
        # 前端播放结束回调：如果不是当前这首就忽略（切歌有竞态）
        cur = playlist.queue.current
        if playlist.queue.playing and cur and str(b.get("id") or "") == cur.id:
            playlist.queue.next_track()
    elif action == "ai":
        query = str(b.get("query") or "").strip()
        if not query:
            return JSONResponse({"ok": False, "error": "歌名不能为空"}, status_code=400)
        ok, line = await app.state.brain.ai_song(query)
        await app.state.brain._push_music()
        return JSONResponse({"ok": ok, "msg": line, "music": playlist.queue.snapshot()})
    elif action == "volume":
        try:
            store.update({"music": {"volume": float(b.get("volume", 0.6))}})
        except (TypeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        await hub.broadcast({"type": "config_changed", "config": {"music": _music_pub()}}, remember=False)
    else:
        return JSONResponse({"ok": False, "error": "未知操作"}, status_code=400)

    await app.state.brain._push_music()
    return JSONResponse({"ok": True, "music": playlist.queue.snapshot()})


@app.get("/api/music/stream/{track_id}")
async def api_music_stream(track_id: str):
    """本地曲库文件流（支持 Range，方便 <audio> 拖动进度）。"""
    from .music_policy import blocked_reason

    info = next((row for row in music.list_local() if row["id"] == track_id), None)
    reason = blocked_reason(info) if info else ""
    if reason:
        return JSONResponse({"ok": False, "error": reason}, status_code=403)
    p = music.local_path(track_id)
    if not p:
        return JSONResponse({"ok": False, "error": "曲目不存在"}, status_code=404)
    return FileResponse(p)


@app.get("/api/music/local")
async def api_music_local():
    from .music_policy import blocked_reason

    return JSONResponse({"ok": True, "tracks": [row for row in music.list_local(force=True) if not blocked_reason(row)],
                         "dir": str(music.music_dir())})


@app.post("/api/say")
async def api_say(body: dict | None = None):
    """以主播身份手动说一句（后台调试/补话用）。"""
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "内容不能为空"}, status_code=400)
    if app.state.brain.paused:
        return JSONResponse({"ok": False, "error": "直播已暂停，请先恢复直播"}, status_code=409)
    if len(app.state.command_tasks) >= 4:
        return JSONResponse({"ok": False, "error": "人工播报处理中，请稍后再试"}, status_code=429)
    task = asyncio.create_task(_manual_say(text), name="manual:say")
    app.state.command_tasks.add(task)
    task.add_done_callback(app.state.command_tasks.discard)
    try:
        await task
    except asyncio.CancelledError:
        return JSONResponse({"ok": False, "error": "播报已中止"}, status_code=409)
    return JSONResponse({"ok": True})


@app.get("/api/ai/context")
async def api_ai_context():
    return JSONResponse({"ok": True, "context": app.state.brain.runtime_context()})


@app.post("/api/session/boot")
async def api_session_boot(keep: bool = False):
    """由控制台显式清场；普通刷新保留直播会话。"""
    if keep:
        return JSONResponse({"ok": True, "kept": True})

    await app.state.brain.stop_speech()
    app.state.brain._pending_replies.clear()
    app.state.brain.pending_enter.clear()
    app.state.brain.pending_enter_at = 0.0
    app.state.brain._history.clear()
    app.state.brain.last_interaction = None
    app.state.brain.last_operation = {}
    app.state.brain.gift_show.clear()
    app.state.brain.fishing.cancel()
    app.state.brain.sync_activity_pauses()
    while not hub.queue.empty():
        hub.queue.get_nowait()
        hub.queue.task_done()
    playlist.queue.clear()
    playlist.queue.stop()
    playlist.queue.history.clear()
    app.state.brain.likes.pending.clear()
    app.state.brain.likes.active = None
    hub.recent.clear()
    await hub.broadcast({"type": "reset"}, remember=False)
    await app.state.brain.configure_live_features()
    await hub.broadcast({"type": "gift_ranking", "ranking": metrics.gift_board.snapshot()}, remember=False)
    await app.state.brain._push_likes()
    await app.state.brain._push_stats()
    log.info("控制台已清场（队列/当前曲目/弹幕）")
    return JSONResponse({"ok": True, "kept": False})


@app.get("/api/models")
async def api_models():
    """列出已安装的 Live2D 模型。"""
    return JSONResponse({"ok": True, "models": models_store.list_models()})


@app.post("/api/models/upload")
async def api_models_upload(file: UploadFile = File(...), name: str = Form("")):
    """上传 Live2D 模型 zip，自动解压、校验并修复缺失引用。"""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return JSONResponse({"ok": False, "error": "请上传 .zip 压缩包"}, status_code=400)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODELS_DIR / f".upload-{int(time.time() * 1000)}.zip"
    try:
        size = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_MODEL_ZIP:
                    return JSONResponse(
                        {"ok": False, "error": f"压缩包超过 {MAX_MODEL_ZIP // 1024 // 1024}MB 上限"},
                        status_code=400,
                    )
                f.write(chunk)
        result = models_store.import_zip(tmp, display_name=name or Path(file.filename).stem)
    finally:
        with contextlib.suppress(Exception):
            tmp.unlink()

    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    log.info("模型已导入：%s（%d 个文件，修复 %d 处）",
             result["name"], result["files"], len(result.get("fixed") or []))
    return JSONResponse(result)


@app.delete("/api/models")
async def api_models_delete(body: dict):
    result = models_store.delete_model((body or {}).get("id") or "")
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    log.info("模型已删除：%s", result.get("deleted"))
    return JSONResponse(result)


@app.get("/api/stage")
async def api_stage_get():
    return JSONResponse({
        "ok": True,
        "stage": Effective.stage(),
        "interact": Effective.interact(),
        "fallback": Effective.fallback(),
        "templates": Effective.templates(),
        "ingest": {"enabled": Effective.ingest()["enabled"]},
    })


@app.post("/api/inject")
async def api_inject(body: dict | None = None):
    """注入一条事件，用于测试互动规则 / 演示，不必等真实弹幕。

    body: {kind?: chat|gift|enter|follow, text?, user?, gift?, value?, count?}
    """
    b = body or {}
    kind = str(b.get("kind") or "chat")
    user = str(b.get("user") or "测试观众")
    user_id = str(b.get("user_id") or "inj:" + user)[:100]
    avatar = str(b.get("avatar") or "")
    if kind == "gift":
        from .ingest.douyin_cdp import _gift_value

        gift = str(b.get("gift") or "小心心")
        count = int(b.get("count") or 1)
        value = int(b.get("value") or _gift_value(gift) * count)
        ev = Event(kind="gift", user_id=user_id, user_name=user, gift_name=gift, gift_count=count,
                   gift_value=value, text=f"送出了{gift}x{count}", avatar=avatar)
    elif kind in ("enter", "follow", "like"):
        try:
            count = int(b.get("count", 1))
            if not 1 <= count <= 1000000:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            return JSONResponse({"ok": False, "error": "互动数量必须是 1～1000000 的整数"}, status_code=400)
        ev = Event(kind=kind, user_id=user_id, user_name=user, gift_count=count,
                   avatar=avatar)
    else:
        ev = Event(kind="chat", user_id=user_id,
                    user_name=user, text=str(b.get("text") or ""), avatar=avatar, is_admin=b.get("is_admin") is True)

    hub.feed(ev)
    return JSONResponse({"ok": True, "event": ev.to_dict()})


@app.post("/api/llm/test")
async def api_llm_test(body: dict | None = None):
    """后台"测试连接"按钮：绕过熔断，真的打一次接口验证 Key / 模型名。

    必须绕开 stream()：那里熔断时会直接吐离线话术，看起来"有回复"，
    实际根本没连上，用户就会被"测试通过"骗过去。
    """
    import httpx

    brain: Brain = app.state.brain
    cfg = Effective.llm()
    if not (cfg.get("base_url") and cfg.get("api_key") and cfg.get("model")):
        return JSONResponse({
            "ok": False, "offline": True,
            "error": "未配置 LLM（需要填 Base URL、API Key、模型名）",
        })

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": "回复两个字：在的"}],
        "max_tokens": 20,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0)) as c:
            r = await c.post(url, json=payload,
                             headers={"Authorization": f"Bearer {cfg['api_key']}"})
        if r.status_code in (401, 403):
            return JSONResponse({"ok": False, "error":
                f"API Key 无效（HTTP {r.status_code}）。请更换有效的 Key 后重试。"})
        if r.status_code == 404:
            return JSONResponse({"ok": False, "error":
                f"接口地址或模型名不存在（HTTP 404）。请检查 Base URL 是否以 /v1 结尾，"
                f"以及模型名「{cfg['model']}」是否可用。"})
        if r.status_code >= 400:
            return JSONResponse({"ok": False, "error":
                f"HTTP {r.status_code}：{r.text[:200]}"})
        data = r.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        metrics.add_usage(data.get("usage"), payload["messages"][0]["content"], text or "")
        await brain._push_stats()
        # 通了就把熔断清掉，让直播立刻恢复调用模型
        brain.llm.reset_breaker()
        return JSONResponse({"ok": True, "reply": (text or "").strip()})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"连接失败：{type(exc).__name__} {exc}"})


@app.post("/api/tts/preview")
async def api_tts_preview(body: dict | None = None):
    """后台"试听"按钮：用当前语音设置合成一句。"""
    import base64

    text = ((body or {}).get("text") or "").strip() or "你好，我是大肥鱼，这是试听效果"
    brain: Brain = app.state.brain
    audio = await brain.speaker.tts.synthesize(text)
    if not audio:
        return JSONResponse({"ok": False, "error": "语音合成失败，请检查网络或语音设置"})
    return JSONResponse({"ok": True, "audio": base64.b64encode(audio).decode(), "mime": "audio/mpeg"})


@app.post("/api/ingest/kill-browser")
async def api_kill_browser():
    """强制清掉采集用浏览器（不影响用户日常浏览器）。

    总开关关掉、或切到直连后如果还有残留的直播间窗口，
    点这个就能立刻收干净，不用重启服务。
    """
    from .ingest.douyin_cdp import DouyinCDPIngestor

    before = _count_auto_browsers()
    try:
        DouyinCDPIngestor(room="")._kill_by_port()
    except Exception:
        log.exception("清理浏览器失败")
    after = _count_auto_browsers()
    log.info("手动清理浏览器：%d → %d", before, after)
    return JSONResponse({"ok": True, "before": before, "after": after})


def _count_auto_browsers() -> int:
    """数一下我们自己的采集浏览器（按 user-data-dir 认，不误伤日常 Edge）。"""
    import subprocess

    from .config import ROOT

    marker = str(ROOT / "data" / "browser-profile")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Where-Object { $_.CommandLine -like $env:DSH_M } | "
        "Measure-Object | Select-Object -ExpandProperty Count"
    )
    try:
        env = dict(os.environ)
        env["DSH_M"] = f"*{marker}*"
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return int((out or "0").strip() or 0)
    except Exception:
        return -1


@app.post("/api/renderer/start")
async def api_renderer_start(body: dict | None = None):
    import subprocess
    from .renderer import renderer_status
    from .native.display import display_path, resolve_size

    options = body or {}
    try:
        width, height = resolve_size(int(options["width"]) if "width" in options else None,
                                     int(options["height"]) if "height" in options else None,
                                     display_path(18849))
        fps = int(options.get("fps", 30))
        if not (320 <= width <= 1920 and 320 <= height <= 1920 and 10 <= fps <= 60):
            raise ValueError("尺寸须为 320–1920，帧率须为 10–60")
    except (ValueError, TypeError, OverflowError) as error:
        return JSONResponse({"ok": False, "error": str(error)}, status_code=400)
    async with app.state.renderer_lock:
        status = renderer_status()
        if _renderer_alive() or status.get("running"):
            return JSONResponse({**status, "ok": True, "already": True})
        args = [*renderer_command(), "--url", f"http://127.0.0.1:{settings.port}",
                "--width", str(width), "--height", str(height), "--fps", str(fps)]
        try:
            with (LOG_DIR / "renderer.log").open("a", encoding="utf-8") as logfile:
                proc = subprocess.Popen(args, cwd=str(ROOT), stdout=logfile, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            app.state.renderer_proc = proc
            return JSONResponse({"ok": True, "pid": proc.pid, "backend": "pyside6"})
        except Exception as error:
            log.exception("启动原生窗口失败")
            return JSONResponse({"ok": False, "error": str(error)}, status_code=500)


async def _stop_renderer():
    proc = getattr(app.state, "renderer_proc", None)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, timeout=6)
        except TimeoutError:
            proc.kill()
            await asyncio.to_thread(proc.wait, timeout=3)
        except Exception:
            if proc.poll() is None:
                proc.kill()
                await asyncio.to_thread(proc.wait, timeout=3)
    app.state.renderer_proc = None


@app.post("/api/renderer/stop")
async def api_renderer_stop():
    from .renderer import renderer_status
    async with app.state.renderer_lock:
        if not _renderer_alive() and renderer_status().get("running"):
            return JSONResponse({"ok": False, "error": "该工作台由桌面直接启动，请关闭工作台窗口"}, status_code=409)
        await _stop_renderer()
    return JSONResponse({"ok": True})


@app.get("/api/renderer/status")
async def api_renderer_status():
    from .renderer import renderer_status
    status = renderer_status()
    return JSONResponse({**status, "ok": True, "running": _renderer_alive() or status.get("running", False)})


def _renderer_alive() -> bool:
    proc = getattr(app.state, "renderer_proc", None)
    return bool(proc and proc.poll() is None)


@app.get("/api/ingest/status")
async def api_ingest_status():
    """接入层详细状态：直连模式能看到"没开播"还是"连不上"。"""
    ing = app.state.ingestor
    task = app.state.ingest_task
    base = {"name": getattr(ing, "name", "?"), "alive": not task.done()}
    if task.done() and not task.cancelled() and task.exception():
        base["error"] = str(task.exception())
    if hasattr(ing, "status"):
        try:
            base["detail"] = ing.status()
        except Exception:
            pass
    return JSONResponse({"ok": True, "ingest": base})


@app.post("/api/restart-ingest")
async def api_restart_ingest():
    """改完弹幕源/房间号后，让接入层重建（不必重启整个服务）。"""
    info = await _rebuild_ingest()
    return JSONResponse({"ok": True, **info})


async def _rebuild_ingest() -> dict:
    async with app.state.ingest_lock:
        return await _replace_ingest()


async def _stop_ingest(ingestor, task) -> None:
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if hasattr(ingestor, "stop"):
        try:
            await ingestor.stop()
        except Exception:
            log.warning("接入层清理失败", exc_info=True)


async def _replace_ingest() -> dict:
    """(重)建接入层。总开关关掉时建出来的是 OffIngestor，不连任何东西。"""
    ingestor = build_ingestor()
    await _stop_ingest(app.state.ingestor, app.state.ingest_task)
    app.state.ingestor = ingestor
    app.state.brain.ingestor = ingestor      # 重建后统计要指向新接入层

    async def emit(ev: Event) -> None:
        hub.feed(ev)

    app.state.ingest_task = asyncio.create_task(ingestor.run(emit), name=f"ingest:{ingestor.name}")
    log.info("接入层已重建：%s（房间号 %s）", ingestor.name,
             getattr(ingestor, "room", "-"))
    return {"ingest": ingestor.name, "room": getattr(ingestor, "room", "")}


@app.get("/api/health")
async def health():
    tasks = {"brain": app.state.brain_task, "ingest": app.state.ingest_task}
    running = {name: not task.done() for name, task in tasks.items()}
    healthy = all(running.values())
    return JSONResponse({"ok": healthy, "tasks": running, "ts": time.time()},
                        status_code=200 if healthy else 503)


# ------------------------------------------------------------------ 内部投喂通道
@app.websocket("/ws-feed")
async def ws_feed(ws: WebSocket):
    """给独立桥接服务（app/bridge_server.py）用的内部通道：把外部弹幕灌进大脑。"""
    await ws.accept()
    log.info("桥接服务已接入")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            from .ingest.bridge import normalize

            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                ev = normalize(item)
                if ev:
                    hub.feed(ev)
    except WebSocketDisconnect:
        pass
    finally:
        log.info("桥接服务已断开")


# 静态资源（务必放在路由之后挂载）
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
