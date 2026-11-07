"""TTS 引擎抽象 + edge-tts 实现（微软免费神经网络语音，无需 Key）。"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

from ..config import settings

log = logging.getLogger(__name__)


class TTSEngine(Protocol):
    name: str

    async def synthesize(self, text: str) -> bytes | None:
        """返回 mp3 字节；失败返回 None（前端会退化为纯字幕）。"""


class EdgeTTS:
    name = "edge"

    # 连续失败后短暂冷却，避免每个句子都卡一次超时（无人直播必须能自愈）
    _fail_streak = 0
    _cooldown_until = 0.0

    async def synthesize(self, text: str) -> bytes | None:
        text = clean(text)
        if not text:
            return None

        now = asyncio.get_event_loop().time()
        if now < self._cooldown_until:
            return None  # 冷却中，直接让前端走字幕

        try:
            import edge_tts

            from ..settings_store import Effective
            from .cache import load as cache_load, save as cache_save

            cfg = Effective.tts()

            # 先查缓存：开场白、欢迎语、感谢语这些会反复说到，
            # 命中直接返回，省一次网络往返（实测首次 ~2.8s，命中 ~0ms）
            key = f"{cfg['voice']}|{cfg['rate']}|{cfg['volume']}|{text}"
            hit = cache_load(key)
            if hit:
                EdgeTTS._fail_streak = 0
                return hit

            communicate = edge_tts.Communicate(
                text,
                cfg["voice"],
                rate=cfg["rate"],
                volume=cfg["volume"],
            )
            buf = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if not buf:
                log.warning("edge-tts 返回空音频：%s", text[:30])
                return None
            EdgeTTS._fail_streak = 0
            data = bytes(buf)
            cache_save(key, data)
            return data
        except Exception as exc:
            EdgeTTS._fail_streak += 1
            if EdgeTTS._fail_streak >= 3:
                # 连续失败：进入 30 秒冷却，避免拖垮整条播报节奏
                EdgeTTS._cooldown_until = asyncio.get_event_loop().time() + 30
                EdgeTTS._fail_streak = 0
                log.error("edge-tts 连续失败，暂停语音 30 秒（画面与字幕照常）：%s", exc)
            else:
                log.error("edge-tts 合成失败：%s", exc)
            return None


class NullTTS:
    name = "none"

    async def synthesize(self, text: str) -> bytes | None:
        return None


_MD = re.compile(r"[*_#`>~\[\]()]|\s{2,}")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F]"
)


def clean(text: str) -> str:
    """把大模型输出清洗成适合朗读的纯文本。"""
    text = _MD.sub(" ", text or "")
    text = _EMOJI.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    # 去掉句首括号动作，如"（笑）"
    text = re.sub(r"^[（(].{0,8}[）)]", "", text).strip()
    return text


def split_sentences(text: str, max_len: int = 42) -> list[str]:
    """按标点切成适合逐句播报的短句，兼顾停顿自然。"""
    text = clean(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        while len(p) > max_len:
            cut = max(p.rfind("，", 0, max_len), p.rfind("、", 0, max_len), p.rfind(" ", 0, max_len))
            if cut <= 4:
                cut = max_len
            out.append(p[: cut + 1].strip())
            p = p[cut + 1 :].strip()
        if p:
            out.append(p)
    return [s for s in out if s]


def build_engine() -> TTSEngine:
    from ..settings_store import Effective

    if Effective.tts()["engine"] == "edge":
        return EdgeTTS()
    return NullTTS()
