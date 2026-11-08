from __future__ import annotations

import copy
import uuid


SPOTS = {
    "cloud": {"name": "云汐钓台", "description": "开阔云湖，鱼群种类均衡", "favored": ()},
    "reeds": {"name": "芦苇浅湾", "description": "芦苇与荷叶环绕，小型水族更活跃",
              "favored": ("silver", "perch", "koi", "goldfish", "catfish", "puffer")},
    "reef": {"name": "星礁海岸", "description": "灯塔照着礁石，海洋水族更活跃",
             "favored": ("tuna", "shark", "whale", "ray", "lobster", "crab", "blue_tang", "clown")},
}
AMBIENT_ACTIONS = {"scan": "遮眼观察远处鱼群", "stretch": "持竿伸懒腰",
                   "shuffle": "沿钓台挪动位置", "inspect": "低头检查鱼线"}


class FishingAutonomy:
    def init_autonomy(self):
        self.spot = "cloud"
        self.session_started = None
        self.quiet_since = self.spot_since = self.last_auto_at = self.last_invite_at = 0.0
        self.last_success_at = None
        self.failed_rounds = 0
        self.next_auto_at = 0.0
        self.last_ambient_kind = ""
        self.ambient = self.relocation = self.auto_notice = None

    def begin_session(self, now):
        if self.session_started is not None:
            return
        self.session_started = now
        self.quiet_since = self.spot_since = self.last_auto_at = self.last_invite_at = now
        self.last_success_at = None
        self.failed_rounds = 0
        self.next_auto_at = now

    def viewer_activity(self, now=None):
        if self.paused_at is not None:
            return False
        self.quiet_since = self._clock(now)
        changed = bool(self.ambient or self.auto_notice)
        self.ambient = self.auto_notice = None
        if changed:
            self.revision += 1
        return changed

    def spot_weights(self, kinds):
        from .fishing_catalog import FISH
        favorites = SPOTS[self.spot]["favored"]
        return [FISH[kind]["chance"] * (2 if kind in favorites else 1) for kind in kinds]

    def relocate(self, target="next", now=None, reason="观众提议换个地方钓鱼", automatic=False):
        if self.paused_at is not None:
            raise ValueError("直播已暂停或正在礼物演出")
        self.tick(now)
        if self.phase != "waiting" or self.net_queue or self.gift_bank:
            raise ValueError("请等本条鱼和礼物演出完成，回到等待咬钩时再换钓点")
        choices = tuple(SPOTS)
        target = choices[(choices.index(self.spot) + 1) % len(choices)] if target == "next" else target
        if target not in SPOTS:
            raise ValueError("未知钓点")
        if target == self.spot:
            raise ValueError("已经在这个钓点，可以发换个钓点")
        now = self._clock(now)
        self.relocation = {"id": uuid.uuid4().hex[:12], "from": self.spot, "to": target,
                           "reason": reason, "automatic": automatic}
        self.ambient = self.auto_notice = None
        self.last_auto_at = now
        self._phase("relocating", now, 10)
        return True

    def finish_relocation(self, now):
        self.spot = self.relocation["to"]
        self.spot_since = self.last_auto_at = now
        self.failed_rounds = 0
        self.relocation = None
        self._phase("idle", now)
        self.start(self.return_scene, now)

    def auto_tick(self, now=None):
        now = self._clock(now)
        if not self.active or self.paused_at is not None or not self.config.get("dedicated"):
            return False
        changed = False
        if self.auto_notice and (now - self.auto_notice["at"] > 20 or not self.config.get("auto_voice", True)):
            self.auto_notice = None
            self.revision += 1
            changed = True
        if self.ambient and (now - self.ambient["at"] >= self.ambient["duration"] or not self.config.get("auto_actions", True)):
            self.ambient = None
            self.revision += 1
            changed = True
        if self.phase != "waiting" or self.net_queue or self.gift_bank or self.deadline - now < 4:
            return changed
        if now - self.quiet_since < 3:
            return changed
        dry = now - max(self.session_started or 0, self.spot_since, self.last_success_at or 0)
        if self.config.get("auto_relocate", True) and (dry >= self.config.get("dry_seconds", 180)
                or self.failed_rounds >= self.config.get("failed_limit", 3)):
            reason = f"连续{self.failed_rounds}竿没收获" if self.failed_rounds >= self.config.get("failed_limit", 3) else f"已经{int(dry)}秒没有收获"
            return self.relocate(now=now, reason=reason, automatic=True)
        if (self.config.get("auto_actions", True) and self.ambient is None
                and now - max(self.quiet_since, self.last_auto_at) >= self.config.get("quiet_seconds", 25)
                and now >= self.next_auto_at):
            choices = [kind for kind in AMBIENT_ACTIONS if kind != self.last_ambient_kind]
            kind = self.rng.choice(choices)
            self.last_ambient_kind = kind
            self.ambient = {"id": uuid.uuid4().hex[:12], "kind": kind, "label": AMBIENT_ACTIONS[kind],
                            "at": now, "duration": self.rng.uniform(5, 7)}
            self.last_auto_at = now
            self.next_auto_at = now + self.config.get("quiet_seconds", 25) * self.rng.uniform(1, 1.35)
            self.revision += 1
            changed = True
        if (self.config.get("auto_voice", True) and self.config.get("voice", True) and self.auto_notice is None
                and now - max(self.quiet_since, self.last_invite_at) >= self.config.get("auto_invite_seconds", 90)
                and self.deadline - now >= 7):
            self.auto_notice = {"id": uuid.uuid4().hex[:12], "at": now, "spot": SPOTS[self.spot]["name"]}
            self.last_invite_at = now
            self.revision += 1
            changed = True
        return changed

    def record_phase(self, name, now):
        if name != "waiting":
            self.ambient = self.auto_notice = None
        if name == "escaped":
            self.failed_rounds += 1
        elif name in ("caught", "net_caught"):
            self.last_success_at = now
            self.failed_rounds = 0

    def pause_autonomy(self, delay):
        for key in ("session_started", "quiet_since", "spot_since", "last_auto_at", "last_invite_at", "last_success_at", "next_auto_at"):
            value = getattr(self, key)
            if value is not None:
                setattr(self, key, value + delay)
        for activity in (self.ambient, self.auto_notice):
            if activity:
                activity["at"] += delay

    def autonomy_snapshot(self, now):
        ambient = copy.deepcopy(self.ambient)
        if ambient:
            ambient["elapsed"] = max(0, now - ambient["at"])
            if ambient["elapsed"] >= ambient["duration"] or not self.config.get("auto_actions", True):
                ambient = None
        return {"spot": self.spot, "spot_name": SPOTS[self.spot]["name"],
                "spot_description": SPOTS[self.spot]["description"], "ambient": ambient,
                "relocation": copy.deepcopy(self.relocation), "auto_notice": copy.deepcopy(self.auto_notice),
                "quiet_seconds": max(0, now - self.quiet_since), "failed_rounds": self.failed_rounds,
                "dry_seconds": max(0, now - max(self.session_started if self.session_started is not None else now, self.last_success_at or 0)),
                "auto_actions": self.config.get("auto_actions", True),
                "auto_relocate": self.config.get("auto_relocate", True)}
