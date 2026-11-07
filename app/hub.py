"""进程内事件总线：接入层 → 大脑 → WebSocket 广播（直播画面/控制台）。"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections import deque
from typing import Any
from .tts.playback import PlaybackReceipts
from .performance import PerformanceState

log = logging.getLogger(__name__)


class Hub:
    """极简 pub/sub：一个队列给大脑消费，一组 WebSocket 连接收广播。"""

    def __init__(self, history: int = 80, send_timeout: float = 1.0) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2000)
        self.clients: set[Any] = set()
        self.playback = PlaybackReceipts()
        self.performance = PerformanceState()
        self.recent: deque[dict] = deque(maxlen=history)
        self.send_timeout = send_timeout
        self._broadcast_lock = asyncio.Lock()
        self.day = dt.date.today().isoformat()
        self.stats = {
            "danmu_total": 0,
            "gift_total": 0,
            "enter_total": 0,
            "replies": 0,
            "online": 0,
            "started_at": 0.0,
        }

    # ---- 入站 ----
    def feed(self, event) -> None:
        """非阻塞投递；队列满时丢弃最旧的，保证直播不卡住。"""
        self.roll_day()
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                self.queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                return
        kind = getattr(event, "kind", "")
        key = {"chat": "danmu_total", "gift": "gift_total", "enter": "enter_total"}.get(kind)
        if key:
            self.stats[key] += 1

    # ---- 广播 ----
    def roll_day(self):
        today = dt.date.today().isoformat()
        if today != self.day:
            self.day = today
            for key in ("danmu_total", "gift_total", "enter_total", "replies"):
                self.stats[key] = 0

    async def broadcast(self, payload: dict, remember: bool = True) -> None:
        async def send(ws):
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=self.send_timeout)
            except Exception as exc:  # 客户端断开
                log.debug("broadcast drop: %s", exc)
                self.clients.discard(ws)
                self.playback.remove(ws)
                try:
                    await asyncio.wait_for(ws.close(code=1013), timeout=self.send_timeout)
                except Exception:
                    pass

        async with self._broadcast_lock:
            if payload.get("type") in PerformanceState.TYPES:
                from .settings_store import Effective
                self.performance.observe(payload, {"interact": Effective.interact(), "stage": Effective.stage()})
                payload = {**payload, "performance": self.performance.snapshot()}
            if remember and payload.get("type") in {"event", "answered", "subtitle"}:
                self.recent.append(payload)
            await asyncio.gather(*(send(ws) for ws in list(self.clients)))
            self.stats["online"] = len(self.clients)


hub = Hub()
