from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote

from .settings_store import Effective


BUILTIN_BLOCKED = (
    "国歌", "军歌", "军旅", "军队", "军乐", "红歌", "红色歌曲", "革命歌曲",
    "义勇军进行曲", "中国人民解放军进行曲", "中国人民志愿军战歌", "人民海军向前进",
    "强军战歌", "强军有我", "当那一天来临", "打靶归来", "团结就是力量", "一二三四歌",
    "当兵的人", "我是一个兵", "军中绿花", "军港之夜", "兵哥哥", "兵妹妹",
    "说句心里话", "小白杨", "骏马奔驰保边疆", "三大纪律八项注意", "学习雷锋好榜样",
    "没有共产党就没有新中国", "歌唱祖国", "保卫黄河", "黄河大合唱", "游击队歌",
    "祖国不会忘记", "检阅进行曲", "分列式进行曲", "拉德茨基进行曲",
    "national anthem", "military march", "military song", "army song",
    "march of the volunteers", "star spangled banner", "god save the king",
    "god save the queen", "la marseillaise", "君之代", "君が代",
)

_TRADITIONAL = str.maketrans("國軍義進強團結當歸來綠說話駿馬邊疆紀律學習鋒紅產黨檢閱黃衛戰艦隊樂愛華歲",
                            "国军义进强团结当归来绿说话骏马边疆纪律学习锋红产党检阅黄卫战舰队乐爱华岁")


def normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", unquote(str(value or ""))).translate(_TRADITIONAL).casefold()
    return "".join(char for char in text if char.isalnum())


def blocked_reason(value: str | dict, config: dict | None = None) -> str:
    config = config if config is not None else Effective.music()
    if isinstance(value, dict):
        parts = [value.get(key) for key in ("title", "by", "album", "genre", "category", "tags", "aliases", "file", "request_query")]
    else:
        parts = [value]
    texts = [normalized(part) for part in parts if part]
    extra = re.split(r"[,，;；\n]+", config.get("blocked_keywords", ""))
    for word in (*BUILTIN_BLOCKED, *extra):
        match = normalized(word)
        if match and any(match in text for text in texts):
            return f"当前直播歌曲规则不播放或演唱这类内容（命中：{word.strip()}），请换一首"
    return ""


def approved_songs(config: dict | None = None) -> list[tuple[str, str]]:
    config = config if config is not None else Effective.music()
    choices = []
    for line in config.get("ai_allowlist", "").splitlines():
        title, _, artist = line.strip().partition("|")
        title, artist = title.strip(), artist.strip()
        if title and not blocked_reason({"title": title, "by": artist}, config):
            choices.append((title, artist))
    return choices


def ai_allowed(value: str | dict, config: dict | None = None) -> bool:
    for title, artist in approved_songs(config):
        if isinstance(value, dict):
            if normalized(value.get("title")) == normalized(title) and (
                    not artist or normalized(value.get("by")) == normalized(artist)):
                return True
        elif normalized(value) in {normalized(title), normalized(title + artist), normalized(artist + title)}:
            return True
    return False


def playback_reason(info: dict, from_ai: bool = False) -> str:
    reason = blocked_reason(info)
    if reason:
        return reason
    if from_ai and not ai_allowed(info):
        return "AI 自动点歌只播放已确认歌单中的歌曲，请在设置中填写歌名和歌手"
    return ""


def query_reason(query: str, from_ai: bool = False) -> str:
    reason = blocked_reason(query)
    if reason:
        return reason
    if re.search(r"(?:https?://|file:|[a-z]:[\\/])", query, re.IGNORECASE):
        return "请使用歌名和歌手点歌，链接或文件路径无法核对歌曲内容"
    if from_ai and not ai_allowed(query):
        return "AI 自动点歌只播放已确认歌单中的歌曲，请先在设置中确认歌单"
    return ""
