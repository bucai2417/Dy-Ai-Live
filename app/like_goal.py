from __future__ import annotations

import random
import datetime as dt
import time
import uuid
from collections import deque


STUNTS = {
    "swim": {"label": "深蓝环游 · 流光泡泡", "duration": 16.0},
    "bicycle": {"label": "鱼鱼骑车巡游", "duration": 8.0},
    "web_swing": {"label": "蛛丝空中摆荡", "duration": 9.0},
    "rocket": {"label": "鱼鱼火箭升空", "duration": 7.5},
}


class LikeGoal:
    def __init__(self):
        self.total = 0
        self.step = 100
        self.next_target = 100
        self.enabled = True
        self.mode = "cycle"
        self.cooldown = 5.0
        self.index = 0
        self.completed = 0
        self.pending = deque()
        self.active = None
        self.started = 0.0
        self.ready_at = 0.0
        self.paused_at = None
        self.source_totals = {}
        self.day = dt.date.today().isoformat()
        self._day_changed = False

    def roll_day(self):
        today = dt.date.today().isoformat()
        if today != self.day:
            self.day = today
            self._day_changed = True
            self.total = self.completed = self.index = 0
            self.next_target = self.step
            self.pending.clear()
            self.active = None

    def configure(self, config, interactions=True):
        enabled = bool(config.get("enabled", True)) and interactions
        step = int(config.get("step", 100))
        if self.step != step or enabled != self.enabled:
            self.step = step
            self.next_target = self.total + step
            self.pending.clear()
        self.enabled = enabled
        self.mode = config.get("mode", "cycle")
        self.cooldown = float(config.get("cooldown", 5))
        if not enabled:
            self.pending = deque(item for item in self.pending if item["manual"])
            if self.active and not self.active["manual"]:
                self.active = None

    def pause(self, paused, now=None):
        now = time.monotonic() if now is None else now
        if paused and self.paused_at is None:
            self.paused_at = now
        elif not paused and self.paused_at is not None:
            delta = now - self.paused_at
            self.started += delta
            self.ready_at += delta
            self.paused_at = None

    def add(self, event):
        self.roll_day()
        count = event.like_count if event.like_count is not None else event.gift_count
        count = max(0, int(count or 0))
        total = event.like_total
        if total is not None:
            source = event.like_source or "default"
            previous = self.source_totals.get(source)
            self.source_totals[source] = max(previous or 0, int(total), 0)
            if previous is not None:
                count = max(0, int(total) - previous) if total >= previous else 0
        self.total += count
        if not self.enabled or self.paused_at is not None:
            self.next_target += count
            return
        if self.total < self.next_target:
            return
        crossed = (self.total - self.next_target) // self.step + 1
        self.next_target += crossed * self.step
        self.completed += crossed
        room = max(0, 3 - len(self.pending))
        for _ in range(min(crossed, room)):
            self.pending.append({"motion": self._pick(), "milestones": 1, "manual": False})
        extra = crossed - min(crossed, room)
        if extra and self.pending:
            self.pending[-1]["milestones"] += extra

    def _pick(self):
        names = tuple(STUNTS)
        if self.mode in STUNTS:
            return self.mode
        if self.mode == "random":
            choices = [name for name in names if name != getattr(self, "last_motion", "")]
            motion = random.choice(choices)
        else:
            motion = names[self.index % len(names)]
            self.index += 1
        self.last_motion = motion
        return motion

    def preview(self, motion):
        if motion not in STUNTS or self.paused_at is not None or len(self.pending) >= 3:
            return False
        self.pending.append({"motion": motion, "milestones": 0, "manual": True})
        return True

    def tick(self, now=None):
        self.roll_day()
        changed = self._day_changed
        self._day_changed = False
        now = time.monotonic() if now is None else now
        if self.paused_at is not None:
            return changed
        if self.active and now - self.started >= self.active["duration"]:
            self.active = None
            self.ready_at = now + self.cooldown
            changed = True
        if self.active is None and self.pending and now >= self.ready_at:
            item = self.pending.popleft()
            self.active = {**STUNTS[item["motion"]], **item, "id": uuid.uuid4().hex[:12]}
            self.started = now
            changed = True
        return changed

    def snapshot(self, now=None):
        self.roll_day()
        now = time.monotonic() if now is None else now
        active = None
        if self.active:
            clock = self.paused_at if self.paused_at is not None else now
            active = {**self.active, "elapsed": max(0, min(self.active["duration"], clock - self.started))}
        return {"total": self.total, "step": self.step, "target": self.next_target,
                "remaining": max(0, self.next_target - self.total), "enabled": self.enabled,
                "pending": len(self.pending), "completed": self.completed, "active": active, "paused": self.paused_at is not None}
