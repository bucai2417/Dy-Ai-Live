from __future__ import annotations

import math


ACTIONS = {
    "wave": {"label": "挥手打个招呼", "duration": 3.6, "effect": "bubbles"},
    "bow": {"label": "谢谢你的心意", "duration": 3.4, "effect": "sparkle"},
    "happy": {"label": "开心到冒泡", "duration": 3.8, "effect": "sparkle"},
    "dance": {"label": "一起摇摆吧", "duration": 5.2, "effect": "confetti"},
    "shy": {"label": "被夸得脸红啦", "duration": 3.8, "effect": "hearts"},
    "surprise": {"label": "哇！收到惊喜了", "duration": 3.4, "effect": "sparkle"},
    "shake": {"label": "哈哈，太好笑啦", "duration": 3.5, "effect": "bubbles"},
    "nod": {"label": "听到你啦", "duration": 2.6, "effect": ""},
    "handstand": {"label": "倒过来看世界", "duration": 4.8, "effect": "sparkle"},
    "spin": {"label": "转一圈给你看", "duration": 3.8, "effect": "confetti"},
    "mini": {"label": "变成小小一只", "duration": 5.0, "effect": "bubbles"},
    "heart": {"label": "送你一颗小心心", "duration": 4.6, "effect": "hearts"},
    "stretch": {"label": "伸个懒腰放松一下", "duration": 5.0, "effect": "sparkle"},
    "peek": {"label": "探头看看谁在潜水", "duration": 4.8, "effect": "bubbles"},
    "float": {"label": "轻飘飘水母漂浮", "duration": 5.8, "effect": "bubbles"},
    "sleepy": {"label": "打个小盹等你来", "duration": 5.4, "effect": ""},
    "tail_sway": {"label": "鲸尾摇摇乐", "duration": 5.0, "effect": "bubbles"},
}
SKINS = {"sailor": "蓝海水手服", "raincoat": "柠檬雨衣", "starry": "星空礼服"}
ACCESSORIES = {"original": "恢复原装", "crown": "星光皇冠", "cool": "墨镜派对", "festival": "蝴蝶结装扮"}
COSTUMES = {**SKINS, **ACCESSORIES}
EFFECTS = {"", "hearts", "sparkle", "confetti", "bubbles"}
RULE_ACTIONS = {"r-cmt-dance": "dance", "r-cmt-beauty": "shy", "r-cmt-laugh": "shake",
                "r-cmt-666": "happy", "r-gift-big": "surprise", "r-gift-mid": "bow",
                "r-gift-small": "bow", "r-follow": "wave"}


def event_action(event: dict) -> str:
    kind = event.get("kind")
    if kind == "gift":
        return "surprise" if float(event.get("giftValue") or 0) >= 500 else "bow"
    if kind in ("follow", "enter"):
        return "wave"
    if kind == "like":
        return "nod"
    if kind == "chat":
        text = str(event.get("text") or "")
        for action, words in (("dance", ("跳舞", "跳个舞", "扭一个")),
                              ("shy", ("可爱", "好看", "漂亮")), ("shake", ("哈哈", "笑死")),
                              ("happy", ("666", "厉害", "太强")), ("wave", ("你好", "晚上好", "早上好"))):
            if any(word in text for word in words):
                return action
    return ""


def pose(reaction: dict, now: float) -> dict:
    result = {"x": 0.0, "y": 0.0, "tilt": 0.0, "scale_x": 1.0, "scale_y": 1.0,
              "eyes": 0.0, "blush": 0.0, "mouth": 0.0, "strength": 0.0}
    age = now - reaction.get("started", now)
    duration = reaction.get("duration", 0)
    if age < 0 or age >= duration - 1e-7:
        return result
    strength = min(1.0, age / 0.3, (duration - age) / 0.45)
    strength = strength * strength * (3 - 2 * strength)
    motion = reaction.get("motion")
    result["strength"] = strength
    if motion == "wave":
        result.update(x=math.sin(age * 4.8) * 9, tilt=math.sin(age * 4.8) * 5)
    elif motion == "bow":
        bend = math.sin(min(1, age / duration) * math.pi) ** 2
        result.update(y=24 * bend, scale_y=1 - 0.09 * bend, eyes=bend * 0.75)
    elif motion == "happy":
        result.update(y=-abs(math.sin(age * 5.5)) * 23, scale_y=1 + math.sin(age * 11) * 0.018,
                      tilt=math.sin(age * 3) * 3, eyes=max(0, math.sin(age * 4)) * 0.8)
    elif motion == "dance":
        result.update(x=math.sin(age * 5) * 20, y=-abs(math.sin(age * 5)) * 10,
                      tilt=math.sin(age * 5) * 8, scale_y=1 + math.sin(age * 10) * 0.018)
    elif motion == "shy":
        result.update(tilt=-5, y=6, scale_x=0.98, eyes=0.3, blush=1)
    elif motion == "surprise":
        result.update(y=-17 * abs(math.sin(age * 3.5)), scale_x=1.025, scale_y=1.035, mouth=0.75)
    elif motion == "shake":
        result.update(x=math.sin(age * 13) * 4, y=math.sin(age * 12) * 4,
                      tilt=math.sin(age * 9) * 3, eyes=0.8, mouth=0.55)
    elif motion == "nod":
        result.update(y=(1 - math.cos(age * 7)) * 4, scale_y=1 - (1 - math.cos(age * 7)) * 0.015)
    elif motion == "handstand":
        result.update(tilt=180, scale_x=0.85, scale_y=0.85)
    elif motion == "spin":
        progress = age / duration
        result.update(tilt=360 * (progress * progress * (3 - 2 * progress)), scale_x=0.85, scale_y=0.85)
    elif motion == "mini":
        result.update(scale_x=0.55, scale_y=0.55, y=30)
    elif motion == "heart":
        result.update(tilt=math.sin(age * 2.8) * 5, scale_x=1.025, scale_y=1.025, blush=0.9,
                      y=-abs(math.sin(age * 2.8)) * 8)
    elif motion == "stretch":
        result.update(scale_x=0.96, scale_y=1.08, y=-16, eyes=0.8, tilt=math.sin(age * 1.5) * 3)
    elif motion == "peek":
        result.update(x=math.sin(age * 2.2) * 42, tilt=math.sin(age * 2.2) * 12, scale_x=0.86,
                      scale_y=0.86, y=22)
    elif motion == "float":
        result.update(x=math.sin(age * 1.4) * 24, y=-24 - math.sin(age * 2) * 20,
                      tilt=math.sin(age * 1.4) * 7, scale_y=1.02)
    elif motion == "sleepy":
        result.update(eyes=0.95, tilt=math.sin(age * 1.3) * 7, y=10 + math.sin(age * 1.6) * 6,
                      scale_y=0.97)
    for key in result:
        if key.startswith("scale"):
            result[key] = 1 + (result[key] - 1) * strength
        elif key != "strength" and not (key == "tilt" and motion == "spin"):
            result[key] *= strength
    return result
