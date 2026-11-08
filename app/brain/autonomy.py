from __future__ import annotations

import time

from ..settings_store import Effective


class Autonomy:
    def init_autonomy(self):
        self._audience_at = time.monotonic()
        self._auto_move_at = self._audience_at
        self._auto_move_pause = None
        self._auto_walk = None
        self._last_fishing_auto_voice = ""

    def note_audience(self):
        if self.paused:
            return False
        self._audience_at = time.monotonic()
        return self.fishing.viewer_activity()

    def pause_autonomy(self, paused):
        now = time.monotonic()
        if paused and self._auto_move_pause is None:
            self._auto_move_pause = now
        elif not paused and self._auto_move_pause is not None:
            delay = now - self._auto_move_pause
            self._audience_at += delay
            self._auto_move_at += delay
            if self._auto_walk:
                self._auto_walk["at"] += delay
            self._auto_move_pause = None

    async def autonomous_tick(self, now=None):
        now = time.monotonic() if now is None else now
        if self.paused:
            return False
        config = Effective.idle()
        if self._auto_walk:
            current = self.world.describe(now)
            if self.world.revision != self._auto_walk["revision"]:
                self._auto_walk = None
            elif self.world.paused_at is None and (not current["moving"] or not config["movement_enabled"]
                    or self._audience_at > self._auto_walk["at"] or not Effective.interact()["enabled"]):
                if current["enabled"]:
                    self.world.command("move", "stop", manual=True, now=now)
                self._auto_walk = None
                await self.live_bus.broadcast({"type": "world", "world": self.world.snapshot(now)}, remember=False)
        if (not Effective.interact()["enabled"] or self.gift_show.active or self.likes.active
                or self.likes.pending or self._pending_replies or self._response_task or self._idle_preparing
                or self.speaker.busy or self._live_voice_task or self.live_bus.performance.snapshot()):
            return False
        if self.fishing.active:
            if self.fishing.auto_tick(now):
                await self._sync_fishing()
                return True
            return False
        current = self.world.describe(now)
        if (not config["movement_enabled"] or not current["enabled"] or current["moving"]
                or current["activity"] != "idle" or self.world.paused_at is not None
                or now - max(self._audience_at, self._auto_move_at) < config["movement_seconds"]):
            return False
        self.world.command("activity", "walk", manual=True, now=now)
        self._auto_walk = {"revision": self.world.revision, "at": now}
        self._auto_move_at = now
        await self.live_bus.broadcast({"type": "world", "world": self.world.snapshot(now)}, remember=False)
        return True
