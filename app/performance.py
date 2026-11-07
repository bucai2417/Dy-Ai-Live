from __future__ import annotations

import time

from .native.actions import ACTIONS, EFFECTS, RULE_ACTIONS, event_action


class PerformanceState:
    TYPES = {"event", "reaction", "motion", "music", "state", "reset", "config_changed", "reaction_clear", "gift_show"}

    def __init__(self):
        self.reaction = {}
        self.until = 0.0
        self.last_at = -100.0
        self.paused_at = None
        self.live_paused = self.gift_paused = False
        self.track_id = None

    def snapshot(self, now=None):
        clock = self.paused_at if self.paused_at is not None else time.monotonic() if now is None else now
        if not self.reaction or clock >= self.until:
            return None
        return {**{key: value for key, value in self.reaction.items() if key != "started"},
                "elapsed": round(max(0, clock - self.reaction["started"]), 4),
                "remaining": round(max(0, self.until - clock), 4)}

    def observe(self, message, config, now=None):
        now = time.monotonic() if now is None else now
        kind = message.get("type")
        enabled = config.get("interact", {}).get("enabled", True)
        if kind in ("reset", "reaction_clear") or (kind == "config_changed" and not enabled):
            self.reaction, self.until = {}, 0
            self.last_at = -100.0
            if kind == "reset":
                self.track_id = None
            return
        if kind in ("state", "gift_show"):
            if kind == "state":
                self.live_paused = bool(message.get("paused"))
            else:
                self.gift_paused = bool((message.get("show") or {}).get("active"))
            paused = self.live_paused or self.gift_paused
            if paused and self.paused_at is None:
                self.paused_at = now
            elif not paused and self.paused_at is not None:
                delay = now - self.paused_at
                if self.reaction:
                    self.reaction["started"] += delay
                    self.until += delay
                    self.last_at += delay
                self.paused_at = None
            return
        motion, effect, priority, manual = "", None, 1, False
        if kind == "event":
            event = message.get("event") or {}
            if event.get("kind") != "chat" or not config.get("interact", {}).get("intent_routing"):
                motion = event_action(event)
        elif kind == "reaction":
            motion = str(message.get("motion") or "happy")
            if motion in ("happy", "shake"):
                motion = RULE_ACTIONS.get(message.get("rule_id"), motion)
            manual = bool(message.get("manual"))
            priority = 4 if manual else 0 if message.get("ambient") else 2
            effect = message.get("effect")
        elif kind == "motion":
            motion, effect, priority = str(message.get("name") or ""), "", 0
        elif kind == "music":
            music = message.get("music") or {}
            identifier = (music.get("current") or {}).get("id")
            if identifier and identifier != self.track_id and music.get("playing") and config.get("stage", {}).get("scene_reactions", False):
                motion = "dance"
            self.track_id = identifier
        if self.paused_at is not None or motion not in ACTIONS or (not enabled and not manual):
            return
        previous_priority = self.reaction.get("priority", 0)
        repeated = priority == 2 and previous_priority <= 2
        replace_manual = manual and now - self.last_at >= .6
        if now < self.until and priority <= previous_priority and not replace_manual and not repeated:
            return
        if now - self.last_at < .6 and priority <= previous_priority and not repeated:
            return
        preset = ACTIONS[motion]
        effect = preset["effect"] if effect is None else effect
        self.reaction = {**preset, "motion": motion, "started": now, "priority": priority,
                         "effect": effect if effect in EFFECTS else ""}
        self.until = now + preset["duration"]
        self.last_at = now
