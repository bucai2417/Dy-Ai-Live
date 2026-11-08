from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict, deque

from .actions import ACTIONS, COSTUMES, SKINS, EFFECTS, RULE_ACTIONS, event_action
from ..gifts import GiftBoard


class StageState:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = {"name": "大肥鱼", "stage": {}, "music": {}}
        self.connected = False
        self.paused = False
        self.paused_at = 0.0
        self.animation_started = time.monotonic()
        self.paused_seconds = 0.0
        self.stats = {}
        self.room = {}
        self.events = deque(maxlen=8)
        self.event_ids = OrderedDict()
        self.gift_senders = OrderedDict()
        self.gift_board = GiftBoard()
        self.gift_ranking = None
        self.avatar_images = {}
        self.costume = ""
        self.outfit = ""
        self.celebration_until = 0.0
        self.video_image = None
        self.video_pending = 0
        self.music = {}
        self.playback = {}
        self.subtitle = "欢迎来到深蓝的小小宇宙"
        self.subtitle_until = 0.0
        self.subtitle_at = 0.0
        self.mouth = 0.0
        self.mouth_at = 0.0
        self.reaction = {}
        self.reaction_until = 0.0
        self.last_reaction_at = -10.0
        self.danmu = 0
        self.like_goal = {}
        self.like_goal_at = 0.0
        self.world = {}
        self.world_at = 0.0
        self.fishing = {}
        self.fishing_at = 0.0
        self.gift_show = {}
        self.gift_show_at = 0.0
        self.game = {}
        self.game_at = 0.0

    def apply(self, message: dict):
        kind = message.get("type")
        now = time.monotonic()
        with self.lock:
            if kind == "hello":
                self.config.update(message.get("config") or {})
                self.stats = message.get("stats") or {}
                if "giftRanking" in self.stats:
                    self._rank(self.stats["giftRanking"], now, animate=False)
                self.music = message.get("music") or {}
                self.paused = bool(message.get("paused"))
                self.paused_at = now if self.paused else 0.0
                self.animation_started = now
                self.paused_seconds = 0.0
                self.costume = (self.config.get("stage") or {}).get("costume", "original")
                self.celebration_until = 0
                self.outfit = (self.config.get("stage") or {}).get("outfit", "sailor")
                self.like_goal = message.get("likeGoal") or {}
                self.like_goal_at = now
                self.world = message.get("world") or {}
                self.world_at = now
                self.fishing = message.get("fishing") or {}
                self.fishing_at = now
                self.gift_show = message.get("giftShow") or {}
                self.gift_show_at = now
                self.game = message.get("game") or {}
                self.game_at = now
                self.reaction = {}
                self.reaction_until = 0

                self.subtitle_until = 0
                self.danmu = (message.get("eventStats") or {}).get("danmu_total", 0)
                self.events.clear()
                for item in message.get("recent") or []:
                    if item.get("type") == "event":
                        self.events.append(item.get("event") or {})
                        self._remember_event((item.get("event") or {}).get("id"))
            elif kind == "config_changed":
                self.config.update(message.get("config") or {})
                if "stage" in (message.get("config") or {}):
                    self.costume = (self.config.get("stage") or {}).get("costume", "original")
                    if self.costume == "cool":
                        self.celebration_until = 0
                    self.outfit = (self.config.get("stage") or {}).get("outfit", "sailor")
                self._rank(self.gift_ranking or self.gift_board.snapshot(), now, animate=False,
                           authoritative=self.gift_ranking is not None)
                if not self.config.get("interact", {}).get("enabled", True):
                    self.reaction = {}
                    self.reaction_until = 0
            elif kind == "event" and isinstance(message.get("event"), dict):
                event = message["event"]
                if self._remember_event(event.get("id")):
                    self.events.append(event)
                    if event.get("kind") == "chat":
                        self.danmu += 1
                    if event.get("kind") == "gift":
                        self._gift(event, now)
                    routed = event.get("kind") == "chat" and self.config.get("interact", {}).get("intent_routing")
                    action = None if routed else event_action(event)
                    if action:
                        self._react(action, now, priority=1)
            elif kind == "fishing":
                self.fishing = message.get("fishing") or {}
                self.fishing_at = now
            elif kind == "gift_show":
                self.gift_show = message.get("show") or {}
                self.gift_show_at = now
            elif kind == "game":
                self.game = message.get("game") or {}
                self.game_at = now
            elif kind == "world":
                self.world = message.get("world") or {}
                self.world_at = now
            elif kind == "like_goal":
                self.like_goal = message.get("goal") or {}
                self.like_goal_at = now
            elif kind == "stats":
                self.stats = message.get("stats") or {}
                if "eventStats" in message:
                    self.danmu = message["eventStats"].get("danmu_total", self.danmu)
                self.room = message.get("room") or self.room
                if "giftRanking" in self.stats:
                    self._rank(self.stats["giftRanking"], now)
            elif kind == "gift_ranking":
                self._rank(message.get("ranking") or {}, now)
            elif kind == "music":
                previous_track = (self.music.get("current") or {}).get("id")
                self.music = message.get("music") or {}
                current_track = (self.music.get("current") or {}).get("id")
                if (current_track and current_track != previous_track and self.music.get("playing")
                        and self.config.get("stage", {}).get("scene_reactions", False)):
                    self._react("dance", now, priority=1)
                if self.playback.get("id") != (self.music.get("current") or {}).get("id"):
                    self.playback = {}
            elif kind == "subtitle" and not self.paused:
                self.subtitle = str(message.get("text") or "")
                self.subtitle_at = now
                self.subtitle_until = now + max(8, len(self.subtitle) * .18)
            elif kind == "state":
                if self.game.get("active") and not self.paused and not self.game.get("paused"):
                    delta = max(0, now - self.game_at)
                    self.game["elapsed"] = self.game.get("elapsed", 0) + delta
                    self.game["remaining"] = max(0, self.game.get("remaining", 0) - delta)
                self.game_at = now
                if (self.gift_show.get("active") and not self.paused and not self.gift_show.get("paused")
                        and not self.gift_show.get("holding")):
                    self.gift_show["elapsed"] = self.gift_show.get("elapsed", 0) + max(0, now - self.gift_show_at)
                self.gift_show_at = now
                if self.fishing.get("active") and not self.paused and not self.fishing.get("paused"):
                    delta = max(0, now - self.fishing_at)
                    for key in ("elapsed", "phase_elapsed", "pull_age", "gift_age"):
                        self.fishing[key] = self.fishing.get(key, 0) + delta
                    self.fishing["remaining"] = max(0, self.fishing.get("remaining", 0) - delta)
                self.fishing_at = now
                if self.world and not self.paused and not self.world.get("paused"):
                    for key in ("movement", "camera_move", "focus_move"):
                        if key in self.world:
                            self.world[key]["elapsed"] += max(0, now - self.world_at)
                self.world_at = now
                if self.like_goal.get("active") and not self.paused and not self.like_goal.get("paused"):
                    self.like_goal["active"]["elapsed"] += max(0, now - self.like_goal_at)
                self.like_goal_at = now
                paused = bool(message.get("paused"))
                if paused and not self.paused:
                    self.paused_at = now
                elif self.paused and not paused:
                    delay = max(0, now - self.paused_at)
                    self.paused_seconds += delay
                    if self.reaction:
                        self.reaction["started"] += delay
                        self.reaction_until += delay
                    self.paused_at = 0.0
                self.paused = paused
                self.gift_show["paused"] = paused
                self.game["paused"] = paused or bool(self.gift_show.get("active"))
                self.fishing["paused"] = paused or bool(self.gift_show.get("active")) or bool(self.game.get("active"))
                activity_paused = paused or bool(self.gift_show.get("active")) or bool(self.game.get("active")) or (bool(self.fishing.get("active")) and self.fishing.get("phase") != "interacting")
                self.world["paused"] = activity_paused
                self.like_goal["paused"] = activity_paused
                self.mouth = 0
                self.subtitle_until = 0
            elif kind == "speech_interrupt":
                self.mouth = 0
                self.subtitle_until = 0
            elif kind == "reaction" and not self.paused:
                if message.get("ambient") and (self.video_image is not None or self.like_goal.get("active")):
                    return
                motion = str(message.get("motion") or "happy")
                if motion in ("happy", "shake"):
                    motion = RULE_ACTIONS.get(message.get("rule_id"), motion)
                self._react(motion, now, effect=message.get("effect"),
                            priority=4 if message.get("manual") else 0 if message.get("ambient") else 2,
                            manual=bool(message.get("manual")))
            elif kind == "motion":
                self._react(str(message.get("name") or ""), now, effect="", priority=0)
            elif kind == "costume" and not self.paused:
                costume = message.get("name")
                if isinstance(costume, str) and costume in COSTUMES:
                    if costume in SKINS:
                        self.outfit = costume
                    else:
                        self.costume = costume
                        if costume == "original":
                            self.outfit = "sailor"
                    self.celebration_until = 0
            elif kind == "reset":
                self.gift_show = {}
                self.gift_show_at = now
                self.game = {}
                self.game_at = now
                self.fishing = {}
                self.fishing_at = now
                self.like_goal = {}
                self.like_goal_at = now
                self.events.clear()
                self.event_ids.clear()
                self.gift_senders.clear()
                self.gift_board.clear()
                self.gift_ranking = None
                self.avatar_images.clear()
                self.costume = ""
                self.celebration_until = 0
                self.video_image = None
                self.video_pending = 0
                self.music = {}
                self.playback = {}
                self.subtitle_until = 0
                self.mouth = 0
                self.reaction_until = 0
                self.reaction = {}

            if "performance" in message:
                self._performance(message["performance"], now)

    def _performance(self, performance, now):
        now = self.paused_at if self.paused else now
        if not performance:
            self.reaction, self.reaction_until = {}, 0
            return
        if performance.get("motion") not in ACTIONS:
            return
        elapsed = max(0, min(float(performance.get("elapsed", 0)), float(performance["duration"])))
        self.reaction = {**performance, "started": now - elapsed}
        self.reaction_until = now + max(0, float(performance.get("remaining", 0)))
        self.last_reaction_at = self.reaction["started"]

    def _remember_event(self, identifier):
        if not identifier:
            return True
        if identifier in self.event_ids:
            return False
        self.event_ids[identifier] = None
        while len(self.event_ids) > 2048:
            self.event_ids.popitem(last=False)
        return True

    def _gift(self, event, now):
        if self.gift_ranking is None:
            self.gift_board.add(event)
            self._rank(self.gift_board.snapshot(), now, authoritative=False)

    def _rank(self, ranking, now, animate=True, authoritative=True):
        if authoritative:
            self.gift_ranking = ranking
        stage = self.config.get("stage") or {}
        mode = "count" if stage.get("ball_rank_by") == "count" else "value"
        limit = max(0, min(10, int(stage.get("ball_max", 10))))
        self.stats["ranking"] = ranking.get(mode, [])[:10]
        previous = self.gift_senders
        self.gift_senders = OrderedDict()
        for row in ranking.get(mode, [])[:limit]:
            key = row.get("key") or "name:" + str(row.get("name") or "观众")
            old = previous.get(key) or {}
            changed = row.get("count") != old.get("count")
            self.gift_senders[key] = {**row, "key": key, "at": now if changed else old.get("at", now)}
        urls = {sender.get("avatar") for sender in self.gift_senders.values()}
        urls.add(((self.gift_show.get("active") or {}).get("user") or {}).get("avatar"))
        urls.add(((self.fishing.get("net") or {}).get("user") or {}).get("avatar"))
        self.avatar_images = {url: image for url, image in self.avatar_images.items() if url in urls}
        if (animate and previous and self.gift_senders and next(iter(previous)) != next(iter(self.gift_senders))
                and stage.get("scene_reactions", False) and not self.paused
                and self.config.get("interact", {}).get("enabled", True)):
            self._react("happy", now, priority=2)
            if (self.costume or stage.get("costume", "original")) != "cool":
                self.celebration_until = now + 8

    def set_avatar(self, url, image):
        with self.lock:
            donor = ((self.gift_show.get("active") or {}).get("user") or {}).get("avatar")
            net_donor = ((self.fishing.get("net") or {}).get("user") or {}).get("avatar")
            if url in (donor, net_donor) or any(sender.get("avatar") == url for sender in self.gift_senders.values()):
                self.avatar_images[url] = image

    def set_video(self, image, pending=0):
        with self.lock:
            self.video_image = image
            self.video_pending = pending

    def _react(self, motion, now, effect=None, priority=1, manual=False):
        if self.paused or motion not in ACTIONS:
            return
        if not manual and not self.config.get("interact", {}).get("enabled", True):
            return
        replace_manual = manual and now - self.last_reaction_at >= 0.6
        repeated_request = priority == 2 and self.reaction.get("priority", 0) <= 2
        if now < self.reaction_until and priority <= self.reaction.get("priority", 0) and not replace_manual and not repeated_request:
            return
        if now - self.last_reaction_at < 0.6 and priority <= self.reaction.get("priority", 0) and not repeated_request:
            return
        preset = ACTIONS[motion]
        effect = preset["effect"] if effect is None else effect
        self.reaction = {**preset, "motion": motion, "started": now, "priority": priority,
                         "effect": effect if effect in EFFECTS else ""}
        self.reaction_until = now + preset["duration"]
        self.last_reaction_at = now

    def set_playback(self, identifier, position, duration, status):
        with self.lock:
            self.playback = {"id": identifier, "position": max(0, position), "duration": max(0, duration),
                             "status": status, "at": time.monotonic()}

    def snapshot(self) -> dict:
        with self.lock:
            snapshot = copy.deepcopy({
                "config": self.config, "connected": self.connected, "paused": self.paused,
                "stats": self.stats, "room": self.room, "events": list(self.events),
                "music": self.music, "playback": self.playback, "subtitle": self.subtitle, "subtitle_until": self.subtitle_until, "subtitle_at": self.subtitle_at,
                "mouth": self.mouth, "mouth_at": self.mouth_at, "reaction": self.reaction,
                "reaction_until": self.reaction_until, "danmu": self.danmu,
                "gift_senders": list(self.gift_senders.values()),
                "costume": self.costume, "celebration_until": self.celebration_until,
                "outfit": self.outfit, "paused_at": self.paused_at,
                "animation_time": max(0, (self.paused_at if self.paused else time.monotonic())
                                      - self.animation_started - self.paused_seconds),
                "video_pending": self.video_pending,
                "like_goal": self.like_goal, "like_goal_at": self.like_goal_at,
                "world": self.world, "world_at": self.world_at,
                "fishing": self.fishing, "fishing_at": self.fishing_at,
                "gift_show": self.gift_show, "gift_show_at": self.gift_show_at,
                "game": self.game, "game_at": self.game_at,
            })
            snapshot["avatar_images"] = self.avatar_images.copy()
            snapshot["video_image"] = self.video_image
            return snapshot

    def set_mouth(self, value: float):
        with self.lock:
            self.mouth = max(0.0, min(1.0, value))
            self.mouth_at = time.monotonic()


class FrameStore:
    def __init__(self):
        self.condition = threading.Condition()
        self.image = None
        self.sequence = 0
        self.fps = 0.0
        self.render_ms = 0.0
        self.error = ""
        self.jpeg_sequence = -1
        self.jpeg = b""
        self.jpeg_lock = threading.Lock()
        self.stopped = False

    def publish(self, image, fps: float, render_ms: float):
        with self.condition:
            self.image = image
            self.sequence += 1
            self.fps = fps
            self.render_ms = render_ms
            self.condition.notify_all()

    def latest(self):
        with self.condition:
            return self.image, self.sequence

    def close(self):
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
