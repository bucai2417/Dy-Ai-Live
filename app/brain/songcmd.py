"""点歌指令解析：把弹幕/礼物/AI 指令翻译成队列操作。

弹幕里观众可能说各种说法，这里只认那些明确的：
    点歌 晴天        → 点歌
    来一首 晴天      → 同上
    点一首 周杰伦 晴天
    切歌 / 下一首    → 切歌
    停止 / 暂停音乐  → 停
    播放 / 继续      → 继续

不认"这首歌真好听"这类，避免误触发。
"""
from __future__ import annotations

import logging
import re

from ..playlist import queue

log = logging.getLogger(__name__)

# 「点歌 XXX」「来一首 XXX」「点一首 XXX」——歌名取到结尾
_REQ = re.compile(
    r"^(?:点歌|点一首|来一首|来首|播放|放一首|放首|我想听|想听)\s*[:：]?\s*(.{1,60})$"
)
_REQ_TAIL = re.compile(
    r"^(?:(?:主播|小鱼|大肥鱼)[，,\s]*)?(?:(?:请|能不能|可不可以|可以|能|帮我|给我)\s*)+"
    r"(?:点歌|点一首|来一首|来首|播放|放一首|放首|放)\s*[:：]?\s*(.{1,60})$"
)
_NEXT = re.compile(r"^(?:切歌|下一首|下首|换一首|换歌|跳过|skip|next)$")
_STOP = re.compile(r"^(?:停止音乐|停止播放|暂停音乐|暂停播放|别放了|关掉音乐|停一下|静音)$")
_RESUME = re.compile(r"^(?:继续播放|继续放|播放音乐|开音乐|放起来)$")
_WHAT = re.compile(r"^(?:在放什么|放的是什么|现在什么歌|什么歌|这首是什么)$")

CUT_WORDS = ("切歌", "下一首", "跳过", "换一首", "换歌", "停一下", "别放了")


class SongRequest:
    __slots__ = ("query", "action")

    def __init__(self, action: str, query: str = "") -> None:
        self.action = action           # add / next / stop / resume / what
        self.query = query


def parse_request(text: str) -> SongRequest | None:
    """从一条弹幕里识别点歌意图；识别不到返回 None。"""
    t = (text or "").strip()
    if not t or len(t) > 100:
        return None

    plain = re.sub(r"[\s\u3000，,。.!！?？]+", "", t)
    plain = re.sub(r"^(?:请|能不能|帮我|可以)", "", plain)
    plain = re.sub(r"(?:吧|吗|好吗|可以吗)$", "", plain)
    if _NEXT.match(plain):
        return SongRequest("next")
    if _STOP.match(plain):
        return SongRequest("stop")
    if _RESUME.match(plain):
        return SongRequest("resume")
    if _WHAT.match(plain):
        return SongRequest("what")

    m = _REQ.match(t) or _REQ_TAIL.search(t)
    if m:
        q = (m.group(1) or "").strip(" ，,。.！!？?、~～")
        q = re.sub(r"(?:可以吗|好不好|好吗|吧|吗|呗)$", "", q).strip(" 《》\"'")
        if q in ("什么", "什么歌", "歌", "音乐", "一下", "不了", "不出来"):
            return None
        # 「点歌」两个字后面什么都没有，当闲聊处理
        if len(q) >= 1 and not _NEXT.match(re.sub(r"\s", "", q)):
            return SongRequest("add", q[:60])
    return None


def queue_view() -> dict:
    return queue.snapshot()


def describe_current() -> str:
    """给 AI 用的当前播放描述。"""
    cur = queue.current
    if not cur:
        return "现在没有放歌"
    return f"正在放《{cur.title}》{('-' + cur.by) if cur.by else ''}"


def describe_queue(limit: int = 5) -> str:
    items = queue.pending()[:limit]
    if not items:
        return "队列是空的"
    parts = [f"《{t.title}》" + (f"（{t.requested_by}点）" if t.requested_by else "")
             for t in items]
    return "、".join(parts)
