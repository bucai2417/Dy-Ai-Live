from __future__ import annotations

import asyncio
import random
import time
from collections import deque

from ..settings_store import Effective, store
from ..music_policy import approved_songs
from .utterance import SENTENCE_END


def finish_text(text, target):
    ends = [match.end() for match in SENTENCE_END.finditer(text)]
    limit = max(target * 2, target + 80)
    complete = [end for end in ends if end <= limit]
    if not complete:
        return ""
    cut = next((end for end in complete if end >= target), complete[-1])
    return text[:cut].strip()


class AICompanion:
    def init_companion(self):
        self.topic_memory = deque(maxlen=12)
        self.companion_status = "等待安静时主动找话题"
        self.companion_retry_at = 0
        self._companion_task = None
        self._companion_speaking = False

    async def interrupt_companion(self):
        task = self._companion_task
        if not task or task.done():
            return
        speaking = self._companion_speaking
        if speaking:
            self.speaker.interrupt()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.companion_status = "观众互动优先，稍后再聊"
        if speaking:
            await self.live_bus.broadcast({"type": "speech_interrupt"}, remember=False)

    def companion_available(self):
        world = self.world.describe()
        fishing = self.fishing.snapshot()
        performance = self.live_bus.performance.snapshot()
        return not (self.paused or self.speaker.busy or self._response_task or self._pending_replies
                    or self.pending_enter or self.likes.active or self.likes.pending or self.gift_show.active
                    or self.games.active or self._live_voice_task
                    or performance and performance.get("priority", 0) >= 2
                    or world["activity"] in ("sleep", "drive", "parked")
                    or fishing["active"] and (fishing["phase"] != "waiting" or fishing["remaining"] < 10))

    async def companion_once(self):
        if self._companion_task and not self._companion_task.done():
            return False
        task = asyncio.create_task(self._companion_once(), name="brain:companion")
        self._companion_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            if self._companion_task is task:
                self._companion_task = None

    async def _companion_once(self):
        from .scheduler import queue

        config = Effective.idle()
        if not config["ai_chat"] or not self.companion_available():
            return False
        if queue.playing and not config["speech_during_music"]:
            self.companion_status = "歌曲播放中，等待歌曲结束再聊"
            return False
        interval = max(15, Effective.pace()["idle_chat_seconds"])
        now = time.time()
        quiet = min(now - max(self.last_speak_at, self.live_bus.stats["started_at"]), time.monotonic() - self._audience_at)
        if now < self.companion_retry_at or quiet < interval:
            return False
        if not self.llm.active:
            self.companion_status = "AI未连接，等待可用模型；不会播放预设聊天"
            self.companion_retry_at = now + 30
            return False
        activity, generation = self._audience_at, self._speech_generation
        self._idle_preparing = True
        self.companion_status = "AI正在想新话题"
        self.companion_retry_at = now + interval
        try:
            music = Effective.music()
            choices = approved_songs(music)
            if (music["enabled"] and music["ai_dj"] and choices and self._ambient_available()
                    and not queue.paused and not queue.playing and not queue.pending()):
                title, artist = random.choice(choices)
                self.companion_status = "正在安排已审核的音乐：" + title
                selected, _ = await self.ai_song(f"{title} {artist}".strip())
                if selected:
                    await self._push_music()
                    self.companion_status = "已安排音乐：" + title
                    return True
            result = await self.llm.structured(
                "你是陪伴型虚拟主播，直播间安静时自己找话题自然自言自语。依据人设和真实场景，自主选择一个生活化、想象力丰富或轻松有趣的话题，"
                "像坐在旁边闲聊，2到4句完整中文，可接着上次展开或换个话题。不要总问有没有人、不要每句催观众互动、不要乞求送礼。"
                "不编造现场观众、弹幕、现实经历或实时新闻，不重复近期说过的内容，不背诵输入里的候选台词。"
                "仅在allow_gameplay_invites为真时可以自然邀请观众尝试已启用玩法，但不必每段邀请。"
                '不可声称已执行场景动作。睡觉不适合这段聊天。输出{"topic":"简短主题","speech":"可直接播报的话"}。',
                {"persona": store.section("persona"), "scene": self.runtime_context(),
                 "recent_topics": list(self.topic_memory), "preferences": config["topic_preferences"],
                 "allow_gameplay_invites": config["gameplay_invites"],
                 "target_characters": config["chat_chars"]}, max_tokens=1000)
            if not isinstance(result, dict) or not isinstance(result.get("speech"), str) or not isinstance(result.get("topic"), str):
                self.companion_status = "AI暂未生成有效话题，稍后重试"
                self.companion_retry_at = time.time() + 60
                return False
            text = finish_text(result["speech"].strip(), config["chat_chars"])
            if len(text) < 12 or any(text == entry["speech"] for entry in self.topic_memory):
                self.companion_status = "话题过短或重复，等待下一次构思"
                return False
            if (activity != self._audience_at or generation != self._speech_generation
                    or not Effective.idle()["ai_chat"] or not self.companion_available()):
                self.companion_status = "观众互动优先，已放弃过时话题"
                return False
            topic = result["topic"].strip()[:50]
            self.topic_memory.append({"topic": topic, "speech": text})
            self.companion_status = "正在聊：" + topic
            self._companion_speaking = True
            await self._speak(text, None, .2, motion="")
            self.companion_status = "刚聊过：" + topic
            return True
        finally:
            self._companion_speaking = False
            self._idle_preparing = False
