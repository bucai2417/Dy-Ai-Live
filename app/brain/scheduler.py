"""大脑调度层 —— 本项目真正的自研核心。

职责：
1. 过滤：空消息、重复、超长、敏感词、同一用户刷屏
2. 打分：礼物 > 粉丝团 > 提问 > 普通闲聊，叠加新鲜度衰减
3. 节奏：最小发言间隔、说话时长预算、冷场主动找话题
4. 打断：高价值弹幕可以打断当前播报
5. 组织 prompt → 调 LLM → 交给语音层播报
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from contextlib import aclosing

from ..config import settings
from ..events import Event
from ..hub import hub
from ..settings_store import Effective, store
from ..tts.edge import build_engine, split_sentences
from ..metrics import metrics
from ..music import resolve as music_resolve
from ..music_policy import approved_songs, blocked_reason, query_reason, playback_reason
from ..playlist import make_track, queue
from ..like_goal import LikeGoal
from ..lines import render_line
from . import songcmd
from .interact import InteractEngine, pick_expression
from .llm import LLM, random_topic
from .utterance import complete_utterance
from .live_interactions import LiveInteractions
from .autonomy import Autonomy
from .companion import AICompanion
from .games import Games

log = logging.getLogger(__name__)

QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|如何|能不能|可不可以|是不是")


class Speaker:
    """语音播报器：逐句合成、逐句推送，支持打断。"""

    def __init__(self) -> None:
        self.tts = build_engine()
        self.busy = False
        self.current_score = 0.0
        self._cancel = asyncio.Event()
        self._gate = asyncio.Lock()

    def interrupt(self) -> None:
        if self.busy:
            log.info("打断当前播报")
            self._cancel.set()

    async def say(self, text: str, meta: dict | None = None, score: float = 0.0) -> bool:
        if not text.strip():
            return False
        async with self._gate:
            if self.tts.name != Effective.tts()["engine"]:
                self.tts = build_engine()
            self.busy = True
            self.current_score = score
            self._cancel.clear()
            sentences = split_sentences(text)
            try:
                await hub.broadcast({"type": "speech_start", "text": text, "meta": meta or {}}, remember=False)
                for index, sentence in enumerate(sentences):
                    if self._cancel.is_set():
                        break
                    await hub.broadcast({"type": "subtitle", "text": sentence, "index": index,
                                         "total": len(sentences), "final": index == len(sentences) - 1})
                    try:
                        audio = await asyncio.wait_for(self.tts.synthesize(sentence), timeout=20)
                    except Exception:
                        log.warning("语音合成失败，继续显示字幕", exc_info=True)
                        audio = None
                    if self._cancel.is_set():
                        break
                    if audio:
                        import base64

                        identifier, receipt = hub.playback.prepare(hub.clients)
                        cancelled = asyncio.create_task(self._cancel.wait())
                        try:
                            await hub.broadcast({
                                "type": "voice", "id": identifier, "text": sentence,
                                "audio": base64.b64encode(audio).decode(), "mime": "audio/mpeg",
                            }, remember=False)
                            if receipt is not None:
                                done, _ = await asyncio.wait((receipt, cancelled), timeout=max(90, len(sentence) * 3),
                                                             return_when=asyncio.FIRST_COMPLETED)
                                if not done:
                                    log.warning("未收到语音播放完成确认：%s", identifier)
                                    await hub.broadcast({"type": "error", "error": "语音播放确认超时，请检查原生工作台的音频输出"}, remember=False)
                                elif receipt in done and receipt.result() != "ended":
                                    log.warning("语音未正常播完：%s %s", identifier, receipt.result())
                        finally:
                            hub.playback.forget(identifier)
                            cancelled.cancel()
                            await asyncio.gather(cancelled, return_exceptions=True)
                    elif self.tts.name != "none":
                        await hub.broadcast({"type": "error", "error": "当前句语音合成失败或服务正在冷却，已保留字幕"}, remember=False)
                        await asyncio.sleep(min(0.9 + len(sentence) * 0.12, 3.5))
                return not self._cancel.is_set()
            finally:
                self.busy = False
                self.current_score = 0.0
                await hub.broadcast({"type": "speech_end", "meta": meta or {}}, remember=False)


class Brain(LiveInteractions, Autonomy, AICompanion, Games):
    def __init__(self) -> None:
        self.llm = LLM()
        self.speaker = Speaker()
        self.interact = InteractEngine()
        self.last_speak_at = 0.0
        self.pending_enter: list[str] = []
        self.pending_enter_at = 0.0
        self._history: list[dict] = []
        self.paused = False
        self._welcome_count = 0     # 欢迎语播报次数
        self._speak_count = 0       # 总播报次数
        self._last_welcome_at = 0.0
        self._follow_thanked: set[str] = set()
        self._last_follow_at = 0.0
        self._follow_pending = 0
        self._speech_generation = 0
        self._pending_replies: list[Event] = []
        self._reply_ready = asyncio.Event()
        self._response_task: asyncio.Task | None = None
        self._response_score = 0.0
        self._ambient_tasks: list[asyncio.Task] = []
        self._running = False
        self._resume_music_id: str | None = None
        self._music_was_paused = False
        self.likes = LikeGoal()
        from ..world import WorldDirector
        self.world = WorldDirector(Effective.world())
        from ..fishing import FishingGame
        self.fishing = FishingGame(Effective.fishing())
        self.init_live_interactions()
        self.init_autonomy()
        self.init_games()
        self.init_companion()
        self._fishing_scored_round = ""
        self._fishing_scored_net = ""
        self.likes.configure(Effective.like_goal(), Effective.interact()["enabled"])
        self._likes_dirty = False
        self.last_activity_at = time.time()
        self._last_idle_action_at = 0.0
        self._idle_action_index = 0
        self._last_idle_motion = ""
        self._idle_topic_index = 0
        self._idle_invite_index = 0
        self._last_idle_invite_at = 0.0
        self._idle_preparing = False
        self.last_operation = {}
        self.last_interaction = None
        self._control_generation = 0
        self.llm.context_provider = self.runtime_context

    def runtime_context(self, now=None):
        from .capabilities import runtime_context
        return runtime_context(self, hub, queue, now, metrics)

    def _remember_interaction(self, intent, result):
        self.last_interaction = intent
        self.last_operation = {"action": intent.action, "motion": intent.motion, "ok": True, "result": result}

    async def _intent_error(self, intent, ev, text, quick=False):
        self.last_operation = {"action": intent.action, "motion": intent.motion, "ok": False, "result": text}
        if quick:
            await hub.broadcast({"type": "subtitle", "text": text}, remember=False)
        else:
            await self._speak(text, ev, self._score(ev))
        return True

    def _start_ambient(self) -> None:
        self._ambient_tasks = [asyncio.create_task(loop(), name=f"brain:{loop.__name__}")
                               for loop in (self._idle_loop, self._idle_action_loop, self._enter_flush_loop)]

    async def set_paused(self, paused: bool) -> None:
        if self.paused == paused:
            return
        self.paused = paused
        self.pause_autonomy(paused)
        self.sync_activity_pauses()
        if paused:
            self._music_was_paused = queue.paused
            self._resume_music_id = queue.current.id if queue.playing and queue.current else None
            queue.pause()
            self._pending_replies.clear()
            self._reply_ready.clear()
            self.pending_enter.clear()
            self.pending_enter_at = 0.0
            self._follow_pending = 0
            for task in self._ambient_tasks:
                task.cancel()
            await asyncio.gather(*self._ambient_tasks, return_exceptions=True)
            self._ambient_tasks.clear()
            await self.stop_speech()
        else:
            queue.paused = self._music_was_paused
            if (self._resume_music_id and queue.current and queue.current.id == self._resume_music_id
                    and Effective.music()["enabled"]):
                queue.resume()
            self._resume_music_id = None
            self.last_speak_at = time.time()
            if self._running:
                self._start_ambient()
        await hub.broadcast({"type": "state", "paused": paused}, remember=False)
        await self._push_music()

        await self._push_likes()
        await self._push_fishing()
        await self._push_gift_show()
        await self.push_games()

    async def stop_speech(self) -> None:
        self._speech_generation += 1
        await self.interrupt_companion()
        await self.stop_live_voice()
        self.speaker.interrupt()
        task = self._response_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await hub.broadcast({"type": "speech_interrupt"}, remember=False)

    # ---------------------------------------------------------------- 过滤打分
    def _allow(self, ev: Event) -> bool:
        if self.paused:
            return False
        if ev.kind != "chat":
            return True

        f = Effective.filters()
        t = ev.text.strip()
        if not t or len(t) > f["max_input_chars"]:
            return False
        if any(w in t for w in f["banned_words"]):
            log.info("命中敏感词，忽略：%s", t[:20])
            return False

        return True

    def _score(self, ev: Event) -> float:
        s = Effective.scoring()
        base = {
            "gift": float(s.get("gift", 6.0)),
            "follow": float(s.get("follow", 3.0)),
            "chat": float(s.get("chat", 1.0)),
            "enter": float(s.get("enter", 0.4)),
            "like": float(s.get("like", 0.1)),
            "system": 0.0,
        }.get(ev.kind, 0.0)
        if ev.kind == "gift":
            base += min(ev.gift_value, 3000) / 300.0
        if ev.kind == "chat":
            if QUESTION.search(ev.text):
                base += float(s.get("question_bonus", 1.2))
            if len(ev.text) >= 8:
                base += 0.3
            if re.search(r"主播|大肥鱼|鱼", ev.text):
                base += float(s.get("mention_bonus", 0.5))
            # 纯表情/纯符号弹幕没有回答价值，降权
            if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", ev.text):
                base *= 0.2
        return base

    # ---------------------------------------------------------------- 主循环
    async def run(self) -> None:
        hub.stats["started_at"] = time.time()
        self._running = True
        await self.configure_live_features()
        if not self.paused:
            self._start_ambient()
        tasks = [asyncio.create_task(loop(), name=f"brain:{loop.__name__}") for loop in
                 (self._stats_loop, self._music_loop, self._reply_loop, self._like_loop)]
        log.info("大脑已启动（LLM=%s, TTS=%s）", self.llm.status_text,
                 self.speaker.tts.name)

        try:
            while True:
                ev = await hub.queue.get()
                try:
                    handled = await self._publish_event(ev)
                    if not handled and self._allow(ev):
                        while len(self._pending_replies) >= 2000 and not self.paused:
                            await asyncio.sleep(0.1)
                        if self.paused:
                            continue
                        self._pending_replies.append(ev)
                        self._pending_replies.sort(key=self._score, reverse=True)
                        self._reply_ready.set()
                except Exception as exc:
                    log.exception("处理事件失败：%s", exc)
                finally:
                    hub.queue.task_done()
        finally:
            self._running = False
            tasks.extend(self._ambient_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.stop_live_voice()
            await self.close_games()

    async def handle(self, ev: Event) -> None:
        handled = await self._publish_event(ev)
        if not handled and self._allow(ev):
            await self._respond(ev)

    async def _publish_event(self, ev: Event) -> bool:
        if ev.kind == "gift" and not metrics.add_gift(ev):
            return True
        self.last_activity_at = time.time()
        await hub.broadcast({"type": "event", "event": ev.to_dict()})
        if ev.kind in ("chat", "gift", "follow") and (ev.kind != "chat" or ev.text.strip()):
            await self.interrupt_companion()
            if self.note_audience():
                await self._push_fishing()
        if ev.kind == "like":
            self.likes.configure(Effective.like_goal(), Effective.interact()["enabled"])
            previous_total = self.likes.total
            previous_day = self.likes.day
            self.likes.add(ev)
            self._likes_dirty = True
            delta = max(0, self.likes.total - (previous_total if self.likes.day == previous_day else 0))
            if delta > 0:
                await self.interrupt_companion()
            if delta > 0 and self.note_audience():
                await self._push_fishing()
            if not self.paused and self.fishing.active and not self.fishing_result_speaking():
                revision = self.fishing.revision
                user_count = ev.like_count if ev.like_count is not None else ev.gift_count
                attributed = min(delta, max(0, user_count or 0))
                user = {"id": ev.user_id, "name": ev.user_name}
                if attributed:
                    self.fishing.add_likes(attributed, ev.id, user=user)
                if delta > attributed:
                    self.fishing.add_likes(delta - attributed, ev.id + ":room")
                if self.fishing.revision != revision:
                    await self._sync_fishing()

        # 统计先记：礼物榜/token 消耗要包含被过滤掉的事件，否则数据会少
        if ev.kind == "gift":
            net_gift = False
            if not self.paused and not self.games.active and Effective.interact()["enabled"]:
                donor = {"id": ev.user_id, "name": ev.user_name, "avatar": ev.avatar,
                         "tribute": metrics.gift_board.tribute(ev.to_dict())}
                net_value = 0 if ev.gift_value_estimated else ev.gift_value
                net_gift = self.fishing.add_net_gift(ev.gift_count or 1, net_value, donor, ev.id, ev.gift_name)
                if net_gift or self.fishing.add_gift(ev.gift_count or 1, donor, ev.id):
                    await self._sync_fishing()
            await hub.broadcast({"type": "gift_ranking", "ranking": metrics.gift_board.snapshot()}, remember=False)
            if (not self.paused and Effective.interact()["enabled"] and (net_gift or not Effective.gift_show()["enabled"])
                    and "墨镜" in (ev.gift_name or "").replace("鏡", "镜")):
                from ..wardrobe import wear
                try:
                    await wear("cool", hub, event_id=ev.id)
                except OSError:
                    log.exception("墨镜装扮保存失败，本次仍显示配饰")
                    await hub.broadcast({"type": "costume", "name": "cool", "event_id": ev.id}, remember=False)
            if not net_gift and self.queue_gift_show(ev):
                if self.tick_gift_show():
                    await self.sync_gift_show()
                else:
                    await self._push_gift_show()
            if net_gift:
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return True
        if ev.kind == "chat" and self._allow(ev) and Effective.interact()["enabled"]:
            from .intent import Intent, local_intent, video_requested
            intent = local_intent(ev.text)
            if intent.action == "game":
                await self._execute_intent(ev, intent, quick=True)
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return True
            if (intent.action == "chat" or self.game_answer_matches(ev.text)) and await self.game_chat(ev):
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return True
            if intent.action == "fishing":
                if intent.motion in ("start", "cancel", "dive", "relocate", "spot_cloud", "spot_reeds", "spot_reef", "enter_dedicated", "exit_dedicated"):
                    self._control_generation += 1
                await self._execute_intent(ev, intent, quick=True)
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return True
            if intent.motion in ("activity:wake", "activity:park", "activity:dismount", "move:stop") or (intent.action == "control" and intent.motion == "cancel"):
                self._control_generation += 1
                await self._execute_intent(ev, intent, quick=True)
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return True
            stage = Effective.stage()
            if stage["video_enabled"] and video_requested(ev.text, stage["video_keywords"]):
                await self._execute_intent(ev, Intent("video"), quick=True)
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return True
        return False

    async def _push_likes(self):
        self._likes_dirty = False
        await hub.broadcast({"type": "like_goal", "goal": self.likes.snapshot()}, remember=False)

    async def _like_loop(self):
        while True:
            await asyncio.sleep(0.25)
            if self.tick_gift_show():
                await self.sync_gift_show()
            await self.games_tick()
            await self.ensure_dedicated_fishing()
            if (self.dedicated_fishing and self.fishing.phase in ("waiting", "hooked")
                    and self.likes.pending and not self.gift_show.active and not self.games.active and not self.paused):
                from ..like_goal import STUNTS
                duration = sum(STUNTS[item["motion"]]["duration"] + self.likes.cooldown for item in self.likes.pending)
                await self.interrupt_fishing("点赞达标整活", duration + 1)
            if not self.fishing_result_speaking() and self.fishing.tick():
                await self._sync_fishing()
            changed = self.likes.tick() if self.likes.paused_at is None else False
            if changed or self._likes_dirty:
                await self._push_likes()
            await self.live_voice_tick()

    async def _push_fishing(self):
        await hub.broadcast({"type": "fishing", "fishing": self.fishing.snapshot()}, remember=False)

    async def _sync_fishing(self):
        if (self.fishing.phase == "net_caught" and self.fishing.net
                and self._fishing_scored_net != self.fishing.net["id"]):
            haul = self.fishing.net
            changed = False
            for index, fish in enumerate(haul["fish"]):
                identifier = "net:" + haul["id"] + ":" + str(index)
                changed |= bool(metrics.add_fish_encounter(identifier, fish))
                changed |= bool(metrics.add_catch(identifier, fish, haul["user"]))
            self._fishing_scored_net = haul["id"]
            if changed:
                await hub.broadcast({"type": "stats", "stats": metrics.snapshot(Effective.pricing())}, remember=False)
        if self.fishing.fish and metrics.add_fish_encounter(self.fishing.round_id, self.fishing.fish):
            await hub.broadcast({"type": "stats", "stats": metrics.snapshot(Effective.pricing())}, remember=False)
        if self.fishing.phase == "caught" and self._fishing_scored_round != self.fishing.round_id:
            metrics.add_catch(self.fishing.round_id, self.fishing.fish, self.fishing.winner)
            self._fishing_scored_round = self.fishing.round_id
            await hub.broadcast({"type": "stats", "stats": metrics.snapshot(Effective.pricing())}, remember=False)
        if not self.fishing.active:
            await self.ensure_dedicated_fishing()
        self.sync_activity_pauses()
        await hub.broadcast({"type": "world", "world": self.world.snapshot()}, remember=False)
        await self._push_likes()
        await self._push_fishing()

    async def fishing_command(self, operation, ev=None, manual=False):
        if self.paused:
            raise ValueError("直播已暂停，请先恢复直播")
        if manual:
            self.note_audience()
        if operation in ("enter_dedicated", "exit_dedicated"):
            return await self.set_fishing_mode(operation == "enter_dedicated", ev, manual)
        if self.gift_show.active:
            raise ValueError("正在完整播放礼物演出，结束后继续钓鱼")
        if not manual and not Effective.interact()["enabled"]:
            raise ValueError("人物互动暂时关闭了")
        self.fishing.config = Effective.fishing()
        try:
            if operation == "start":
                if self.fishing.phase == "interacting":
                    self.fishing.resume()
                    return "已回到钓鱼台，继续本轮钓鱼"
                self.fishing.start(self.world.describe()["scene_label"])
                self.world.pause(True)
                self.likes.pause(True)
                await hub.broadcast({"type": "reaction_clear"}, remember=False)
                label = "已进入钓鱼高台，等咬钩后发“拉钩”或点赞协力收线"
            elif operation == "dive":
                if not self.fishing.active:
                    self.fishing.start(self.world.describe()["scene_label"])
                await self._sync_fishing()
                self.fishing.start_dive()
                await hub.broadcast({"type": "reaction_clear"}, remember=False)
                label = "放下鱼竿，来一段花式跳水！游几圈再回台重新抛竿"
            elif operation == "pull":
                if self.fishing_result_speaking():
                    raise ValueError("正在播报本轮鱼获结果，下一竿再拉钩")
                user = {"id": ev.user_id, "name": ev.user_name} if ev and not manual else None
                self.fishing.pull(user=user, event_id=ev.id if ev else "")
                label = "已协力收线"
            elif operation in ("relocate", "spot_cloud", "spot_reeds", "spot_reef"):
                self.fishing.relocate("next" if operation == "relocate" else operation[5:])
                label = self.fishing.snapshot()["label"]
            elif operation in ("loosen", "feed", "cheer"):
                user = {"id": ev.user_id, "name": ev.user_name} if ev and not manual else None
                self.fishing.fish_action(operation, user, ev.id if ev else "")
                label = {"loosen": "鱼线松开了，上鱼进度下降，快发拉钩追回来！",
                         "feed": "撒一把鱼饵引鱼，缩短这次等待时间",
                         "cheer": "加油护线！接下来十秒每次拉钩的基础脱钩概率减半"}[operation]
            elif operation == "cancel":
                if self.dedicated_fishing:
                    raise ValueError("当前是常驻钓鱼。管理员或获授权榜单前三可发“退出常驻钓鱼”；主播也可在场景导演退出或设置中关闭")
                if not self.fishing.active:
                    raise ValueError("现在没有在钓鱼")
                self.fishing.cancel()
                label = "已收竿，返回原场景"
            else:
                raise ValueError("未知钓鱼指令")
        finally:
            await self._sync_fishing()
        return label

    async def cancel_fishing(self, force=False):
        if self.dedicated_fishing and not force:
            raise ValueError("当前是钓鱼专用直播，请先在设置中关闭常驻钓鱼")
        if self.fishing.active:
            self.fishing.cancel()
            await self._sync_fishing()

    async def _reply_loop(self) -> None:
        try:
            while True:
                await self._reply_ready.wait()
                if not self._pending_replies:
                    self._reply_ready.clear()
                    continue
                ev = self._pending_replies.pop(0)
                if self.paused:
                    continue
                self._response_score = self._score(ev)
                self._response_task = asyncio.create_task(self._respond(ev), name="brain:response")
                await asyncio.gather(self._response_task, return_exceptions=True)
                if not self._response_task.cancelled() and self._response_task.exception():
                    log.error("回复处理失败：%s", self._response_task.exception())
                self._response_task = None
                self._response_score = 0.0
        finally:
            if self._response_task:
                self._response_task.cancel()
                await asyncio.gather(self._response_task, return_exceptions=True)
                self._response_task = None
            self._pending_replies.clear()

    async def _respond(self, ev: Event) -> None:
        if self.paused:
            return

        pace = Effective.pace()

        # ---- 点歌优先处理：观众指令/礼物点歌先入队，再决定怎么接话 ----
        if ev.kind == "gift":
            handled = await self._maybe_gift_song(ev)
            if handled:
                return
            if ev.id in self.gift_show.seen and Effective.gift_show()["enabled"]:
                return
        if ev.kind == "chat":
            handled = await self._chat_intent(ev)
            if handled:
                ev.answered = True
                await hub.broadcast({"type": "answered", "id": ev.id})
                return

        # ---- 互动规则优先：命中就给画面反馈 ----
        hit = self.interact.match(ev) if ev.kind != "chat" else None
        if hit:
            # 有台词的规则直接接管播报（说完即结束，不走普通流程，避免双重回复）
            if hit.reply:
                await self._fire(hit, ev)
                return
            # 没台词只做视觉反馈，把发言权还给正常流程
            if hit.has_visual:
                await self._visual(hit)

        if ev.kind == "enter":
            if pace["announce_enter"]:
                self.pending_enter.append(ev.user_name)
                if not self.pending_enter_at:
                    self.pending_enter_at = time.time()
            return
        if ev.kind == "like":
            if pace["announce_like"] and random.random() < 0.25:
                await self._speak(render_line("like_thanks", name=ev.user_name, count=ev.gift_count), ev, 0.1)
            return

        score = self._score(ev)

        while self.speaker.busy or (self._live_voice_task and not self._live_voice_task.done()):
            if self.paused:
                return
            await asyncio.sleep(0.05)
        gap = time.time() - self.last_speak_at
        if gap < pace["min_speak_gap"]:
            await asyncio.sleep(pace["min_speak_gap"] - gap)

        tpl = Effective.templates()
        # 「有模型时优先让模型说」：开着就把模板句换成模型即兴生成
        use_llm_voice = self.llm.active and tpl["prefer_llm"]

        if ev.kind == "gift":
            # 规则没命中才走默认感谢语
            if not (tpl["enabled"] and tpl["gift"]):
                return                      # 模板关掉：只记数据，不说话
            if use_llm_voice:
                said = await self._llm_line(
                    f"观众「{ev.user_name}」送出了 {ev.gift_name}x{ev.gift_count}，"
                    f"用你自己的语气道个谢，20 字内。", ev)
                if said:
                    await self._speak(said, ev, score, motion="happy")
                    return
            text = self._gift_line(ev)
            await self._speak(text, ev, score, motion="happy")
            return
        if ev.kind == "follow":
            if not pace["announce_follow"]:
                return
            if not (tpl["enabled"] and tpl["follow"]):
                return
            # 同一个人的关注只谢一次（抖音会重复推 SocialMessage）
            if ev.user_name in self._follow_thanked:
                return
            self._follow_thanked.add(ev.user_name)
            if len(self._follow_thanked) > 2000:
                self._follow_thanked.clear()
            # 大直播间关注刷得快，全谢一遍就只会说"谢谢关注"了
            if time.time() - self._last_follow_at < 20.0:
                self._follow_pending += 1
                return
            self._last_follow_at = time.time()
            if use_llm_voice:
                said = await self._llm_line(
                    f"观众「{ev.user_name}」关注了你，自然地表示感谢，20 字内。", ev)
                if said:
                    await self._speak(said, ev, score, motion="happy")
                    return
            pool = store.lines("follow") or ["谢谢{name}的关注"]
            tpl_line = random.choice(pool)
            await self._speak(tpl_line.format(name=ev.user_name), ev, score, motion="happy")
            return

        # 聊天 → LLM
        await self._chat(ev, score)

    # ---------------------------------------------------------------- 点歌
    async def _music_loop(self) -> None:
        """盯着队列：没在放就自动开下一首，放完自动切。

        前端 <audio> 播放结束时会上报 music_ended，这里也有超时兜底——
        万一声卡异常/页面刷新导致 ended 事件没到，超时也能继续。
        """
        while True:
            try:
                await asyncio.sleep(3)
                cfg = Effective.music()
                if queue.enforce_policy():
                    await self._push_music()
                if self.paused or not cfg["enabled"] or not cfg["autoplay"]:
                    continue
                if queue.playing and queue.current:
                    # 超时兜底：曲目时长 + 60 秒还没收到 ended，认为卡住了
                    dur = queue.current.duration or 240
                    if time.time() - queue.started_at > dur + 60:
                        log.info("曲目超时，自动切下一首")
                        queue.next_track()
                        await self._push_music()
                        continue
                if not queue.playing and queue.pending():
                    await self.music_autoplay()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("音乐循环异常", exc_info=True)

    async def _push_music(self) -> None:
        """把播放器状态推给直播画面。"""
        queue.enforce_policy()
        await hub.broadcast({"type": "music", "music": queue.snapshot()}, remember=False)

    async def _chat_intent(self, ev: Event) -> bool:
        from .intent import local_intent

        reason = blocked_reason(ev.text)
        if reason and re.search(r"播|放|唱|听|聽|点|點|来一首|來一首", ev.text):
            await self._speak(reason, ev, self._score(ev))
            return True
        generation = self._speech_generation
        control_generation = self._control_generation
        local = local_intent(ev.text)
        intent = local if local.action in ("world", "motion", "stunt", "costume", "control", "status", "help", "fishing") else await self.llm.classify_intent(ev.text)
        if self.paused or generation != self._speech_generation:
            return True
        intent = intent if intent is not None else local
        if control_generation != self._control_generation and intent.action != "chat":
            await hub.broadcast({"type": "subtitle", "text": "人物刚收到停止或下车指令，较早的操作已取消"}, remember=False)
            return True
        log.info("[意图/%s] %s → %s %s", intent.source, ev.text[:40], intent.action,
                 intent.query or intent.motion)
        return await self._execute_intent(ev, intent)

    async def _execute_intent(self, ev, intent, quick=False):
        from .intent import COSTUMES, MOTIONS
        from .capabilities import help_line, status_line

        if intent.action == "game":
            try:
                label = await self.game_command(intent.motion, ev)
            except ValueError as error:
                return await self._intent_error(intent, ev, str(error), True)
            self._remember_interaction(intent, label)
            await hub.broadcast({"type": "subtitle", "text": label}, remember=False)
            return True
        if self.games.active and intent.action in ("world", "motion", "costume", "video", "control", "fishing"):
            return await self._intent_error(intent, ev, "小游戏正在进行，开局者或管理员可发‘结束小游戏’后切换玩法", True)

        if self.gift_show.active and intent.action in ("world", "motion", "costume", "video", "control"):
            return await self._intent_error(intent, ev, "正在播放专属礼物演出，表演结束后再来互动吧", True)

        if intent.action == "fishing":
            try:
                label = await self.fishing_command(intent.motion, ev)
            except (ValueError, OSError) as error:
                return await self._intent_error(intent, ev, str(error), True)
            self._remember_interaction(intent, label)
            if intent.motion != "pull":
                await hub.broadcast({"type": "subtitle", "text": label}, remember=False)
            return True
        if intent.action in ("status", "help"):
            text = (status_line if intent.action == "status" else help_line)(self.runtime_context())
            await self._speak(text, ev, self._score(ev))
            return True
        if intent.action == "control":
            if not Effective.interact()["enabled"]:
                return await self._intent_error(intent, ev, "人物互动暂时关闭了", quick)
            if intent.motion == "repeat":
                if self.last_interaction is None:
                    return await self._intent_error(intent, ev, "还没有可以重复的成功互动", quick)
                return await self._execute_intent(ev, self.last_interaction, quick)
            if intent.motion != "cancel":
                return await self._intent_error(intent, ev, "未知人物控制命令", quick)
            if self.paused:
                return await self._intent_error(intent, ev, "直播已暂停，请先恢复直播", quick)
            try:
                if self.dedicated_fishing and self.fishing.phase == "interacting":
                    self.fishing.resume()
                    await self._sync_fishing()
                elif self.dedicated_fishing and self.fishing.phase == "diving":
                    raise ValueError("正在水中游泳，回到台上后继续钓鱼")
                elif not self.dedicated_fishing:
                    await self.cancel_fishing()
            except ValueError as error:
                return await self._intent_error(intent, ev, str(error), quick)
            result = "人物动作与整活已停止"
            if self.world.config.get("enabled", True) and self.world.config.get("chat_control", True):
                self.world.command("move", "stop")
                await hub.broadcast({"type": "world", "world": self.world.snapshot()}, remember=False)
            elif self.world.config.get("enabled", True) and self.world.describe()["activity"] != "idle":
                result = "短动作与整活已停止；场景弹幕控制已关闭，原场景行为继续"
            self.likes.active = None
            self.likes.pending.clear()
            self.likes.ready_at = 0
            await self._push_likes()
            await hub.broadcast({"type": "reaction_clear"}, remember=False)
            self.last_operation = {"action": "control", "motion": "cancel", "ok": True, "result": result}
            await hub.broadcast({"type": "subtitle", "text": result}, remember=False)
            return True
        if intent.action in ("song", "next", "stop", "resume", "what"):
            request = songcmd.SongRequest("add" if intent.action == "song" else intent.action, intent.query)
            return await self._maybe_song_cmd(ev, request)
        if intent.action == "world":
            if not Effective.interact()["enabled"]:
                return await self._intent_error(intent, ev, "人物互动暂时关闭了", quick)
            opened_interruption = False
            try:
                kind, value = intent.motion.split(":", 1)
                if self.fishing.active:
                    from ..world import COMMANDS
                    if kind not in COMMANDS or value not in COMMANDS[kind]:
                        raise ValueError("未知场景指令")
                    if not self.world.config.get("enabled", True):
                        raise ValueError("请先在设置中开启三维场景")
                    if not self.world.config.get("chat_control", True):
                        raise ValueError("弹幕场景控制暂时关闭了")
                    if self.dedicated_fishing:
                        opened_interruption = self.fishing.phase != "interacting"
                        await self.interrupt_fishing("场景互动", 20)
                    else:
                        await self.cancel_fishing()
                label = self.world.command(kind, value)
                if self.fishing.phase == "interacting":
                    self.fishing.interrupt(label, max(20, self.world.describe()["remaining_seconds"] + 10))
                    await self._push_fishing()
            except ValueError as error:
                if opened_interruption and self.fishing.resume():
                    await self._sync_fishing()
                return await self._intent_error(intent, ev, str(error), quick)
            self._remember_interaction(intent, label)
            await hub.broadcast({"type": "world", "world": self.world.snapshot()}, remember=False)
            await hub.broadcast({"type": "subtitle", "text": f"{ev.user_name} · {label}"}, remember=False)
            return True
        if intent.action == "stunt":
            if not Effective.interact()["enabled"]:
                return await self._intent_error(intent, ev, "人物互动暂时关闭了", quick)
            try:
                await self.play_stunt(intent.motion)
            except ValueError as error:
                return await self._intent_error(intent, ev, str(error), quick)
            self._remember_interaction(intent, "整活请求已接收，按顺序播放")
            return True
        if intent.action in ("costume", "video"):
            if not Effective.interact()["enabled"]:
                return await self._intent_error(intent, ev, "人物互动暂时关闭了", quick)
            elif intent.action == "costume":
                from ..wardrobe import wear
                try:
                    worn = await wear(intent.motion, hub)
                except (ValueError, OSError) as error:
                    return await self._intent_error(intent, ev, str(error), quick)
                self._remember_interaction(intent, "已换上" + COSTUMES[worn])
                await self._speak(render_line("costume_ack", costume=COSTUMES[worn]), ev, self._score(ev), motion="" if self.fishing.active else "happy")
            elif Effective.stage()["video_enabled"]:
                try:
                    await self.interrupt_fishing("奶蛙短片", 9)
                except ValueError as error:
                    return await self._intent_error(intent, ev, str(error), quick)
                await hub.broadcast({"type": "video", "clip": "milk-frog", "event_id": ev.id}, remember=False)
                self._remember_interaction(intent, "奶蛙短片播放请求已发送")
            else:
                return await self._intent_error(intent, ev, "互动视频暂时关闭了", quick)
            return True
        if intent.action == "motion":
            if not Effective.interact()["enabled"]:
                return await self._intent_error(intent, ev, "人物互动暂时关闭了", quick)
            active = hub.performance.snapshot()
            if active and active["priority"] > 2:
                return await self._intent_error(intent, ev, "正在播放控制台指定的动作，请稍后再试", quick)
            from ..native.actions import ACTIONS
            try:
                await self.interrupt_fishing(MOTIONS[intent.motion], ACTIONS[intent.motion]["duration"] + .5)
            except ValueError as error:
                return await self._intent_error(intent, ev, str(error), quick)
            await hub.broadcast({"type": "reaction", "motion": intent.motion, "event_id": ev.id,
                                 "source": intent.source}, remember=False)
            self._remember_interaction(intent, "已触发" + MOTIONS[intent.motion])
            await self._speak(render_line("action_ack", action=MOTIONS[intent.motion]), ev, self._score(ev))
            return True
        if intent.source == "local" and not re.search(r"不要|别|不想|不用|不许|不能|不准", ev.text):
            hit = self.interact.match(ev, ignore_cooldown=True)
            if hit:
                if hit.reply:
                    await self._fire(hit, ev)
                    return True
                if hit.has_visual:
                    await self._visual(hit)
        return False

    async def _maybe_song_cmd(self, ev: Event, req=None) -> bool:
        """弹幕里的点歌/切歌指令。返回 True 表示这条已处理，别再走闲聊。"""
        cfg = Effective.music()
        if not cfg["enabled"]:
            if req is not None:
                await self._speak("音乐功能暂时关闭了", ev, self._score(ev))
                return True
            return False
        req = req or songcmd.parse_request(ev.text)
        if not req:
            return False

        if req.action == "next":
            cur = queue.next_track()
            await self._push_music()
            await self._speak(f"好，切歌。{('下一首是《' + cur.title + '》') if cur else '队列里没歌了'}",
                             ev, self._score(ev), motion="happy")
            return True

        if req.action == "stop":
            queue.pause()
            await self._push_music()
            await self._speak("行，音乐先停了", ev, self._score(ev))
            return True

        if req.action == "resume":
            if queue.resume():
                await self._push_music()
                await self._speak("继续放", ev, self._score(ev))
            else:
                await self._speak("现在没有歌，点一首吧", ev, self._score(ev))
            return True

        if req.action == "what":
            await self._speak(songcmd.describe_current(), ev, self._score(ev))
            return True

        # action == add
        ok, line = await self._enqueue(req.query, ev.user_name)
        await self._push_music()
        await self._speak(line, ev, self._score(ev), motion="happy" if ok else "talk")
        return True

    async def _enqueue(self, query: str, user: str, gift_value: int = 0, from_ai: bool = False) -> tuple[bool, str]:
        """解析并加入队列，返回 (成功, 要说的话)。"""
        cfg = Effective.music()
        if not cfg["enabled"]:
            return False, "音乐功能没开"
        reason = query_reason(query, from_ai)
        if reason:
            return False, reason
        try:
            info = await music_resolve(query, cookie=cfg["cookie"],
                                       providers=cfg["providers"])
        except Exception:
            log.debug("点歌解析失败", exc_info=True)
            info = None
        if not info:
            return False, f"《{query}》没找到符合歌曲规则且能播放的版本，换一首试试"

        reason = query_reason(query, from_ai) or playback_reason(info, from_ai)
        if reason:
            return False, reason
        track = make_track({**info, "request_query": query}, user=user, gift_value=gift_value, from_ai=from_ai)
        ok, why = queue.add(track)
        if not ok:
            return False, why

        n = len(queue.pending())
        if n <= 1:
            return True, f"好，放《{track.title}》"
        return True, f"《{track.title}》排上了，前面还有 {n - 1} 首"

    async def music_autoplay(self) -> None:
        """队列有歌但没在放时自动开播（前端会真的开始播放）。"""
        cfg = Effective.music()
        if not cfg["enabled"] or not cfg["autoplay"]:
            return
        if self.paused or queue.paused or queue.playing or not queue.pending():
            return
        cur = queue.next_track()
        await self._push_music()
        if cur:
            log.info("自动播放：%s - %s", cur.title, cur.by)

    async def ai_song(self, query: str) -> tuple[bool, str]:
        """AI 主动点歌（放队尾，不跟观众抢）。"""
        cfg = Effective.music()
        if not cfg["enabled"] or not cfg["ai_dj"]:
            return False, "AI 点歌没开"
        return await self._enqueue(query, store.persona_name, gift_value=0, from_ai=True)

    async def _maybe_gift_song(self, ev: Event) -> bool:
        """送礼点歌：礼物价值够高就给插队权。

        判定顺序：
          1. 弹幕文本本身就是点歌指令（送礼同时说"点歌 X"）
          2. 否则把礼物名当成歌名试一次（送"晴天"这种礼物名的情况很少，
             但主播可能配了自定义礼物），失败就算了，不影响正常谢礼。
        """
        cfg = Effective.music()
        if not cfg["enabled"] or not cfg["gift_priority"]:
            return False
        if ev.gift_value < cfg["gift_min"]:
            return False

        query = ""
        req = songcmd.parse_request(ev.text or "")
        if req and req.action == "add":
            query = req.query
        if not query:
            return False      # 没有明确点歌意图就不抢谢礼流程

        ok, line = await self._enqueue(query, ev.user_name, gift_value=ev.gift_value)
        await self._push_music()
        if ok:
            await self._speak(f"感谢{ev.user_name}的{ev.gift_name}！{line}，给你优先安排",
                             ev, self._score(ev), motion="happy")
            ev.answered = True
            return True
        await self._speak(f"谢谢{ev.user_name}的礼物。{line}", ev, self._score(ev))
        ev.answered = True
        return True

    # ---------------------------------------------------------------- 话术
    async def _llm_line(self, prompt: str, ev: Event | None = None, limit: int = 26) -> str:
        """让模型即兴说一句。失败或空返回时给空串，由调用方决定要不要退回模板。"""
        if not self.llm.active:
            return ""
        try:
            return await complete_utterance(self.llm.stream(prompt, self._history,
                                            name=(ev.user_name if ev else "")), limit)
        except Exception:
            log.debug("模型即兴生成失败", exc_info=True)
            return ""

    def _gift_line(self, ev: Event) -> str:
        pool = store.lines("thanks_big") if ev.gift_value >= 100 else store.lines("thanks_small")
        if not pool:
            pool = ["谢谢{name}的{gift}"]
        tpl = random.choice(pool)
        return tpl.format(name=ev.user_name, gift=f"{ev.gift_name}x{ev.gift_count}")

    def _prompt(self, ev: Event) -> str:
        # 只把弹幕原文交给模型（别混入指令，否则离线兜底会把指令复读出来）
        return ev.text

    async def _chat(self, ev: Event, score: float) -> None:
        generation = self._speech_generation
        pace = Effective.pace()
        limit = max(20, int(pace["max_reply_chars"]))
        motion = "talk"
        text = await complete_utterance(self.llm.stream(self._prompt(ev), self._history, name=ev.user_name),
                                       limit if self.llm.active else None)
        if not text or self.paused or generation != self._speech_generation:
            return
        # 标明这句是模型生成的还是离线兜底，方便后台核对"AI 到底有没有在思考"
        source = "模型" if self.llm.active else "离线"
        log.info("[%s] 弹幕「%s」→ 回复「%s」", source, (ev.text or "")[:20], text[:30])
        self._history.append({"role": "user", "content": ev.text})
        self._history.append({"role": "assistant", "content": text})
        self._history = self._history[-8:]

        await self._speak(text, ev, score, motion=motion)
        ev.answered = True
        await hub.broadcast({"type": "answered", "id": ev.id})

    async def _speak(self, text: str, ev: Event | None, score: float, motion: str = "talk") -> None:
        if self.paused:
            return
        if self._live_voice_task and self._live_voice_task is not asyncio.current_task():
            await self.stop_live_voice()
        self.last_speak_at = time.time()
        self._speak_count += 1
        hub.roll_day()
        hub.stats["replies"] += 1
        metrics.add_reply()

        # 兜底语音开关：关掉后字幕照显示，但不出声（后台想静音试运行很有用）
        fb = Effective.fallback()
        silent_fallback = (not self.llm.active) and (not fb["offline_voice"]) and not (ev and ev.kind == "system")

        # 冷场话题没有触发事件，ev 为 None，这里要防一下
        meta = {"event": ev.to_dict()} if ev is not None else {}

        if motion:
            await hub.broadcast({"type": "motion", "name": motion})
        if silent_fallback:
            # 走"无语音"路径：只推字幕，不生成 TTS
            await hub.broadcast({"type": "subtitle", "text": text}, remember=False)
            await hub.broadcast({"type": "speech_start", "silent": True}, remember=False)
            await asyncio.sleep(min(2.5, max(0.8, len(text) * 0.06)))
            await hub.broadcast({"type": "speech_end"}, remember=False)
        else:
            await self.speaker.say(text, meta=meta, score=score)
        self.last_speak_at = time.time()
        await self._push_stats()

    # ---------------------------------------------------------------- 数据统计
    async def _stats_loop(self) -> None:
        """每 10 秒推一次统计，保证 token/礼物榜即使没人说话也在更新。"""
        while True:
            try:
                await asyncio.sleep(10)
                await self._push_stats()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.debug("统计推送失败", exc_info=True)

    async def _push_stats(self) -> None:
        """把统计快照推给直播画面（token/对话数/礼物榜 + 抖音真实人数）。"""
        payload = {
            "type": "stats",
            "stats": metrics.snapshot(Effective.pricing()),
            "eventStats": dict(hub.stats),
        }
        room = self._room_stats()
        if room:
            payload["room"] = room
        await hub.broadcast(payload, remember=False)

    def _room_stats(self) -> dict | None:
        """从接入层取抖音真实在线/累计观看人数。

        注意这跟「有几个网页开着直播画面」完全是两回事，
        之前界面上显示的 online 是后者，所以数字一直是 1，用户会以为坏了。
        """
        app_ing = getattr(self, "ingestor", None)
        if app_ing is None:
            return None
        getter = getattr(app_ing, "room_stats", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    # ---------------------------------------------------------------- 互动规则
    async def _visual(self, hit) -> bool:
        """只发画面指令，不占发言权。"""
        if self.fishing.active:
            from ..native.actions import ACTIONS, RULE_ACTIONS
            motion = RULE_ACTIONS.get(hit.rule.get("id"), hit.motion) if hit.motion in ("happy", "shake") else hit.motion
            preset = ACTIONS.get(motion, ACTIONS["happy"])
            try:
                await self.interrupt_fishing(preset["label"], preset["duration"] + .5)
            except ValueError:
                return False
        payload = {
            "type": "reaction",
            "motion": hit.motion,
            "expression": pick_expression(hit.rule),
            "effect": hit.effect,
            "rule_id": hit.rule.get("id") or "",
        }
        await hub.broadcast(payload, remember=False)
        return True

    async def _fire(self, hit, ev: Event) -> None:
        """规则命中：动作/表情/特效立刻上，台词按节奏播。"""
        if not await self._visual(hit):
            return
        if not hit.reply:
            return
        gap = time.time() - self.last_speak_at
        min_gap = Effective.pace()["min_speak_gap"]
        if gap < min_gap:
            await asyncio.sleep(min_gap - gap)

        # 规则里的 reply 也是写死的台词。开了「优先让模型说」就让模型即兴生成，
        # 只有模型不可用（或关了该选项）才照念规则文案。
        tpl = Effective.templates()
        # 礼物类规则受「送礼感谢语」开关管，其它类受总开关管——
        # 之前这里只看 enabled，导致单独关掉「礼物」后仍然会念规则台词。
        allow_tpl = tpl["enabled"] and (tpl["gift"] if ev.kind == "gift" else True)
        line = hit.reply
        if tpl["prefer_llm"]:
            got = await self._llm_line(
                f"观众「{ev.user_name}」触发了「{hit.rule.get('on')}」互动，"
                f"内容是：{ev.text or (ev.gift_name or '')}。用你自己的语气回应一句。", ev)
            if got:
                line = got
            elif not allow_tpl:
                # 想优先模型、但模型没吐出东西，且模板不允许：动作照做，台词留白
                return
        elif not allow_tpl:
            # 模板不允许：动作照做，不念预设台词
            return

        await self._speak(line, ev, self._score(ev), motion=hit.motion or "talk")
        ev.answered = True
        await hub.broadcast({"type": "answered", "id": ev.id})

    # ---------------------------------------------------------------- 定时任务
    async def _enter_flush_loop(self) -> None:
        """把进场用户合并成一句话播报，避免话都被"欢迎"占满。

        大直播间每秒几十人进场，如果每次都喊欢迎，AI 就只会说欢迎语，
        真正的弹幕一条都聊不上。所以这里限制欢迎语的最小间隔。
        """
        MIN_WELCOME_GAP = 25.0   # 两次欢迎之间至少隔这么久
        MAX_WELCOME_RATIO = 0.34  # 欢迎语最多占总播报量的三分之一

        while True:
            await asyncio.sleep(1.0)
            if self.paused or not self.pending_enter or self.speaker.busy or self._response_task:
                continue
            window = Effective.pace()["enter_merge_window"]
            if time.time() - self.pending_enter_at < window:
                continue

            # 刚说完话先等一等，别让欢迎语挤掉正经回复
            if time.time() - self.last_speak_at < MIN_WELCOME_GAP:
                continue
            if time.time() - self._last_welcome_at < MIN_WELCOME_GAP:
                continue
            if self._welcome_count and self._welcome_count / max(self._speak_count, 1) > MAX_WELCOME_RATIO:
                # 欢迎语已经太多，清空积压继续聊弹幕
                self.pending_enter.clear()
                self.pending_enter_at = 0.0
                continue

            names = self.pending_enter[:]
            self.pending_enter.clear()
            self.pending_enter_at = 0.0
            if not names:
                continue

            tpl = Effective.templates()
            if not (tpl["enabled"] and tpl["welcome"]):
                continue                    # 关了欢迎语就静默，不占发言位

            uniq = list(dict.fromkeys(names))
            # 人多时直接报数量，不再念一长串名字（念不完且很吵）
            if len(uniq) == 1:
                text = ""
                if self.llm.active and tpl["prefer_llm"]:
                    text = await self._llm_line(
                        f"观众「{uniq[0]}」刚进直播间，欢迎一下，20 字内。")
                if not text:
                    pool = store.lines("welcome") or ["欢迎{a}来到直播间"]
                    text = random.choice(pool).format(a=uniq[0])
            elif len(uniq) <= 3:
                text = render_line("welcome_group", names="、".join(uniq))
            else:
                text = render_line("welcome_many", count=len(uniq))
            self._last_welcome_at = time.time()
            self._welcome_count += 1
            # 被限流压下的关注数，顺带在欢迎语里一起谢掉，别让观众觉得被无视
            if self._follow_pending:
                text += "，" + render_line("follow_batch", count=self._follow_pending)
                self._follow_pending = 0
            self.last_speak_at = time.time()
            await self.speaker.say(text, meta={"kind": "welcome"}, score=0.4)
            self.last_speak_at = time.time()

    def _ambient_available(self):
        world = self.world.describe()
        return not (self.paused or self.speaker.busy or self._response_task or self._pending_replies
                    or self.pending_enter or self.likes.active or self.fishing.active or self.gift_show.active or self.games.active
                    or self._live_voice_task or hub.performance.snapshot()
                    or (world["enabled"] and (world["moving"] or world["activity"] != "idle")))

    async def idle_action(self, manual=False):
        from ..native.actions import ACTIONS

        config = Effective.idle()
        if self.paused:
            return False
        if not manual:
            if not self._ambient_available() or self._idle_preparing or not Effective.interact()["enabled"]:
                return False
            if not config["actions_enabled"]:
                return False
            quiet_since = max(self.last_activity_at, self.last_speak_at, self._last_idle_action_at,
                              hub.stats["started_at"])
            if time.time() - quiet_since < config["action_seconds"]:
                return False
        pool = [motion for motion in config["actions"] if motion in ACTIONS]
        if not pool:
            return False
        if config["action_mode"] == "random":
            motion = random.choice([motion for motion in pool if motion != self._last_idle_motion] or pool)
        else:
            motion = pool[self._idle_action_index % len(pool)]
            self._idle_action_index += 1
        self._last_idle_motion = motion
        if manual and self.fishing.active:
            try:
                await self.interrupt_fishing(ACTIONS[motion]["label"], ACTIONS[motion]["duration"] + .5)
            except ValueError:
                return False
        self._last_idle_action_at = time.time()
        await hub.broadcast({"type": "reaction", "motion": motion, "ambient": not manual, "manual": manual},
                            remember=False)
        return True

    async def _idle_action_loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                if not await self.autonomous_tick():
                    await self.idle_action()
            except Exception:
                log.exception("自动动作调度失败，下一轮继续尝试")

    async def _idle_once(self):
        if Effective.idle()["ai_chat"]:
            return await self.companion_once()
        if not self._ambient_available():
            return False
        templates = Effective.templates()
        if not (templates["enabled"] and templates["idle"]):
            return False
        if queue.playing and not Effective.idle()["speech_during_music"]:
            return False
        idle = time.time() - max(self.last_speak_at, self.last_activity_at, hub.stats["started_at"])
        if idle < Effective.pace()["idle_chat_seconds"]:
            return False
        activity = self.last_activity_at
        generation = self._speech_generation
        self.last_speak_at = time.time()
        self._idle_preparing = True
        try:
            from .capabilities import invitation_options
            invitation = None
            idle_config = Effective.idle()
            if idle_config["gameplay_invites"] and time.time() - self._last_idle_invite_at >= idle_config["invite_seconds"]:
                options = invitation_options(self.runtime_context())
                if options:
                    invitation = options[self._idle_invite_index % len(options)]
            music = Effective.music()
            choices = approved_songs(music)
            if invitation:
                reference = render_line(invitation[1])
                text = reference
                if self.llm.active and templates["prefer_llm"]:
                    text = await self._llm_line("直播间暂时安静，邀请观众体验一项已开启的玩法。请按这句事实参考自然说一两句，"
                                                "保留触发口令，不要声称已经执行，不要自行开局：" + reference, limit=90) or reference
            elif (self.llm.active and music["enabled"] and music["ai_dj"] and choices and not queue.paused
                    and not queue.playing and not queue.pending()):
                title, artist = random.choice(choices)
                ok, _ = await self.ai_song(f"{title} {artist}".strip())
                if ok:
                    await self._push_music()
                    text = render_line("idle_dj")
                else:
                    text = ""
            elif self.llm.active and templates["prefer_llm"]:
                text = await self._llm_line("直播间暂时安静，请结合实时状态用一句完整的话邀请观众聊天。只介绍当前已开启的能力，不要声称已执行，40字左右。", limit=40)
            else:
                key = "idle_topics" if self._idle_topic_index % 2 == 0 else "idle_invites"
                pool = store.lines(key) or store.lines("idle_topics")
                text = pool[(self._idle_topic_index // 2) % len(pool)] if pool else random_topic()
                self._idle_topic_index += 1
            if (not text or generation != self._speech_generation or activity != self.last_activity_at
                    or not self._ambient_available()):
                return False
            if invitation and (not Effective.idle()["gameplay_invites"] or invitation not in invitation_options(self.runtime_context())):
                return False
            await self._speak(text, None, 0.2, motion="idle")
            if invitation:
                self._last_idle_invite_at = time.time()
                self._idle_invite_index += 1
            return True
        finally:
            self._idle_preparing = False

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            try:
                await self._idle_once()
            except Exception:
                log.exception("冷场互动失败，下一轮继续尝试")
