"""点歌队列：观众点歌、礼物优先、AI 自主点歌。

优先权设计（谁先播）：
  1. 礼物点歌永远插队到最前面，并且按礼物价值排序——刷得多的先放
  2. 普通点歌按先来后到
  3. AI 自主点的歌放队尾，不跟观众抢

为什么用"分数"而不是简单插队：
  大额礼物如果只是"插到第 1 位"，两个大哥会互相顶掉。
  用 gift_value 做排序键，价值高的自然排在前面，且同一个人连刷不会反复插队。
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .music_policy import playback_reason

MAX_QUEUE = 30
MAX_PER_USER = 3          # 同一个人最多排 3 首，防止刷屏


@dataclass
class Track:
    id: str
    title: str
    by: str
    url: str
    provider: str = ""
    duration: int = 0
    kind: str = "remote"
    requested_by: str = ""
    gift_value: int = 0
    priority: int = 0
    from_ai: bool = False
    policy_metadata: dict = field(default_factory=dict)
    added_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "by": self.by,
            "url": self.url,
            "provider": self.provider,
            "duration": self.duration,
            "kind": self.kind,
            "requestedBy": self.requested_by,
            "giftValue": self.gift_value,
            "fromAi": self.from_ai,
        }


class Queue:
    """播放队列。礼物点歌自动获得更高优先级。"""

    def __init__(self) -> None:
        self._items: list[Track] = []
        self.current: Track | None = None
        self.playing: bool = False
        self.paused: bool = False
        self._paused_elapsed: float = 0.0
        self.started_at: float = 0.0
        self.history: deque[dict] = deque(maxlen=30)

    # ------------------------------------------------------------ 查询
    def pending(self) -> list[Track]:
        return list(self._items)

    def snapshot(self) -> dict:
        return {
            "playing": self.playing,
            "current": self.current.to_dict() if self.current else None,
            "queue": [t.to_dict() for t in self._items],
            "elapsed": round(max(0, time.time() - self.started_at), 1) if self.playing else round(self._paused_elapsed, 1),
            "history": list(self.history)[-8:],
        }

    def count_by(self, user: str) -> int:
        return sum(1 for t in self._items if t.requested_by == user) + (
            1 if self.current and self.current.requested_by == user else 0
        )

    # ------------------------------------------------------------ 入队
    def add(self, track: Track) -> tuple[bool, str]:
        """返回 (是否成功, 失败原因)。"""
        reason = self.rejection(track)
        if reason:
            return False, reason
        if len(self._items) >= MAX_QUEUE:
            return False, "点歌队列满了，等会儿再点"
        if track.requested_by and not track.from_ai:
            if self.count_by(track.requested_by) >= MAX_PER_USER:
                return False, f"{track.requested_by} 你已经点了 {MAX_PER_USER} 首，先听会儿吧"

        # 礼物点歌：按价值决定插入位置；普通点歌排队尾
        if track.gift_value > 0:
            idx = 0
            for i, t in enumerate(self._items):
                if t.gift_value >= track.gift_value:
                    idx = i + 1
                else:
                    break
            self._items.insert(idx, track)
        else:
            self._items.append(track)
        return True, ""

    # ------------------------------------------------------------ 切歌
    def next_track(self) -> Track | None:
        self.enforce_policy()
        self.paused = False
        self._paused_elapsed = 0.0
        if self.current:
            self.history.append({
                "title": self.current.title,
                "by": self.current.by,
                "requestedBy": self.current.requested_by,
                "at": time.time(),
            })
        self.current = self._items.pop(0) if self._items else None
        self.started_at = time.time()
        self.playing = bool(self.current)
        return self.current

    def remove(self, track_id: str) -> bool:
        for i, t in enumerate(self._items):
            if t.id == track_id:
                self._items.pop(i)
                return True
        return False

    def clear(self) -> None:
        self._items.clear()

    def stop(self) -> None:
        self.current = None
        self.playing = False
        self.started_at = 0.0
        self.paused = False
        self._paused_elapsed = 0.0

    def pause(self) -> None:
        if self.playing:
            self._paused_elapsed = max(0.0, time.time() - self.started_at)
        self.playing = False
        self.paused = True

    def resume(self) -> Track | None:
        self.enforce_policy()
        if self.current:
            if not self.playing:
                self.started_at = time.time() - self._paused_elapsed
            self.playing = True
            self.paused = False
            return self.current
        return self.next_track()

    @staticmethod
    def rejection(track: Track) -> str:
        return playback_reason({**track.policy_metadata, "title": track.title, "by": track.by}, track.from_ai)

    def enforce_policy(self) -> bool:
        retained = [track for track in self._items if not self.rejection(track)]
        changed = len(retained) != len(self._items)
        self._items = retained
        if self.current and self.rejection(self.current):
            was_paused = self.paused
            self.stop()
            self.paused = was_paused
            changed = True
        return changed

    # ------------------------------------------------------------ 构造
    @staticmethod
    def make(info: dict, *, user: str = "", gift_value: int = 0,
             from_ai: bool = False) -> Track:
        return Track(
            id=uuid.uuid4().hex[:10],
            title=info.get("title") or "",
            by=info.get("by") or "",
            url=info.get("url") or "",
            provider=info.get("provider") or "",
            duration=int(info.get("durationSec") or info.get("duration") or 0),
            kind=info.get("kind") or "remote",
            requested_by=user,
            gift_value=gift_value,
            priority=gift_value,
            from_ai=from_ai,
            policy_metadata={key: info[key] for key in ("album", "genre", "category", "tags", "aliases", "file", "request_query") if key in info},
        )


queue = Queue()


def make_track(info: dict, *, user: str = "", gift_value: int = 0,
               from_ai: bool = False) -> Track:
    """构造曲目。做成模块级函数，避免 queue.Queue.make 这种实例/类混用。"""
    return Queue.make(info, user=user, gift_value=gift_value, from_ai=from_ai)
