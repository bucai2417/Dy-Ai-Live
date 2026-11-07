"""直播数据统计：token 用量、对话次数、礼物排行榜。

为什么要单独一个模块：
    - token 用量必须**按天重置**，否则"今日消耗"会一直累加，运营看不出当天情况
    - 礼物榜要按人去重累加，同一个人刷 100 个小心心要合成一条，不能刷屏
    - 这些数据要频繁广播给直播画面，所以做成快照（snapshot）而不是逐条推送
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from .gifts import GiftBoard
from .fishing_board import FishingBoard

log = logging.getLogger(__name__)

# 常见模型计价（元 / 百万 token）。用户可在后台改成自己套餐的真实价格。
DEFAULT_PRICING = {
    "input_per_m": 2.0,    # 输入（prompt）
    "output_per_m": 8.0,   # 输出（completion）
}


def _today() -> str:
    return dt.date.today().isoformat()


class Metrics:
    def __init__(self) -> None:
        self.day = _today()
        self.tokens_in = 0
        self.tokens_out = 0
        self.llm_calls = 0        # 真正调到模型且成功的次数
        self.offline_calls = 0    # 走离线兜底的次数
        self.estimated = False    # 是否有估算值（部分服务不回 usage）
        self.replies = 0          # 已播报的回复条数
        self.gifts = 0
        self.gift_value = 0
        # 送礼榜：name -> {name, avatar, value, count, ts}
        self.gift_board = GiftBoard()
        self.fishing_board = FishingBoard()
        self.started_at = time.time()
        self.storage = {}

    # ------------------------------------------------------------ 写入
    def _roll(self) -> None:
        """跨天自动归零，保证"今日"是真的今日。"""
        d = _today()
        if d != self.day:
            log.info("跨天重置今日统计（%s → %s）", self.day, d)
            self.day = d
            self.tokens_in = 0
            self.tokens_out = 0
            self.llm_calls = 0
            self.offline_calls = 0
            self.replies = 0
            self.gifts = 0
            self.gift_value = 0
            self.gift_board.clear()
            self.fishing_board.clear()
            self.estimated = False

    def add_tokens(self, tin: int, tout: int, estimated: bool = False) -> None:
        self._roll()
        if tin or tout:
            self.tokens_in += max(0, int(tin))
            self.tokens_out += max(0, int(tout))
        self.estimated = self.estimated or estimated
        self.llm_calls += 1

    def add_offline(self) -> None:
        self._roll()
        self.offline_calls += 1

    def add_usage(self, usage: dict | None, prompt: str, completion: str) -> None:
        usage = usage or {}
        counts = []
        estimated = False
        for key, text in (("prompt_tokens", prompt), ("completion_tokens", completion)):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                counts.append(value)
            else:
                counts.append(estimate_tokens(text))
                estimated = True
        self.add_tokens(*counts, estimated=estimated)

    def add_reply(self) -> None:
        self._roll()
        self.replies += 1

    def add_gift(self, ev) -> None:
        """记录一笔礼物：累加到该用户名下。"""
        self._roll()
        value = int(getattr(ev, "gift_value", 0) or 0)
        self.gifts += 1
        self.gift_value += value

        self.gift_board.add(ev.to_dict())

    def add_catch(self, round_id, fish, winner):
        self._roll()
        return self.fishing_board.add(round_id, fish, winner)

    def add_fish_encounter(self, round_id, fish):
        self._roll()
        if fish.get("day", self.day) != self.day:
            return False
        return self.fishing_board.encounter(round_id, fish)

    def fishing_ranking(self, limit=10):
        self._roll()
        return {"day": self.day, **self.fishing_board.snapshot(limit)}

    # ------------------------------------------------------------ 读取
    def ranking(self, n: int = 3, by: str = "value") -> list[dict]:
        """礼物榜前 n 名。按累计价值降序，价值相同则先到的在前。"""
        self._roll()
        return self.gift_board.ranking(n, by)

    def cost(self, pricing: dict | None = None) -> float:
        """按后台设置的价格换算成金额（元）。"""
        p = {**DEFAULT_PRICING, **(pricing or {})}
        return round(self.tokens_in / 1_000_000 * float(p["input_per_m"])
                     + self.tokens_out / 1_000_000 * float(p["output_per_m"]), 4)

    def snapshot(self, pricing: dict | None = None) -> dict:
        self._roll()
        return {
            "day": self.day,
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "tokens": self.tokens_in + self.tokens_out,
            "llmCalls": self.llm_calls,
            "offlineCalls": self.offline_calls,
            "estimated": self.estimated,
            "replies": self.replies,
            "gifts": self.gifts,
            "giftValue": self.gift_value,
            "cost": self.cost(pricing),
            "ranking": self.ranking(3),
            "giftRanking": self.gift_board.snapshot(),
            "fishingRanking": self.fishing_ranking(),
            "uptime": int(time.time() - self.started_at),
            "storage": dict(self.storage),
        }


metrics = Metrics()


def estimate_tokens(text: str) -> int:
    """没有 usage 时按字数估算。

    中文大约 1 字 ≈ 0.6 token（各家分词不同，取个保守值），
    英文按 4 字符 ≈ 1 token。只用于展示，不用于计费对账。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 0.6 + other / 4) + 1
