"""统一事件模型：所有接入源都归一化成 Event，下游只认这一种格式。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

EventKind = Literal["chat", "gift", "enter", "like", "follow", "system"]


@dataclass(slots=True)
class Event:
    kind: EventKind
    user_id: str = ""
    user_name: str = "观众"
    text: str = ""
    gift_name: str = ""
    gift_count: int = 0
    # 礼物价值（钻石/音浪），未知时给个保守估值用于打分
    gift_value: int = 0
    avatar: str = ""
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    # 是否已被播报，用于前端灰显
    answered: bool = False
    like_count: int | None = None
    like_total: int | None = None
    like_source: str = ""
    like_incremental: bool = False
    is_admin: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "userId": self.user_id,
            "userName": self.user_name,
            "text": self.text,
            "giftName": self.gift_name,
            "giftCount": self.gift_count,
            "giftValue": self.gift_value,
            "avatar": self.avatar,
            "ts": self.ts,
            "answered": self.answered,
            "likeCount": self.like_count,
            "likeTotal": self.like_total,
            "isAdmin": self.is_admin,
        }
