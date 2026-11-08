from __future__ import annotations

import copy
import random
import time
from collections import deque


SHOWS = {
    "tea": ("热茶回礼", 12), "video": ("奶蛙惊喜", 12),
    "flowers": ("花束炫耀", 11), "lollipop": ("巨型棒棒糖", 11),
    "sunglasses": ("墨镜走秀", 10), "heart": ("爱心拥抱", 10),
    "rocket": ("星际升空", 12), "crown": ("加冕时刻", 11), "sparkles": ("星光谢礼", 10),
}


def gift_effect(name, video_ready=False, rng=None):
    name = str(name).replace("鏡", "镜")
    if "啤酒" in name:
        return (rng or random).choice(("tea", "video")) if video_ready else "tea"
    if any(word in name for word in ("鲜花", "玫瑰", "花束", "花花")):
        return "flowers"
    if "棒棒糖" in name:
        return "lollipop"
    if "墨镜" in name:
        return "sunglasses"
    if any(word in name for word in ("火箭", "飞船", "飞机", "飞艇")):
        return "rocket"
    if any(word in name for word in ("皇冠", "王冠", "城堡", "嘉年华")):
        return "crown"
    if any(word in name for word in ("爱", "心", "比心")):
        return "heart"
    return "sparkles"


class GiftShow:
    def __init__(self):
        self.active = None
        self.pending = deque()
        self.seen = deque(maxlen=4096)
        self.started = 0.0
        self.paused_at = None
        self.holding = False

    def enqueue(self, event, video_ready=False, tribute=None):
        if event.id in self.seen:
            return True
        key = (event.user_id or event.user_name, event.gift_name)
        for item in self.pending:
            if (item["user"]["id"] or item["user"]["name"], item["gift"]) == key:
                item["count"] += max(1, event.gift_count)
                if tribute:
                    item["tribute"]["daily_count"] = tribute["daily_count"]
                if event.avatar:
                    item["user"]["avatar"] = event.avatar
                self.seen.append(event.id)
                return True
        if len(self.pending) >= 100:
            return False
        effect = gift_effect(event.gift_name, video_ready)
        label, duration = SHOWS[effect]
        self.pending.append({"id": event.id, "effect": effect, "label": label, "duration": duration,
                             "gift": event.gift_name or "礼物", "count": max(1, event.gift_count),
                             "user": {"id": event.user_id, "name": event.user_name, "avatar": event.avatar},
                             "tribute": copy.deepcopy(tribute or {})})
        self.seen.append(event.id)
        return True

    def tick(self, now=None, hold=False):
        if self.paused_at is not None:
            return False
        now = time.monotonic() if now is None else now
        changed = False
        holding = bool(self.active and hold and self.active["duration"] - 1.2 <= now - self.started < 30)
        if holding != self.holding:
            self.holding = holding
            changed = True
        if self.active and now - self.started >= self.active["duration"] and not self.holding:
            self.active = None
            changed = True
        if self.active is None and self.pending:
            self.active = self.pending.popleft()
            self.started = now
            changed = True
        return changed

    def clear(self):
        self.active = None
        self.pending.clear()
        self.holding = False

    def pause(self, paused, now=None):
        now = time.monotonic() if now is None else now
        if paused and self.paused_at is None:
            self.paused_at = now
        elif not paused and self.paused_at is not None:
            self.started += now - self.paused_at
            self.paused_at = None

    def snapshot(self, now=None):
        now = self.paused_at if self.paused_at is not None else time.monotonic() if now is None else now
        elapsed = max(0, now - self.started) if self.active else 0
        if self.holding and self.active:
            elapsed = min(elapsed, self.active["duration"] - 1.2)
        return {"active": copy.deepcopy(self.active), "elapsed": elapsed, "holding": self.holding,
                "pending": len(self.pending), "paused": self.paused_at is not None}
