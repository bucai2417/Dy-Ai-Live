"""抖音弹幕直连（不开浏览器）。

和 douyin_cdp.py 的区别只在「帧从哪来」：
    CDP  方案：浏览器打开直播间 → CDP 抓 WebSocket 帧 → 解析
    直连 方案：自己连 WSS                → 收帧         → 解析

解析层完全复用 douyin_cdp 里的 protobuf 工具（gunzip_any / iter_envelopes /
chat_of / _first_user），所以这里只负责：
    1. 拿 ttwid cookie
    2. 用 web_rid 换内部 roomId，并判断开没开播
    3. 调签名子进程拿 X-Bogus
    4. 连 WSS、心跳、收帧 → 交给同一个解析函数
    5. 下播自动等、开播自动连、断线自动重连

签名子进程依赖使用者自行提供的外部 webmssdk.js，公开源码不附带。
缺少签名入口、SDK 或 Node.js 时，上层会退回 CDP 采集方案。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import httpx
import websockets

from ..config import ROOT
from ..events import Event
from ..settings_store import Effective
from .douyin_cdp import (
    DouyinCDPIngestor,
    _gift_value,
    iter_envelopes,
    chat_of,
    normalize_text,
    gunzip_any,
)

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

ROOM_API = "https://live.douyin.com/webcast/room/web/enter/"
SIGN_DIR = ROOT / "tools" / "douyin_sig"
SIGN_FILE = SIGN_DIR / "sign_server.mjs"
SIGN_SDK = SIGN_DIR / "webmssdk.js"

# 抖音的签名版本号，抖音改版时要跟着调
VERSION_CODE = 180800
SDK_VERSION = "1.0.15"
WS_HOST = "webcast100-ws-web-hl.douyin.com"


# ---------------------------------------------------------------- 签名子进程
class Signer:
    """一行 JSON 一问一答地调用 node 签名子进程。

    用子进程而不是把算法翻成 Python：那 575KB 是混淆字节码，
    逆向既费时又必然随抖音更新失效，直接复用上游实现更现实。
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = asyncio.Lock()
        self._seq = 0
        self._dead = False

    @property
    def available(self) -> bool:
        return SIGN_FILE.is_file() and SIGN_SDK.is_file() and shutil.which("node") is not None and not self._dead

    def _start(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        if not self.available:
            log.error("签名组件不完整或 Node.js 不可用，请使用 bridge / douyin 接入，详见 tools/douyin_sig/README.md")
            self._dead = True
            return False
        try:
            self._proc = subprocess.Popen(
                ["node", str(SIGN_FILE)],
                cwd=str(SIGN_DIR),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log.info("签名子进程已启动 pid=%s", self._proc.pid)
            return True
        except FileNotFoundError:
            log.error("没找到 node，直连模式需要 Node.js 才能算抖音签名")
            self._dead = True
            return False
        except Exception:
            log.exception("签名子进程启动失败")
            self._dead = True
            return False

    async def sign(self, params: dict) -> str:
        """返回 X-Bogus 签名；失败返回空串。"""
        if not self.available:
            return ""
        async with self._lock:
            if not self._start():
                return ""
            self._seq += 1
            req_id = self._seq
            line = json.dumps({"id": req_id, "op": "sign", "params": params},
                              ensure_ascii=False)
            try:
                proc = self._proc
                assert proc and proc.stdin and proc.stdout
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
                # 读一行响应；阻塞读放到线程里，别卡住事件循环
                raw = await asyncio.wait_for(
                    asyncio.to_thread(proc.stdout.readline), timeout=8,
                )
                if not raw:
                    raise RuntimeError("签名子进程无响应")
                resp = json.loads(raw)
                if not resp.get("ok"):
                    raise RuntimeError(resp.get("error") or "签名失败")
                return str(resp.get("signature") or "")
            except Exception as exc:
                log.warning("取签名失败：%s", exc)
                self.close()
                return ""

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.stdin and proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


signer = Signer()


# ---------------------------------------------------------------- 房间信息
async def resolve_room(web_rid: str, client: httpx.AsyncClient) -> dict:
    """用 web_rid 换内部 roomId 和开播状态。

    抖音进不去房间时会回 HTTP 200，但 data.status_code 非 0，
    并在 data.prompts 里给一句中文原因。这些原因很有用：
        4003034  你不在主播设置的可见范围内，无法进入TA的直播间
        其他     房间号不存在 / 已下播 / 需要登录 …
    不把它带出来，界面上只会显示"主播没在播"，用户根本分不清
    是号码写错、房间设了限制、还是真的没开播。
    """
    params = {
        "aid": "6383", "app_name": "douyin_web", "live_id": "1",
        "device_platform": "web", "language": "zh-CN",
        "enter_from": "web_live", "cookie_enabled": "true",
        "screen_width": "1920", "screen_height": "1080",
        "browser_language": "zh-CN", "browser_platform": "Win32",
        "browser_name": "Chrome", "browser_version": "134.0.0.0",
        "web_rid": web_rid,
    }
    r = await client.get(ROOM_API, params=params,
                         headers={"User-Agent": UA,
                                  "Referer": f"https://live.douyin.com/{web_rid}"})
    if r.status_code != 200:
        raise RuntimeError(f"房间接口 HTTP {r.status_code}")
    d = r.json()
    data = d.get("data") or {}
    code = data.get("status_code") or 0
    note = str(data.get("prompts") or "").strip()
    rooms = (data.get("data") or [])
    if not rooms:
        return {"roomId": "", "live": False, "title": "",
                "code": code, "note": note or "房间号不存在或已注销"}
    room = rooms[0]
    return {
        "roomId": str(room.get("id_str") or ""),
        # status: 2=直播中，4=已结束
        "live": room.get("status") == 2,
        "title": str(room.get("title") or ""),
        "code": code,
        "note": note,
    }


def build_ws_url(room_id: str, signature: str, user_uid: str) -> str:
    """拼弹幕 WSS 地址。

    参数必须和签名时那套完全对齐，少一个（尤其 live_id/did_rule/identity/
    endpoint/support_wrds）服务端就会回 HTTP 200 把连接挡掉——
    不是 401，很有迷惑性，看起来像"房间没开播"。
    """
    q = {
        "app_name": "douyin_web",
        "room_id": room_id,
        "compress": "gzip",
        "version_code": str(VERSION_CODE),
        "webcast_sdk_version": SDK_VERSION,
        "update_version_code": SDK_VERSION,
        "live_id": "1",
        "did_rule": "3",
        "user_unique_id": user_uid,
        "identity": "audience",
        "signature": signature,
        "device_platform": "web",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "134.0.0.0",
        "browser_online": "true",
        "tz_name": "Asia/Shanghai",
        "aid": "6383",
        "endpoint": "live_pc",
        "support_wrds": "1",
        "im_path": "/webcast/im/fetch/",
        "need_persist_msg_count": "15",
        "heartbeatDuration": "0",
    }
    qs = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in q.items())
    return f"wss://{WS_HOST}/webcast/im/push/v2/?{qs}"


def sig_params(room_id: str, user_uid: str) -> dict:
    """签名用的参数字典（顺序即拼接顺序，别乱动）。"""
    return {
        "live_id": "1",
        "aid": "6383",
        "version_code": VERSION_CODE,
        "webcast_sdk_version": SDK_VERSION,
        "room_id": room_id,
        "sub_room_id": "",
        "sub_channel_id": "",
        "did_rule": "3",
        "user_unique_id": user_uid,
        "device_platform": "web",
        "device_type": "",
        "ac": "",
        "identity": "audience",
    }


# ---------------------------------------------------------------- 接入层
class DouyinDirectIngestor(DouyinCDPIngestor):
    """继承 CDP 版，只覆盖「取帧」部分，解析逻辑全部复用。"""

    name = "douyin_direct"

    def __init__(self) -> None:
        super().__init__()
        cfg = Effective.ingest()
        self.room = str(cfg.get("douyin_room") or "")
        self._ws = None
        self._client: httpx.AsyncClient | None = None
        self._room_id = ""
        self._live = False
        self._last_state_log = ""

    def _state(self, msg: str) -> None:
        """状态变化才打日志，避免每 15 秒刷屏。"""
        if msg != self._last_state_log:
            self._last_state_log = msg
            log.info("%s", msg)

    def status(self) -> dict:
        """给后台展示的直连状态。"""
        return {
            "mode": "direct",
            "room": self.room,
            "roomId": self._room_id,
            "live": self._live,
            "state": self._last_state_log or "正在连接…",
            "signer": signer.available,
        }

    async def run(self, emit) -> None:
        self._emit = emit
        if not self.room:
            log.error("接入方式=抖音直连，但没有配置房间号")
            return
        if not signer.available:
            log.error("签名组件不可用（tools/douyin_sig），请改用 douyin 浏览器模式")
            return

        log.info("抖音直连已启用（不开浏览器），房间号 %s", self.room)
        fails = 0
        while not self._stopped:
            try:
                await self._client_once()
                fails = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                fails += 1
                wait = min(5 + fails * 3, 40)
                log.warning("直连中断（%s），%d 秒后重试", exc, wait)
                await asyncio.sleep(wait)

        signer.close()

    async def _client_once(self) -> None:
        """完整走一轮：查房间 → 连 WSS → 收到断线才算返回。"""
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     trust_env=False) as client:
            self._client = client
            # 先拿首页 cookie（ttwid 是进 WSS 的门票）
            try:
                await client.get("https://live.douyin.com/",
                                 headers={"User-Agent": UA})
            except Exception as exc:
                raise RuntimeError(f"拿 cookie 失败：{exc}") from exc

            info = await resolve_room(self.room, client)
            if not info["roomId"]:
                note = info.get("note") or "房间号可能写错了"
                code = info.get("code") or 0
                tail = f"（{code}）" if code else ""
                self._state(f"进不去直播间 {self.room}：{note}{tail}")
                self._room_id = ""
                self._live = False
                await asyncio.sleep(20)
                return
            if not info["live"]:
                note = info.get("note") or "主播没在播"
                self._state(f"{note}（{info['title'][:16] or self.room}），20 秒后再看")
                # 把状态暴露给后台，让用户知道"在播但进不去"还是"没开播"
                self._room_id = info["roomId"]
                self._live = False
                await asyncio.sleep(20)
                return

            self._room_id = info["roomId"]
            self._live = True
            self._state(f"已连上直播间：{info['title'][:20] or info['roomId']}")

            uid = str(random.randint(7300000000000000000, 7999999999999999999))
            signature = await signer.sign(sig_params(self._room_id, uid))
            if not signature:
                raise RuntimeError("签名失败")

            url = build_ws_url(self._room_id, signature, uid)
            cookies = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
            headers = {
                "Cookie": cookies,
                "User-Agent": UA,
                "Origin": "https://live.douyin.com",
                "Referer": "https://live.douyin.com/",
            }

            async with websockets.connect(
                url, additional_headers=headers, open_timeout=15,
                ping_interval=None, max_size=None,
            ) as ws:
                self._ws = ws
                log.info("弹幕长连接已建立（%s）", self._room_id)
                await self._pump(ws)

    async def _pump(self, ws) -> None:
        """持续收帧，同时每 8 秒发一次心跳保活。"""
        last_beat = time.time()
        while not self._stopped:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                if time.time() - last_beat > 8:
                    try:
                        await ws.send(b"\x3a\x02\x68\x62")   # 心跳帧
                        last_beat = time.time()
                    except Exception:
                        return
                continue

            if isinstance(raw, str):
                raw = raw.encode("utf-8", "ignore")
            try:
                self._handle_frame(raw)
            except Exception:
                log.debug("直连帧解析失败", exc_info=True)

            if time.time() - last_beat > 8:
                try:
                    await ws.send(b"\x3a\x02\x68\x62")
                    last_beat = time.time()
                except Exception:
                    return

    async def stop(self) -> None:
        """关总开关时调用：断 WSS、关子进程。"""
        self._stopped = True
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        signer.close()

    def shutdown_browser(self) -> None:
        """直连模式没有浏览器，覆盖掉父类实现以免误杀。"""
        return
