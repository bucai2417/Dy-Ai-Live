from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .fishing_board import FishingBoard
from .like_goal import MAX_RECENT_LIKES


log = logging.getLogger(__name__)
COUNTERS = ("tokens_in", "tokens_out", "llm_calls", "offline_calls", "replies", "gifts", "gift_value")
EVENT_COUNTERS = ("danmu_total", "gift_total", "enter_total", "replies")


class LiveHistory:
    def __init__(self, path, metrics, hub, likes):
        self.path = Path(path)
        self.metrics, self.hub, self.likes = metrics, hub, likes
        self.previous = None
        self.restore_error = False
        self.status = {"savedAt": "", "error": ""}
        metrics.storage = self.status

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS days (day TEXT PRIMARY KEY, payload TEXT NOT NULL, saved_at TEXT NOT NULL)")
                yield connection
        finally:
            connection.close()

    def restore(self):
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT payload, saved_at FROM days WHERE day = ?", (dt.date.today().isoformat(),)).fetchone()
            if row is None:
                return False
            data = json.loads(row[0])
            counts = {key: self._count(data["metrics"].get(key, 0)) for key in COUNTERS}
            events = {key: self._count(data.get("events", {}).get(key, 0)) for key in EVENT_COUNTERS}
            gifts = data.get("gift_board", {})
            if not isinstance(gifts, dict):
                raise ValueError("礼物榜数据格式错误")
            records = {}
            for key, gift in gifts.items():
                if not isinstance(gift, dict) or not isinstance(gift.get("name"), str):
                    raise ValueError("礼物榜记录错误")
                records[key] = {**gift, **{field: self._count(gift.get(field, 0)) for field in ("count", "value", "order")}}
            like_state = data.get("likes", {})
            restored_likes = {key: self._count(like_state.get(key, 0)) for key in ("total", "completed", "index")}
            source_totals = {str(key): self._count(value) for key, value in like_state.get("source_totals", {}).items()}
            event_ids = like_state.get("event_ids", [])
            if not isinstance(event_ids, list) or any(not isinstance(value, str) or not value for value in event_ids):
                raise ValueError("点赞消息记录格式错误")
            target = self._count(like_state.get("next_target", self.likes.step))
            fishing_board = FishingBoard.restore(data.get("fishing_board", {}))
            for key, value in counts.items():
                setattr(self.metrics, key, value)
            self.metrics.estimated = bool(data["metrics"].get("estimated"))
            self.metrics.gift_board.records = records
            self.metrics.fishing_board = fishing_board
            self.hub.stats.update(events)
            for key, value in restored_likes.items():
                setattr(self.likes, key, value)
            self.likes.source_totals = source_totals
            self.likes.event_ids = dict.fromkeys(event_ids[-MAX_RECENT_LIKES:])
            self.likes.next_target = max(self.likes.total + 1, target) if like_state.get("step") == self.likes.step else self.likes.total + self.likes.step
            self.status.update(savedAt=row[1], error="")
            return True
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            self.restore_error = True
            self.status["error"] = "今日数据读取失败：" + str(exc)
            log.exception("今日直播数据恢复失败，保留原文件")
            return False

    @staticmethod
    def _count(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("无效的统计计数")
        return value

    def capture(self):
        self.metrics._roll()
        self.hub.roll_day()
        self.likes.roll_day()
        return self.metrics.day, copy.deepcopy({
            "metrics": {key: getattr(self.metrics, key) for key in (*COUNTERS, "estimated")},
            "gift_board": self.metrics.gift_board.records,
            "fishing_board": self.metrics.fishing_board.export(),
            "events": {key: self.hub.stats[key] for key in EVENT_COUNTERS},
            "likes": {**{key: getattr(self.likes, key) for key in ("total", "step", "next_target", "completed", "index", "source_totals")},
                      "event_ids": list(self.likes.event_ids)},
        })

    def write(self, day, data):
        if self.restore_error:
            return
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if self.previous == (day, payload):
            return
        saved = dt.datetime.now().isoformat(timespec="seconds")
        try:
            with self._connect() as connection:
                connection.execute("INSERT INTO days VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET payload=excluded.payload, saved_at=excluded.saved_at", (day, payload, saved))
            self.previous = (day, payload)
            self.status.update(savedAt=saved, error="")
        except (OSError, sqlite3.Error) as exc:
            self.status["error"] = "统计保存失败：" + str(exc)
            log.exception("直播统计保存失败，将在下次重试")

    async def run(self, stop):
        while not stop.is_set():
            await asyncio.to_thread(self.write, *self.capture())
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
        await asyncio.to_thread(self.write, *self.capture())
