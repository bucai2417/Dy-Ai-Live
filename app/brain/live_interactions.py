from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from ..paths import ROOT
from ..gift_show import GiftShow
from ..lines import render_line
from ..settings_store import Effective


log = logging.getLogger(__name__)


class LiveInteractions:
    def init_live_interactions(self):
        self.config_lock = asyncio.Lock()
        self.gift_show = GiftShow()
        self._live_voice_task = None
        self._live_voice_guard = None
        self._gift_voice_id = ""
        self._fishing_voice_key = None
        self._fishing_reminded = ""
        self._sleep_since = None
        self._last_dream_at = 0.0

    @property
    def live_bus(self):
        from .scheduler import hub
        return hub

    @property
    def dedicated_fishing(self):
        config = Effective.fishing()
        return config["enabled"] and config["dedicated"] and Effective.interact()["enabled"]

    async def set_fishing_mode(self, enabled, event=None, manual=False):
        from ..fishing_access import fishing_controller
        from ..settings_store import store
        from .scheduler import metrics
        async with self.config_lock:
            previous = Effective.fishing()
            role = "主播控制台" if manual else fishing_controller(event, previous, metrics)
            if not role:
                raise ValueError("常驻界面切换仅限管理员或获授权榜单的前三名；需要接入源提供用户ID或管理员标记")
            if enabled and (not previous["enabled"] or not Effective.interact()["enabled"]):
                raise ValueError("请主播先在设置中开启钓鱼玩法和人物互动")
            if enabled == previous["dedicated"] and (enabled or not self.fishing.active):
                return "已经在常驻钓鱼界面" if enabled else "已经退出常驻钓鱼界面"
            store.update({"fishing": {"dedicated": enabled}})
            if not enabled:
                self.fishing.cancel()
            public = {key: value for key, value in Effective.fishing().items() if key != "admin_ids"}
            await self.live_bus.broadcast({"type": "config_changed", "config": {"fishing": public}}, remember=False)
            await self.configure_live_features(previous)
            await self.live_bus.broadcast({"type": "world", "world": self.world.snapshot()}, remember=False)
            await self._push_likes()
            if enabled and self.gift_show.active:
                return role + "已开启常驻钓鱼，礼物演出结束后进入钓台"
            return role + ("已进入常驻钓鱼界面" if enabled else "已退出常驻钓鱼界面，返回原场景")

    def sync_activity_pauses(self):
        self.gift_show.pause(self.paused)
        self.fishing.pause(self.paused or bool(self.gift_show.active))
        fishing_busy = self.fishing.active and self.fishing.phase != "interacting"
        self.world.pause(self.paused or fishing_busy or bool(self.gift_show.active))
        self.likes.pause(self.paused or fishing_busy or bool(self.gift_show.active))

    async def interrupt_fishing(self, label, duration):
        if not self.fishing.active:
            return
        self.fishing.interrupt(label, duration)
        self.sync_activity_pauses()
        await self._push_fishing()
        await self.live_bus.broadcast({"type": "world", "world": self.world.snapshot()}, remember=False)
        await self._push_likes()

    async def play_stunt(self, motion):
        from ..like_goal import STUNTS
        if motion not in STUNTS or len(self.likes.pending) >= 3 or self.paused or self.gift_show.active:
            raise ValueError("整活队列已满、直播暂停或正在礼物演出，请稍后再试")
        duration = STUNTS[motion]["duration"] + sum(STUNTS[item["motion"]]["duration"] + self.likes.cooldown for item in self.likes.pending)
        current = self.likes.snapshot().get("active")
        if current:
            duration += max(0, current["duration"] - current["elapsed"]) + self.likes.cooldown
        else:
            clock = self.likes.paused_at if self.likes.paused_at is not None else time.monotonic()
            duration += max(0, self.likes.ready_at - clock)
        await self.interrupt_fishing("完整整活", duration + 1)
        if not self.likes.preview(motion):
            raise ValueError("整活暂时不可用")
        self.likes.tick()
        await self._push_likes()

    async def ensure_dedicated_fishing(self):
        if self.dedicated_fishing and not self.fishing.active and not self.paused and not self.gift_show.active:
            self.fishing.config = Effective.fishing()
            self.fishing.start(self.world.describe()["scene_label"])
            self.sync_activity_pauses()
            await self.live_bus.broadcast({"type": "reaction_clear"}, remember=False)
            await self._push_fishing()
            return True
        return False

    async def configure_live_features(self, previous_fishing=None):
        config = Effective.fishing()
        self.fishing.config = config
        if (not config["enabled"] or not Effective.interact()["enabled"]
                or ((previous_fishing or {}).get("dedicated") and not config["dedicated"])):
            self.fishing.cancel()
        if not Effective.gift_show()["enabled"] or not Effective.interact()["enabled"]:
            self.gift_show.clear()
        self.sync_activity_pauses()
        await self.ensure_dedicated_fishing()
        await self._push_fishing()
        await self._push_gift_show()

    def queue_gift_show(self, event):
        self.gift_show.pause(self.paused)
        config = Effective.gift_show()
        if not config["enabled"] or not Effective.interact()["enabled"]:
            return False
        stage = Effective.stage()
        source = Path(stage["video_path"])
        if not source.is_absolute():
            source = ROOT / source
        video_ready = config["beer_video"] and stage["video_enabled"] and source.is_file()
        return self.gift_show.enqueue(event, video_ready)

    async def _push_gift_show(self):
        await self.live_bus.broadcast({"type": "gift_show", "show": self.gift_show.snapshot()}, remember=False)

    async def sync_gift_show(self):
        self.sync_activity_pauses()
        if self.gift_show.active and self.gift_show.active["effect"] == "sunglasses":
            from ..wardrobe import wear
            try:
                await wear("cool", self.live_bus, event_id=self.gift_show.active["id"])
            except OSError:
                log.exception("墨镜装扮保存失败，本次仍显示配饰")
                await self.live_bus.broadcast({"type": "costume", "name": "cool"}, remember=False)
        await self._push_gift_show()
        await self._push_fishing()
        await self._push_likes()
        await self.live_bus.broadcast({"type": "world", "world": self.world.snapshot()}, remember=False)

    def fishing_result_speaking(self):
        return (self.fishing.phase in ("caught", "escaped", "net_caught") and self._live_voice_task
                and not self._live_voice_task.done() and self._live_voice_guard()
                and self.fishing.snapshot()["phase_elapsed"] < 30)

    async def stop_live_voice(self):
        task = self._live_voice_task
        self._live_voice_task = None
        self._live_voice_guard = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.live_bus.broadcast({"type": "speech_interrupt"}, remember=False)

    def _dreaming(self):
        world = self.world.describe()
        return world["enabled"] and world["activity"] == "sleep" and not world["moving"]

    def live_voice_candidate(self):
        if self.paused:
            return None
        show = self.gift_show.active
        if show:
            if (Effective.gift_show()["voice"] and self._gift_voice_id != show["id"]
                    and self.gift_show.snapshot()["elapsed"] < show["duration"] - 3):
                identifier = show["id"]
                effect = show["effect"]
                key = "gift_" + effect if effect in ("tea", "video", "flowers", "lollipop", "sunglasses") else "gift_special"
                text = render_line(key, name=show["user"]["name"], gift=show["gift"])
                guard = lambda: (not self.paused and Effective.gift_show()["voice"]
                                 and bool(self.gift_show.active) and self.gift_show.active["id"] == identifier)
                return text, guard, ("gift", identifier)
            return None
        if self.fishing.active:
            if not Effective.fishing()["voice"] or self.fishing.paused_at is not None:
                return None
            game = self.fishing.snapshot()
            identifier, phase = game["round_id"], game["phase"]
            if phase == "relocating" and game.get("relocation"):
                from ..fishing_autonomy import SPOTS
                moving = game["relocation"]
                key = (moving["id"], phase)
                if self._fishing_voice_key == key:
                    return None
                text = render_line("fishing_relocate", spot=SPOTS[moving["to"]]["name"], reason=moving["reason"])
                guard = lambda: (not self.paused and not self.gift_show.active and Effective.fishing()["voice"]
                                 and bool(self.fishing.relocation) and self.fishing.relocation["id"] == moving["id"])
                return text, guard, ("fishing", key)
            notice = game.get("auto_notice")
            if (phase == "waiting" and notice and time.monotonic() - notice["at"] <= 20 and Effective.fishing()["auto_voice"]
                    and self._last_fishing_auto_voice != notice["id"]):
                from .scheduler import queue
                if not queue.playing or Effective.idle()["speech_during_music"]:
                    guard = lambda: (not self.paused and not self.gift_show.active and Effective.fishing()["voice"]
                                     and Effective.fishing()["auto_voice"] and self.fishing.phase == "waiting"
                                     and bool(self.fishing.auto_notice) and self.fishing.auto_notice["id"] == notice["id"])
                    return render_line("fishing_auto_invite", spot=notice["spot"]), guard, ("fishing_auto", notice["id"])
            if phase in ("net_cast", "net_caught"):
                haul = game["net"]
                key = (haul["id"], phase)
                if self._fishing_voice_key == key:
                    return None
                values = {"name": haul["user"]["name"], "gift": haul["gift"],
                          "count": len(haul["fish"]), "value": haul["total"],
                          "fish": "、".join(fish["name"] for fish in haul["fish"])}
                guard = lambda: (not self.paused and not self.gift_show.active and Effective.fishing()["voice"]
                                 and bool(self.fishing.net) and self.fishing.net["id"] == haul["id"]
                                 and (self.fishing.phase == phase or phase == "net_cast" and self.fishing.phase == "net_caught"))
                return render_line("fishing_" + phase, **values), guard, ("fishing", key)
            key = (identifier, phase)
            category = "fishing_" + phase
            token = ("fishing", key)
            values = {}
            if phase == "hooked" and self._fishing_voice_key == key:
                if game["phase_elapsed"] < 9 or game["remaining"] < 5 or self._fishing_reminded == identifier:
                    return None
                category, token = "fishing_remind", ("reminder", identifier)
            elif phase not in ("waiting", "hooked", "caught", "escaped", "diving") or self._fishing_voice_key == key:
                return None
            if phase == "caught":
                values = {"fish": game["fish"]["name"], "value": game["fish"]["value"]}
                if game["winner"]:
                    values["name"] = game["winner"]["name"]
                else:
                    category = "fishing_community"
            if (game.get("fish") or {}).get("category") == "item" and phase in ("hooked", "caught", "escaped"):
                category = category.replace("fishing_", "fishing_item_", 1)
            guard = lambda: (not self.paused and not self.gift_show.active and Effective.fishing()["voice"]
                             and self.fishing.round_id == identifier and self.fishing.phase == phase)
            return render_line(category, **values), guard, token
        now = time.monotonic()
        if not self._dreaming():
            self._sleep_since = None
            return None
        if self._sleep_since is None:
            self._sleep_since = now
        config = Effective.idle()
        if (not config["dream_talk"] or now - self._sleep_since < 8
                or now - self._last_dream_at < config["dream_seconds"]):
            return None
        guard = lambda: (not self.paused and not self.gift_show.active and not self.fishing.active
                         and Effective.idle()["dream_talk"] and self._dreaming())
        return render_line("dream_talk"), guard, ("dream", now)

    async def live_voice_tick(self):
        task = self._live_voice_task
        if task:
            if task.done():
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception("互动语音播报失败")
                self._live_voice_task = None
            elif not self._live_voice_guard():
                await self.stop_live_voice()
            else:
                return
        if self.speaker.busy or self._response_task or self._idle_preparing:
            return
        candidate = self.live_voice_candidate()
        if candidate is None:
            return
        text, guard, (kind, token) = candidate
        if kind == "gift":
            self._gift_voice_id = token
        elif kind == "fishing":
            self._fishing_voice_key = token
        elif kind == "reminder":
            self._fishing_reminded = token
        elif kind == "fishing_auto":
            self._last_fishing_auto_voice = token
        else:
            self._last_dream_at = token
        self._live_voice_guard = guard
        self._live_voice_task = asyncio.create_task(self._speak(text, None, 6 if kind == "gift" else 1, motion=""),
                                                   name="brain:interaction_voice")
