from __future__ import annotations

import random
import time

from ..settings_store import Effective
from ..world import SCENES, view


ROUTINES = {"room": ("sleep", "window", "tea", "walk"),
            "terrace": ("lookout", "sleep", "walk"), "cloud": ("lookout", "walk", "run")}


class Autonomy:
    def init_autonomy(self):
        self._audience_at = time.monotonic()
        self._auto_move_at = self._audience_at
        self._auto_scene_at = self._audience_at
        self._auto_scene = self.world.scene
        self._auto_move_pause = None
        self._auto_walk = None
        self._auto_visits = {}
        self._auto_notice = None
        self._auto_voice_id = ""
        self._last_fishing_auto_voice = ""

    def note_audience(self):
        if self.paused:
            return False
        self._audience_at = time.monotonic()
        self._auto_notice = None
        return self.fishing.viewer_activity()

    def pause_autonomy(self, paused):
        now = time.monotonic()
        if paused and self._auto_move_pause is None:
            self._auto_move_pause = now
        elif not paused and self._auto_move_pause is not None:
            delay = now - self._auto_move_pause
            self._audience_at += delay
            self._auto_move_at += delay
            self._auto_scene_at += delay
            if self._auto_walk:
                self._auto_walk["at"] += delay
            self._auto_move_pause = None

    def autonomy_context(self, now=None):
        config = Effective.idle()
        routine = self._auto_walk
        return {"enabled": config["movement_enabled"], "active": bool(routine),
                "quiet_seconds": config["movement_seconds"], "scene_switching": config["scene_switching"],
                "phase": routine["phase"] if routine else "idle", "action": routine["action"] if routine else "",
                "dwell_seconds": routine["dwell"] if routine else 0,
                "next_scene": routine.get("next_scene", "") if routine else "",
                "rule": "自动到达互动区后短暂停留，再起身回到中央；真实观众互动优先，手动指令接管后不自动覆盖"}

    async def _broadcast_auto_world(self, now):
        await self.live_bus.broadcast({"type": "world", "world": self.world.snapshot(now)}, remember=False)
        return True

    def _auto_command(self, kind, value, now):
        self.world.command(kind, value, manual=True, now=now)
        self._auto_walk["revision"] = self.world.revision

    def _auto_announce(self, category, **values):
        self._auto_notice = {"id": str(self.world.revision) + category, "revision": self.world.revision,
                             "category": category, "values": values}

    def autonomous_voice_candidate(self):
        from ..lines import render_line
        from .scheduler import queue
        notice = self._auto_notice
        if (not notice or notice["id"] == self._auto_voice_id or self.paused or self.fishing.active
                or self.gift_show.active or self.games.active or not Effective.idle()["movement_voice"]
                or not Effective.idle()["movement_enabled"] or not Effective.interact()["enabled"]
                or not self._auto_walk or self.world.revision != notice["revision"]
                or self.world.paused_at is not None or self._pending_replies
                or queue.playing and not Effective.idle()["speech_during_music"]):
            return None
        guard = lambda: (not self.paused and not self.gift_show.active and not self.fishing.active
                         and Effective.idle()["movement_voice"] and Effective.idle()["movement_enabled"]
                         and Effective.interact()["enabled"]
                         and self._auto_notice is notice and self.world.revision == notice["revision"])
        return render_line(notice["category"], **notice["values"]), guard, ("world_auto", notice["id"])

    def _next_auto_activity(self, scene, config):
        choices = ROUTINES[scene] if config["scene_interactions"] else ("walk",)
        index = self._auto_visits.get(scene, 0)
        self._auto_visits[scene] = index + 1
        return choices[index % len(choices)]

    async def _advance_auto(self, config, now):
        routine = self._auto_walk
        current = self.world.describe(now)
        if self.world.revision != routine["revision"]:
            self._auto_walk = self._auto_notice = None
            self._auto_move_at = now
            return False
        if self.world.paused_at is not None:
            return False
        cancelled = (not config["movement_enabled"] or not current["enabled"]
                     or not Effective.interact()["enabled"] or self._audience_at > routine["at"]
                     or not config["scene_interactions"] and routine["action"] != "walk"
                     or not config["scene_switching"] and routine["phase"] == "door")
        if cancelled:
            if current["enabled"]:
                if current["activity"] in ("sleep", "window", "tea", "lookout"):
                    self.world.command("activity", "wake", manual=True, now=now)
                else:
                    self.world.command("move", "stop", manual=True, now=now)
            self._auto_walk = self._auto_notice = None
            self._auto_move_at = now
            return await self._broadcast_auto_world(now)
        if current["moving"]:
            return False
        speaking = (self._auto_notice and self._auto_voice_id == self._auto_notice["id"]
                    and self._live_voice_task and not self._live_voice_task.done())
        if speaking and view(self.world.snapshot(now))["activity_age"] < 30:
            return False
        if routine["phase"] == "door":
            destination = routine.pop("next_scene")
            self._auto_command("scene", destination, now)
            self._auto_scene, self._auto_scene_at = destination, now
            routine["phase"] = "visiting"
            self._auto_command("activity", routine["action"], now)
            self._auto_announce("auto_scene", scene=SCENES[destination])
            return await self._broadcast_auto_world(now)
        if routine["phase"] == "returning" or routine["action"] in ("walk", "run"):
            self._auto_walk = self._auto_notice = None
            self._auto_move_at = now
            return False
        pose = view(self.world.snapshot(now))
        if pose["activity_age"] < routine["dwell"]:
            routine["phase"] = "resting"
            return False
        self._auto_command("activity", "wake", now)
        routine["phase"] = "returning"
        self._auto_announce("auto_return")
        return await self._broadcast_auto_world(now)

    async def autonomous_tick(self, now=None):
        now = time.monotonic() if now is None else now
        if self.paused or self.games.active:
            return False
        config = Effective.idle()
        if self.world.scene != self._auto_scene:
            self._auto_scene, self._auto_scene_at = self.world.scene, now
        if self._auto_walk:
            changed = await self._advance_auto(config, now)
            if changed or self._auto_walk and not self.fishing.active:
                return changed
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
        switch = config["scene_switching"] and now - self._auto_scene_at >= config["scene_switch_seconds"]
        sequence = ("room", "cloud", "terrace")
        scene = sequence[(sequence.index(current["scene"]) + 1) % len(sequence)] if switch else current["scene"]
        action = self._next_auto_activity(scene, config)
        self._auto_walk = {"revision": self.world.revision, "at": now, "action": action,
                           "phase": "door" if switch else "visiting",
                           "dwell": random.uniform(config["interaction_seconds"] * .8, config["interaction_seconds"] * 1.2)}
        if switch:
            self._auto_walk["next_scene"] = scene
            self._auto_command("move", "door", now)
            self._auto_announce("auto_depart", scene=SCENES[scene])
        else:
            self._auto_command("activity", action, now)
            self._auto_announce("auto_" + action)
        self._auto_move_at = now
        return await self._broadcast_auto_world(now)
