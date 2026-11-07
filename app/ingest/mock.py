"""模拟弹幕源：无需登录、无需真实直播间，用于开发调试与效果演示。"""
from __future__ import annotations

import asyncio
import random

from ..events import Event

USERS = [
    "路过的咸鱼", "今天也要加油呀", "鱼塘保卫者", "熬夜冠军", "小鲤鱼",
    "不吃香菜", "摸鱼大师", "隔壁老张", "夜跑选手", "爱笑的猫",
    "只潜水不说话", "前排观众", "键盘侠本侠", "早八人",
]

CHATS = [
    "主播你好呀", "这条鱼好可爱", "你会唱歌吗", "今天吃什么了",
    "我关注你很久了", "你几岁了", "这个直播怎么做的", "好家伙，居然会说话",
    "能推荐点好看的吗", "你这表情好逗", "来点才艺", "猜猜我在哪",
    "第一次来，你好", "有点无聊啊", "主播声音好听", "为什么叫大肥鱼",
]

GIFTS = [("小心心", 1, 1), ("玫瑰", 1, 5), ("啤酒", 2, 10), ("棒棒糖", 5, 30), ("嘉年华", 1, 3000)]

TOPICS_BY_VIEWER = {
    "小鲤鱼": ["我最近在学做饭", "今天加班到十点", "能陪我聊会儿吗"],
    "熬夜冠军": ["我又失眠了", "凌晨三点了还没睡"],
    "摸鱼大师": ["上班摸鱼被发现了怎么办", "教我点摸鱼技巧"],
}


class MockIngestor:
    name = "mock"

    def __init__(self) -> None:
        self._round = 0

    async def run(self, emit) -> None:
        await asyncio.sleep(0.5)
        while True:
            self._round += 1
            await emit(self._next())
            await asyncio.sleep(random.uniform(1.8, 5.2))

    # ---- 生成单条事件 ----
    def _next(self) -> Event:
        roll = random.random()
        user = random.choice(USERS)

        if roll < 0.12:
            name, n, val = random.choice(GIFTS)
            return Event(
                kind="gift", user_id=f"u{abs(hash(user)) % 10000}", user_name=user,
                gift_name=name, gift_count=n, gift_value=val,
                text=f"送出了{name}x{n}",
            )
        if roll < 0.30:
            return Event(kind="enter", user_id=f"u{abs(hash(user)) % 10000}", user_name=user)
        if roll < 0.36:
            return Event(kind="like", user_id=f"u{abs(hash(user)) % 10000}", user_name=user, gift_count=random.randint(3, 20))

        # 聊天：一半是随机话题，一半来自"熟客"
        if user in TOPICS_BY_VIEWER and random.random() < 0.5:
            text = random.choice(TOPICS_BY_VIEWER[user])
        else:
            text = random.choice(CHATS)
        return Event(kind="chat", user_id=f"u{abs(hash(user)) % 10000}", user_name=user, text=text)
