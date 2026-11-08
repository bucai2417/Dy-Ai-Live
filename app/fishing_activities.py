from __future__ import annotations

import copy
import datetime as dt
import math
import time
import uuid

from .fishing_catalog import FISH, FISH_KINDS


class FishingActivities:
    def init_activities(self):
        self.net = None
        self.net_queue = []
        self.net_return = None
        self.activity = None
        self.action_times = {}
        self.cheer_until = 0
        self.ambience_started = time.monotonic()

    def add_net_gift(self, count, value=0, user=None, event_id="", gift="礼物", now=None):
        if (not self.active or not self.config.get("dedicated") or not self.config.get("net_enabled", True)
                or not isinstance(count, (int, float)) or not math.isfinite(count) or count <= 0):
            return False
        if event_id and event_id in self.gift_seen:
            return True
        self.tick(now)
        underlying = (self.interruption or {}).get("phase", self.phase)
        if underlying == "hooked":
            return False
        user = {"id": str((user or {}).get("id") or "")[:100],
                "name": str((user or {}).get("name") or "观众")[:80]}
        key = user["id"] or user["name"]
        pending = next((entry for entry in self.net_queue if entry["key"] == key), None)
        if pending is None and len(self.net_queue) >= 32:
            return False
        known = isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        amount = min(1000000, float(value) if known else max(1, int(count)))
        if pending is None:
            pending = {"key": key, "id": uuid.uuid4().hex, "user": user, "gift": str(gift)[:80],
                       "count": 0, "value": 0, "estimated": False}
            self.net_queue.append(pending)
        pending["count"] += min(10000, int(count))
        pending["value"] = min(1000000, pending["value"] + amount)
        pending["estimated"] |= not known
        pending["user"] = user
        if event_id:
            self.gift_seen.append(event_id)
        self._start_net(self._clock(now))
        self.revision += 1
        return True

    def _start_net(self, now):
        if self.phase != "waiting" or not self.net_queue or self.paused_at is not None:
            return False
        self.net_return = {"phase": self.phase, "started": self.phase_started,
                           "deadline": self.deadline, "since": now}
        self.net = self.net_queue.pop(0)
        budget = max(1, round(self.net["value"] * self.config.get("net_multiplier", 20) * self.rng.uniform(.8, 1.4)))
        count = 1 + int(math.log10(max(1, self.net["value"])))
        count = min(5, count, budget)
        weights = [self.rng.uniform(.7, 1.3) for _ in range(count)]
        remaining = budget
        catches = []
        for index in range(count):
            points = remaining if index == count - 1 else min(remaining - (count - index - 1), max(1, round(budget * weights[index] / sum(weights))))
            remaining -= points
            candidates = sorted(FISH_KINDS, key=lambda kind: abs(math.log(max(1, FISH[kind]["value"])) - math.log(points)))[:8]
            kind = self.rng.choice(candidates)
            preset = FISH[kind]
            catches.append({"kind": kind, "name": preset["name"], "rarity": preset["rarity"],
                            "weight": round(self.rng.uniform(*preset["weight"]), 2), "value": points,
                            "day": dt.date.today().isoformat(), "category": "fish", "source": "net"})
        self.net.update(fish=catches, total=budget)
        self._phase("net_cast", now, 10)
        return True

    def finish_net(self, now):
        saved = self.net_return
        delay = now - saved["since"]
        self.phase, self.phase_started, self.deadline = saved["phase"], saved["started"] + delay, saved["deadline"] + delay
        self.net = self.net_return = None
        self.revision += 1

    def fish_action(self, operation, user=None, event_id="", now=None):
        if self.paused_at is not None:
            raise ValueError("直播已暂停或正在礼物演出")
        self.tick(now)
        now = self._clock(now)
        if event_id and event_id in self.seen:
            return False
        expected = "waiting" if operation == "feed" else "hooked"
        if self.phase != expected:
            raise ValueError("投饵需要等待咬钩时使用" if operation == "feed" else "等鱼咬钩后再松线或加油")
        if operation not in ("loosen", "feed", "cheer"):
            raise ValueError("未知钓台互动")
        if operation == "loosen" and not self.config.get("loosen_enabled", True):
            raise ValueError("主播已关闭松线互动")
        cooldown = {"loosen": 2, "feed": 12, "cheer": 10}[operation]
        if now - self.action_times.get(operation, -math.inf) < cooldown:
            raise ValueError(f"这项钓台互动冷却中，间隔 {cooldown} 秒可再次使用")
        if operation == "loosen" and self.progress <= 0:
            raise ValueError("鱼线已经放松，进度为零")
        if event_id:
            self.seen.append(event_id)
        self.action_times[operation] = now
        self.activity = {"kind": operation, "name": str((user or {}).get("name") or "鱼友")[:80], "at": now}
        if operation == "loosen":
            before = self.progress
            self.progress = max(0, before - self.config.get("loosen_amount", .08))
            self.activity["amount"] = before - self.progress
        elif operation == "cheer":
            self.cheer_until = now + 10
        else:
            self.deadline = now + max(3, (self.deadline - now) * .65)
            self.interrupt("投饵引鱼", 4.5, now)
            self.interruption["action"] = "feed"
        self.revision += 1
        return True

    def activity_snapshot(self, now):
        mode = self.config.get("day_mode", "cycle")
        period = max(120, self.config.get("day_cycle", 600))
        if mode == "realtime":
            wall = dt.datetime.now()
            phase = (wall.hour * 3600 + wall.minute * 60 + wall.second) / 86400
            speed = 1 / 86400
        elif mode in ("day", "night"):
            phase, speed = (.5 if mode == "day" else 0), 0
        else:
            phase, speed = (.5 + (now - self.ambience_started) / period) % 1, 1 / period
        return {"net": copy.deepcopy(self.net), "net_pending": len(self.net_queue),
                "net_enabled": self.config.get("net_enabled", True),
                "activity": copy.deepcopy(self.activity),
                "activity_age": max(0, now - self.activity["at"]) if self.activity else 100,
                "cheer_remaining": max(0, self.cheer_until - now),
                "loosen_enabled": self.config.get("loosen_enabled", True),
                "day_phase": phase, "day_speed": speed, "day_mode": mode}
