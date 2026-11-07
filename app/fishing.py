from __future__ import annotations

import copy
import datetime as dt
import math
import random
import time
import uuid
from collections import deque


from .fishing_catalog import FISH, FISH_KINDS, ITEM_KINDS

PHASES = {"idle": "未在钓鱼", "waiting": "坐在高台持竿等待咬钩", "hooked": "鱼儿咬钩，等待大家协力收线",
          "landing": "正在把鱼拉出水面", "caught": "展示捕获结果并庆祝", "escaped": "鱼儿脱钩，本轮结束",
          "interacting": "暂时放下鱼竿互动，结束后继续本轮", "diving": "离开鱼竿跳水游泳，回台后重新抛竿"}


class FishingGame:
    def __init__(self, config, rng=None):
        self.config = dict(config)
        self.rng = rng or random.Random()
        self.phase = "idle"
        self.paused_at = None
        self.revision = 0
        self.round_id = ""
        self.started = self.phase_started = 0.0
        self.deadline = 0.0
        self.progress = 0.0
        self.last_pull = None
        self.winner = None
        self.contributors = {}
        self.fish = None
        self.school = []
        self.reason = ""
        self.return_scene = ""
        self.seen = deque(maxlen=512)
        self.gift_seen = deque(maxlen=2048)
        self.gift_bank = {}
        self.interruption = None
        self.dive = None
        self.last_gift = None

    @property
    def active(self):
        return self.phase != "idle"

    def _clock(self, now=None):
        return self.paused_at if self.paused_at is not None else time.monotonic() if now is None else now

    def start(self, return_scene, now=None):
        if not self.config.get("enabled", True):
            raise ValueError("钓鱼玩法暂时关闭了")
        if self.paused_at is not None:
            raise ValueError("直播已暂停，请先恢复直播")
        if self.active:
            raise ValueError("这一竿还没结束，咬钩后发“拉”或“拉钩”一起收线")
        now = self._clock(now)
        self.round_id = uuid.uuid4().hex[:12]
        self.started = now
        self.return_scene = return_scene
        self.progress = 0.0
        self.fish = self.winner = self.last_pull = None
        self.contributors.clear()
        self.reason = ""
        self.interruption = self.dive = self.last_gift = None
        self.seen.clear()
        kinds = self.rng.choices(FISH_KINDS, weights=[FISH[kind]["chance"] for kind in FISH_KINDS], k=12)
        self.school = [{"kind": kind, "phase": self.rng.random(), "depth": .12 + self.rng.random() * .75,
                        "speed": self.rng.uniform(.035, .075), "direction": self.rng.choice((-1, 1))} for kind in kinds]
        self._phase("waiting", now, self._duration("bite", 12, 28))

    def _duration(self, prefix, low, high):
        minimum = max(1, float(self.config.get(prefix + "_min", low)))
        maximum = max(minimum, float(self.config.get(prefix + "_max", high)))
        return self.rng.uniform(minimum, maximum)

    def interrupt(self, label, duration, now=None):
        if not self.active or self.phase == "diving" or self.paused_at is not None:
            raise ValueError("请等跳水或礼物演出完成后再互动")
        now = self._clock(now)
        if self.phase != "interacting":
            self.interruption = {"phase": self.phase, "started": self.phase_started,
                                 "deadline": self.deadline, "since": now, "label": label, "world": label == "场景互动"}
            self._phase("interacting", now, duration)
        else:
            self.deadline = max(self.deadline, now + duration)
            self.interruption["label"] = label
            self.interruption["world"] |= label == "场景互动"
            self.revision += 1

    def resume(self, now=None):
        if self.phase != "interacting":
            return False
        now = self._clock(now)
        saved = self.interruption
        delay = now - saved["since"]
        self.phase, self.phase_started, self.deadline = saved["phase"], saved["started"] + delay, saved["deadline"] + delay
        if self.last_pull is not None:
            self.last_pull += delay
        self.interruption = None
        self.revision += 1
        self._apply_gifts(now)
        return True

    def start_dive(self, now=None):
        if not self.active or self.paused_at is not None or self.phase == "diving":
            raise ValueError("请等当前演出完成后再跳水")
        now = self._clock(now)
        self.interruption = None
        self.fish = self.winner = self.last_pull = None
        self.progress = 0
        self.contributors.clear()
        self.dive = {"style": self.rng.choice(("tuck", "pike", "twist", "cannonball")),
                     "splash": self.rng.uniform(.65, 1.5), "splash_kind": self.rng.choice(("crown", "fan", "jet")),
                     "laps": self.rng.choice((2, 3, 4))}
        self._phase("diving", now, 24)

    def _phase(self, name, now, duration=0):
        self.phase, self.phase_started, self.deadline = name, now, now + duration
        self.revision += 1

    def tick(self, now=None):
        if not self.active or self.paused_at is not None:
            return False
        now = self._clock(now)
        if now < self.deadline:
            return False
        if self.phase == "waiting":
            slot = self.rng.randrange(len(self.school))
            pool = ITEM_KINDS if self.rng.random() < self.config.get("item_chance", .18) else FISH_KINDS
            kind = self.rng.choices(pool, weights=[FISH[name]["chance"] for name in pool])[0]
            preset = FISH[kind]
            category = preset.get("category", "fish")
            if category == "fish":
                self.school[slot]["kind"] = kind
            else:
                slot = -1
            weight = round(self.rng.uniform(*preset["weight"]), 2)
            value = round(preset["value"] * weight / sum(preset["weight"]) * 2)
            self.fish = {"kind": kind, "slot": slot, "name": preset["name"], "rarity": preset["rarity"],
                         "weight": weight, "value": value, "pulls": preset["pulls"], "day": dt.date.today().isoformat(),
                         "category": category, "description": preset.get("description", "")}
            self._phase("hooked", now, self._duration("fight", 25, 45))
            self._apply_gifts(now)
        elif self.phase == "interacting":
            return self.resume(now)
        elif self.phase == "diving":
            self._phase("idle", now)
            self.start(self.return_scene, now)
        elif self.phase == "hooked":
            self.reason = "等得太久，物品滑落了" if self.fish.get("category") == "item" else "等得太久，鱼儿游走了"
            self._phase("escaped", now, 4)
        elif self.phase == "landing":
            self._phase("caught", now, 6)
        else:
            self._phase("idle", now)
        return True

    def pull(self, units=1, user=None, event_id="", now=None, likes=0, gifts=0):
        if self.paused_at is not None and not gifts:
            raise ValueError("直播已暂停，请先恢复直播")
        self.tick(now)
        if not self.active:
            raise ValueError("先说“去钓鱼”开启一局")
        if self.phase == "waiting":
            raise ValueError("还没咬钩，等浮漂亮起后再拉钩")
        if self.phase != "hooked":
            raise ValueError("这一竿已经结束，等待结果展示完成")
        if event_id and event_id in self.seen:
            return False
        if not isinstance(units, (int, float)) or not math.isfinite(units) or units <= 0:
            return False
        if event_id:
            self.seen.append(event_id)
        now = self._clock(now)
        self._credit(user, units, likes, gifts)
        units = min(units, (1 - self.progress) * self.fish["pulls"])
        self.last_pull = now
        if gifts:
            self.last_gift = {"name": (user or self.winner or {}).get("name", "观众"), "count": gifts, "at": now}
        risk = float(self.config.get("escape_chance", .06))
        escape = risk * .25 if gifts else 1 - (1 - risk) ** units
        if self.rng.random() < escape:
            self.reason = "物品没挂稳，又掉回水里了" if self.fish.get("category") == "item" else "鱼儿奋力挣脱，脱钩了"
            self._phase("escaped", now, 4)
            return True
        self.progress = min(1, self.progress + units / self.fish["pulls"])
        self.revision += 1
        if self.progress >= 1 - 1e-8:
            self.progress = 1
            self._phase("landing", now, 1.6)
        return True

    def _credit(self, user, units, likes=0, gifts=0):
        if user:
            identifier = str(user.get("id") or "")[:100]
            name = str(user.get("name") or "观众")[:80]
            key = identifier or (name if name != "观众" else "")
            if key:
                entry = self.contributors.setdefault(key, {"id": identifier, "name": name,
                                                           "pulls": 0, "likes": 0, "gifts": 0, "score": 0.0})
                entry["name"] = name
                entry["pulls"] += 0 if likes or gifts else 1
                entry["likes"] += likes
                entry["gifts"] += gifts
                entry["score"] += units
                leader = max(self.contributors.values(), key=lambda item: item["score"])
                self.winner = {"id": leader["id"], "name": leader["name"]}

    def add_gift(self, count, user=None, event_id="", now=None):
        if not self.active or count <= 0 or (event_id and event_id in self.gift_seen):
            return False
        self.tick(now)
        if event_id:
            self.gift_seen.append(event_id)
        user = user or {}
        key = user.get("id") or user.get("name") or "anonymous"
        entry = self.gift_bank.setdefault(key, {"user": dict(user), "count": 0})
        entry["count"] += min(10000, int(count))
        self._apply_gifts(self._clock(now))
        self.revision += 1
        return True

    def _apply_gifts(self, now):
        if self.phase == "hooked":
            self.tick(now)
        if self.phase != "hooked" or not self.gift_bank:
            return
        pending, self.gift_bank = self.gift_bank, {}
        total = sum(entry["count"] for entry in pending.values())
        strength = max(2, self.config.get("gift_pulls", 5))
        for entry in pending.values():
            self._credit(entry["user"], entry["count"] * strength, gifts=entry["count"])
        self.pull(total * strength, now=now, gifts=total)
        donor = max(pending.values(), key=lambda entry: entry["count"])["user"]
        self.last_gift["name"] = str(donor.get("name") or "观众")[:80]

    def add_likes(self, count, event_id="", now=None, user=None):
        if self.paused_at is not None or not self.active:
            return False
        self.tick(now)
        if self.phase != "hooked":
            return False
        return self.pull(max(0, count) / max(1, self.config.get("likes_per_pull", 10)),
                         user=user, event_id=event_id, now=now, likes=max(0, count))

    def cancel(self, now=None):
        if self.active:
            self._phase("idle", self._clock(now))
        self.interruption = self.dive = None
        self.gift_bank.clear()

    def pause(self, paused, now=None):
        now = time.monotonic() if now is None else now
        if paused and self.paused_at is None:
            self.paused_at = now
        elif not paused and self.paused_at is not None:
            delay = now - self.paused_at
            self.started += delay
            self.phase_started += delay
            self.deadline += delay
            if self.last_pull is not None:
                self.last_pull += delay
            if self.last_gift:
                self.last_gift["at"] += delay
            if self.interruption:
                for key in ("started", "deadline", "since"):
                    self.interruption[key] += delay
            self.paused_at = None

    def snapshot(self, now=None):
        now = self._clock(now)
        label = PHASES[self.phase]
        if self.fish and self.fish.get("category") == "item":
            label = {"hooked": "钩住了神秘物品，等待大家协力打捞", "landing": "正在把物品打捞出水面",
                     "caught": "展示打捞结果并庆祝", "escaped": "物品滑落，本轮结束"}.get(self.phase, label)
        if self.phase == "diving":
            age = now - self.phase_started
            label = next(text for end, text in ((2, "正在放下鱼竿"), (4, "正在走到钓台边缘"),
                                                (6, "腾空花式跳水"), (7, "入水溅起水花，鱼群受惊"),
                                                (18, "在水中游圈，鱼群散开"), (21, "跃出水面跳回钓台"),
                                                (float("inf"), "拿回鱼竿，准备重新抛竿")) if age < end)
        elif self.interruption:
            label = self.interruption["label"] + "，结束后继续本轮钓鱼"
        return {"active": self.active, "round_id": self.round_id, "phase": self.phase, "label": label,
                "elapsed": max(0, now - self.started), "phase_elapsed": max(0, now - self.phase_started),
                "remaining": max(0, self.deadline - now), "progress": self.progress,
                "pull_age": max(0, now - self.last_pull) if self.last_pull is not None else 100,
                "fish": copy.deepcopy(self.fish), "winner": copy.deepcopy(self.winner), "school": copy.deepcopy(self.school),
                "reason": self.reason, "return_scene": self.return_scene, "paused": self.paused_at is not None,
                "dedicated": bool(self.config.get("dedicated", False)),
                "interruption": copy.deepcopy(self.interruption), "dive": copy.deepcopy(self.dive),
                "gift_pending": sum(entry["count"] for entry in self.gift_bank.values()),
                "gift_age": max(0, now - self.last_gift["at"]) if self.last_gift else 100,
                "gift_boost": copy.deepcopy(self.last_gift), "gift_pulls": self.config.get("gift_pulls", 5),
                "phase_duration": max(0, self.deadline - self.phase_started),
                "contributors": copy.deepcopy(sorted(self.contributors.values(), key=lambda item: -item["score"])[:10]),
                "likes_per_pull": self.config.get("likes_per_pull", 10)}
