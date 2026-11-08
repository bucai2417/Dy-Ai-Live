"""可在线编辑并热生效的配置：人设、话术、LLM、TTS、节奏、敏感词。

设计要点：
- 与 .env 分工：.env 放密钥与启动参数；这里放「运营可调」的内容，存 JSON，随时改。
- 保存即生效：settings_store 内存里立刻更新，大脑每次用都读最新值。
- 原子写：先写临时文件再替换，避免断电/崩溃把配置写坏。
"""
from __future__ import annotations

import copy
import json
import logging
import math
import re
import threading
import time
from string import Formatter
from typing import Any
from .lines import EXTRA_LINES, LINE_PARAMETERS
from .native.actions import SKINS

from .config import DATA_DIR

log = logging.getLogger(__name__)

CONFIG_FILE = DATA_DIR / "runtime.json"

# 默认值：第一次运行写盘，之后以文件为准
DEFAULTS: dict[str, Any] = {
    "persona": {
        "name": "大肥鱼",
        "style": "嘴贫、爱吐槽、热心、偶尔自恋，喜欢用\"鱼\"相关梗",
        "rules": (
            "1. 口语化，像真人主播说话，不要书面语、不要加\"主播：\"等前缀。\n"
            "2. 每次只说 1~2 句、总共不超过 60 个字，说完就停。\n"
            "3. 不确定就幽默带过，绝不编造价格、功效、医疗、金融承诺。\n"
            "4. 涉及送礼：真诚道谢但不谄媚，不诱导消费。\n"
            "5. 不要出现 markdown、emoji 堆砌、括号里的动作描述。\n"
            "6. 输出纯文本，会被直接转成语音念出来。"
        ),
    },
    "llm": {
        "base_url": "",
        "api_key": "",
        "model": "",
        "temperature": 0.85,
        "max_tokens": 220,
        # 人设下发方式：auto=自动探测上游认不认 system；system=强制标准；user=并入 user
        "prompt_mode": "auto",
        # 计价：用于直播画面把 token 换算成金额，按你实际套餐改
        "price_in": 2.0,     # 输入 元/百万 token
        "price_out": 8.0,    # 输出 元/百万 token
        "currency": "¥",
    },
    "tts": {
        "engine": "edge",
        "voice": "zh-CN-XiaoyiNeural",
        "rate": "+8%",
        "volume": "+0%",
    },
    "pace": {
        "min_speak_gap": 1.2,
        "idle_chat_seconds": 45,
        "announce_enter": True,
        "announce_like": False,
        "announce_follow": True,
        "enter_merge_window": 6,
        "max_reply_chars": 60,
    },
    "ingest": {
        "enabled": True,
        "source": "douyin",
        "douyin_room": "",
        "bridge_port": 5001,
    },
    "lines": {
        **EXTRA_LINES,
        "welcome": ["欢迎{a}来到直播间", "哟，{a}来了", "{a}你好呀"],
        "thanks_small": [
            "谢谢{name}送的{gift}，爱你哦",
            "收到{name}的{gift}，我记下了",
            "{name}破费了，谢谢你的{gift}",
        ],
        "thanks_big": [
            "哇，{name}送了{gift}，我这条鱼要原地转三圈了，谢谢",
            "感谢{name}的大礼，我真不知道该说什么了，只能多陪你聊会儿",
        ],
        "follow": [
            "谢谢{name}的关注，鱼塘又多了个伙伴",
            "{name}点了关注，我记住你了",
            "感谢{name}加入，以后常来陪我唠嗑",
        ],
        "idle_topics": [
            "你们今天都吃的啥？我这条鱼只能看着流口水",
            "说真的，盯着屏幕久了对眼睛不好，记得眨眨眼",
            "有人猜猜我今天胖了几斤吗",
            "来，评论区告诉我你们是几点刷到我的",
            "我最喜欢听鱼友讲故事了，有人要讲一个吗",
        ],
    },
    "filters": {
        "banned_words": ["加微信", "私聊", "转账", "刷单", "代练", "外挂", "赌博", "百家乐"],
        "max_input_chars": 120,
        "per_user_cooldown": 4.0,
        "dedupe_seconds": 90,
    },
    "scoring": {
        "gift": 6.0,
        "follow": 3.0,
        "chat": 1.0,
        "enter": 0.4,
        "like": 0.1,
        "question_bonus": 1.2,
        "mention_bonus": 0.5,
    },
    # 舞台：选哪个模型、摆多大、摆在哪。offset 用百分比，换分辨率不会跑位
    "stage": {
        "model": "haru/haru_greeter_t03.model3.json",
        "scale": 1.0,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "flip": False,
        "opacity": 1.0,
        "auto_layout": True,
        "show_danmu": True,
        "show_subtitle": True,
        "subtitle_size": 20,
        "subtitle_backing": False,
        # 顶部/侧边这些信息块，主播可能只想在后台看，不想上画面
        "show_datapods": True,     # 今日 token/花费/对话数 气泡
        "show_tokens": True,
        "show_ranking": True,      # 土豪榜
        "show_ballpit": True,      # 送礼观众漂浮气泡
        "show_badges": True,       # 语音/大脑 状态徽章
        "native_effects": True,
        "native_music_motion": True,
        "scene_reactions": True,
        "costume": "original",
        "outfit": "sailor",
        "avatar_mode": "layered",
        "show_bones": False,
        "video_enabled": True,
        "video_path": "data/interaction-media/奶蛙.webm",
        "video_keywords": "奶蛙,奶娃,小奶蛙",
        "video_volume": 0.8,
        "video_scale": 1.0,
        # 漂浮气泡手感，全部可在后台调
        "ball_speed": 1.0,         # 整体速度倍数 0.1~3
        "ball_max": 10,
        "ball_rank_by": "value",
        "ball_size": 1.0,          # 气泡缩放 0.6~1.8
        "ball_bounce": True,       # 碰边回弹；关掉就穿墙循环
        # 排行榜样式
        "rank_max": 3,             # 显示前几名 1~10
        "rank_effect": True,       # 前三名特效
    },
    # 音乐播放器：观众点歌 + 礼物优先 + AI 自主点歌
    "music": {
        "enabled": True,
        "autoplay": True,          # 队列有歌就自动放
        "gift_priority": True,     # 送礼点歌插队
        "gift_min": 1,             # 送多少价值的礼物才算"点歌优先"
        "ai_dj": True,             # 允许 AI 主动点歌/切歌/报歌名
        "ai_allowlist": "",
        "blocked_keywords": "",
        "volume": 0.6,             # 画面内音量 0~1
        "duck": 0.35,              # 说话时音乐压低到原音量的比例（1=不压）
        "show_player": True,       # 直播画面显示播放器
        "cookie": "",              # QQ 音乐 Cookie（可选）
        "providers": "local,netease,qq",
    },
    # 兜底：LLM 不可用时的话术与开关
    "fallback": {
        "offline_voice": True,     # 兜底语音是否播（关掉就只显示文字不出声）
        "offline_reply": True,     # 无 LLM 时是否用内置话术回复
    },
    "world": {
        "enabled": True,
        "scene": "room",
        "weather": "clear",
        "camera": "front",
        "chat_control": True,
        "cooldown": 2.0,
        "particles": True,
    },
    "fishing": {
        "enabled": True,
        "dedicated": False,
        "voice": True,
        "likes_per_pull": 10,
        "escape_chance": 0.06,
        "bite_min": 12,
        "bite_max": 28,
        "fight_min": 25,
        "fight_max": 45,
        "gift_pulls": 5,
        "chat_switch": True,
        "rank_control": "either",
        "admin_ids": "",
        "item_chance": 0.18,
        "day_mode": "cycle",
        "day_cycle": 600,
        "net_enabled": True,
        "net_multiplier": 20,
        "loosen_enabled": True,
        "loosen_amount": 0.08,
    },
    "gift_show": {"enabled": True, "voice": True, "beer_video": True},
    "bgm": {"enabled": False, "path": "", "volume": 0.2, "duck": 0.25},
    "ticker": {
        "enabled": True,
        "text": "发“去钓鱼”开启协力钓鱼，咬钩后点赞或发“拉钩”收线 · 换个场景 / 回家 / 点歌 / 换装，弹幕一起玩！",
        "speed": 55.0,
    },
    "like_goal": {
        "enabled": True,
        "step": 100,
        "cooldown": 5.0,
        "mode": "cycle",
        "show_progress": True,
    },
    # 预设台词：这些是不经过模型、直接用模板拼出来的句子。
    # 想"画面里一个字都不要预设"就把 enabled 关掉。
    "templates": {
        "enabled": True,       # 总开关
        "gift": True,          # 送礼感谢语
        "follow": True,        # 关注感谢语
        "welcome": True,       # 进场欢迎语
        "idle": True,          # 冷场主动抛话题
        # 有模型时优先让模型说这几类话，而不是念模板（更自然）
        "prefer_llm": True,
    },
    "idle": {
        "dream_talk": True,
        "dream_seconds": 45,
        "actions_enabled": True,
        "gameplay_invites": True,
        "invite_seconds": 180,
        "action_seconds": 25,
        "action_mode": "cycle",
        "actions": ["stretch", "peek", "float", "heart", "sleepy"],
        "speech_during_music": False,
    },
    # 互动：让观众的动作真的能改变画面，而不是只有嘴在动
    "interact": {
        "enabled": True,
        "rules": [
            {
                "id": "r-gift-big",
                "on": "gift",
                "match": "",
                "min_value": 500,
                "motion": "happy",
                "expression": "auto",
                "effect": "confetti",
                "reply": "哇，{name}送了{gift}！我这条鱼要原地转三圈了，谢谢你！",
                "cooldown": 0,
            },
            {
                "id": "r-gift-mid",
                "on": "gift",
                "match": "",
                "min_value": 50,
                "motion": "happy",
                "expression": "auto",
                "effect": "hearts",
                "reply": "感谢{name}的{gift}，我记在心里了",
                "cooldown": 0,
            },
            {
                "id": "r-gift-small",
                "on": "gift",
                "match": "",
                "min_value": 0,
                "motion": "shake",
                "expression": "auto",
                "effect": "sparkle",
                "reply": "谢谢{name}送的{gift}",
                "cooldown": 2,
            },
            {
                "id": "r-cmt-666",
                "on": "chat",
                "match": "666,牛,厉害,绝了,太强",
                "min_value": 0,
                "motion": "happy",
                "expression": "auto",
                "effect": "sparkle",
                "reply": "",
                "cooldown": 8,
            },
            {
                "id": "r-cmt-beauty",
                "on": "chat",
                "match": "好看,漂亮,可爱,帅,美,萌",
                "min_value": 0,
                "motion": "happy",
                "expression": "auto",
                "effect": "hearts",
                "reply": "",
                "cooldown": 10,
            },
            {
                "id": "r-cmt-laugh",
                "on": "chat",
                "match": "哈哈,笑死,乐死,绷不住",
                "min_value": 0,
                "motion": "shake",
                "expression": "auto",
                "effect": "",
                "reply": "",
                "cooldown": 8,
            },
            {
                "id": "r-cmt-dance",
                "on": "chat",
                "match": "跳个舞,跳舞,扭一个,来一段",
                "min_value": 0,
                "motion": "happy",
                "expression": "auto",
                "effect": "confetti",
                "reply": "行吧，那我扭两下，别嫌我胖啊",
                "cooldown": 15,
            },
            {
                "id": "r-follow",
                "on": "follow",
                "match": "",
                "min_value": 0,
                "motion": "happy",
                "expression": "auto",
                "effect": "hearts",
                "reply": "",
                "cooldown": 5,
            },
        ],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _validate_config(data: dict, defaults: dict = DEFAULTS, path: str = "") -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{path or '配置'}必须是对象")
    for key, value in data.items():
        name = f"{path}.{key}" if path else key
        if key not in defaults:
            continue
        expected = defaults[key]
        if isinstance(expected, dict):
            _validate_config(value, expected, name)
        elif isinstance(expected, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{name}必须是开关值")
        elif isinstance(expected, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name}必须是有限数字")
            if not name.startswith(("stage.", "music.")) and value < 0:
                raise ValueError(f"{name}不能为负数")
        elif isinstance(expected, str):
            if not isinstance(value, str):
                raise ValueError(f"{name}必须是文字")
        elif isinstance(expected, list):
            if not isinstance(value, list):
                raise ValueError(f"{name}必须是列表")
            if name == "interact.rules":
                for rule in value:
                    if not isinstance(rule, dict):
                        raise ValueError("互动规则必须是对象")
                    _validate_config(rule, DEFAULTS["interact"]["rules"][0], name)
            elif any(not isinstance(item, str) for item in value):
                raise ValueError(f"{name}只能包含文字")
    if path:
        return
    for section, key, choices in (
        ("ingest", "source", ("", "mock", "bridge", "douyin", "douyin_direct")),
        ("tts", "engine", ("", "edge", "none")),
        ("llm", "prompt_mode", ("auto", "system", "user")),
        ("like_goal", "mode", ("cycle", "random", "swim", "bicycle", "web_swing", "rocket")),
        ("world", "scene", ("room", "terrace", "cloud")),
        ("world", "weather", ("clear", "rain", "snow", "night")),
        ("world", "camera", ("front", "left", "right", "close", "overhead")),
        ("fishing", "rank_control", ("either", "fishing", "gifts", "admins")),
        ("fishing", "day_mode", ("cycle", "realtime", "day", "night")),
    ):
        value = data.get(section, {}).get(key)
        if value is not None and value not in choices:
            raise ValueError(f"{section}.{key}不是有效选项")
    for section, key, lower, upper in (
        ("ingest", "bridge_port", 1, 65535),
        ("llm", "max_tokens", 1, 100000),
        ("llm", "temperature", 0, 2),
        ("pace", "max_reply_chars", 1, 2000),
        ("filters", "max_input_chars", 1, 10000),
        ("like_goal", "step", 1, 1000000),
        ("fishing", "likes_per_pull", 1, 10000),
        ("fishing", "day_cycle", 120, 7200),
        ("fishing", "net_multiplier", 1, 1000),
        ("stage", "subtitle_size", 14, 26),
    ):
        value = data.get(section, {}).get(key)
        if value is not None and (not lower <= value <= upper or (key != "temperature" and int(value) != value)):
            raise ValueError(f"{section}.{key}必须在 {lower}～{upper} 范围内")
    for key in ("rate", "volume"):
        value = data.get("tts", {}).get(key)
        if value and not re.fullmatch(r"[+-]\d+%", value):
            raise ValueError(f"tts.{key}格式应为 +8% 或 -10%")
    for key, lines in data.get("lines", {}).items():
        if key not in DEFAULTS["lines"]:
            continue
        allowed = LINE_PARAMETERS.get(key, set())
        if len(lines) > 100 or any(len(line) > 2000 for line in lines):
            raise ValueError(f"lines.{key}最多 100 条，每条最多 2000 字")
        for line in lines:
            for _, field, spec, conversion in Formatter().parse(line):
                if field is not None and (field not in allowed or spec or conversion):
                    raise ValueError(f"lines.{key}包含不支持的占位符：{field}")
    stage = data.get("stage", {})
    fishing = data.get("fishing", {})
    if len(fishing.get("admin_ids", "")) > 4000:
        raise ValueError("管理员用户ID最多4000字")
    if not 0 <= fishing.get("item_chance", .18) <= .5:
        raise ValueError("趣味物品概率应在0～0.5之间")
    if not .01 <= fishing.get("loosen_amount", .08) <= .25:
        raise ValueError("松线每次减少的进度须在 0.01～0.25 之间")
    for prefix in ("bite", "fight"):
        low, high = fishing.get(prefix + "_min", DEFAULTS["fishing"][prefix + "_min"]), fishing.get(prefix + "_max", DEFAULTS["fishing"][prefix + "_max"])
        if not 3 <= low <= high <= 300:
            raise ValueError("钓鱼随机时长应在 3～300 秒之间，最短时间不能大于最长时间")
    if not 2 <= fishing.get("gift_pulls", 5) <= 30:
        raise ValueError("每件礼物的拉钩力度应在 2～30 次之间")
    if "escape_chance" in fishing and not 0 <= fishing["escape_chance"] <= .5:
        raise ValueError("每次拉钩的脱钩概率应在 0～0.5 范围内")
    ticker = data.get("ticker", {})
    if "speed" in ticker and not 5 <= ticker["speed"] <= 240:
        raise ValueError("滚动速度应在 5～240 范围内")
    if len(ticker.get("text", "")) > 2000:
        raise ValueError("滚动公告最多 2000 字")
    if "cooldown" in data.get("world", {}) and not 0 <= data["world"]["cooldown"] <= 120:
        raise ValueError("场景控制间隔应在 0～120 秒")
    if "outfit" in stage and stage["outfit"] not in SKINS:
        raise ValueError("未知人物服装")
    if "avatar_mode" in stage and stage["avatar_mode"] not in ("layered", "image"):
        raise ValueError("人物模式应为分层骨架或原始立绘")
    idle = data.get("idle", {})
    if "dream_seconds" in idle and not 15 <= idle["dream_seconds"] <= 3600:
        raise ValueError("梦话间隔须为 15～3600 秒")
    for key in ("volume", "duck"):
        value = data.get("bgm", {}).get(key)
        if value is not None and not 0 <= value <= 1:
            raise ValueError(f"背景 BGM 的{key}须为 0～1")
    if len(data.get("bgm", {}).get("path", "")) > 2048:
        raise ValueError("背景 BGM 路径过长")
    if "invite_seconds" in idle and not 30 <= idle["invite_seconds"] <= 3600:
        raise ValueError("玩法邀请间隔须为 30～3600 秒")
    if "action_seconds" in idle and not 5 <= idle["action_seconds"] <= 3600:
        raise ValueError("冷场动作间隔应在 5～3600 秒")
    if idle.get("action_mode", "cycle") not in ("cycle", "random"):
        raise ValueError("冷场动作模式应为 cycle 或 random")
    if "actions" in idle:
        from .native.actions import ACTIONS
        if len(idle["actions"]) > len(ACTIONS) or any(action not in ACTIONS for action in idle["actions"]):
            raise ValueError("冷场动作列表包含未知动作")
    for rule in data.get("interact", {}).get("rules", []):
        for _, field, spec, conversion in Formatter().parse(rule.get("reply") or ""):
            if field is not None and (field not in {"name", "gift", "count"} or spec or conversion):
                raise ValueError(f"互动台词包含不支持的占位符：{field}")


def _initial_defaults() -> dict:
    from .config import settings as env

    data = copy.deepcopy(DEFAULTS)
    for section, fields in {
        "persona": ("name", "style"),
        "llm": ("temperature", "max_tokens"),
        "tts": ("engine", "voice", "rate", "volume"),
        "pace": ("min_speak_gap", "idle_chat_seconds", "announce_enter", "announce_like", "enter_merge_window"),
    }.items():
        for field in fields:
            attribute = field if section == "pace" else f"{section}_{field}"
            data[section][field] = getattr(env, attribute)
    data["ingest"].update(source=env.ingest, douyin_room=env.douyin_room, bridge_port=env.bridge_port)
    return data


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, Any] = _initial_defaults()
        self._mtime = 0.0
        self.load()

    # ---------------- 读写 ----------------
    def load(self) -> None:
        with self._lock:
            if CONFIG_FILE.exists():
                try:
                    raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
                    _validate_config(raw)
                    self._data = _deep_merge(_initial_defaults(), raw)
                    self._mtime = CONFIG_FILE.stat().st_mtime
                    return
                except Exception as exc:
                    log.error("运行配置读取失败，使用默认值：%s", exc)
                    self._data = _initial_defaults()
                    return
            self._data = _initial_defaults()
            self.save()

    def save(self) -> None:
        with self._lock:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(CONFIG_FILE)  # 原子替换
            self._mtime = CONFIG_FILE.stat().st_mtime

    def get_all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def section(self, name: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data.get(name, {}))

    def update(self, patch: dict) -> dict:
        """局部更新（深合并），返回更新后的完整配置。"""
        with self._lock:
            _validate_config(patch)
            previous = self._data
            self._data = _deep_merge(previous, patch)
            try:
                self.save()
            except Exception:
                self._data = previous
                raise
            return copy.deepcopy(self._data)

    def reset(self) -> dict:
        with self._lock:
            previous = self._data
            self._data = _initial_defaults()
            try:
                self.save()
            except Exception:
                self._data = previous
                raise
            return copy.deepcopy(self._data)

    # ---------------- 常用取值 ----------------
    @property
    def persona_name(self) -> str:
        return str(self.section("persona").get("name") or "大肥鱼")

    def lines(self, key: str) -> list[str]:
        got = self.section("lines").get(key) or DEFAULTS["lines"].get(key, [])
        return [str(x) for x in got if str(x).strip()]

    def banned_words(self) -> list[str]:
        return [str(w) for w in (self.section("filters").get("banned_words") or []) if str(w).strip()]


store = SettingsStore()


class Effective:
    """运行时生效配置：把 .env（启动参数/密钥）与 runtime.json（运营可调）合并。"""

    @staticmethod
    def idle() -> dict:
        return _deep_merge(DEFAULTS["idle"], store.section("idle"))

    @staticmethod
    def llm() -> dict:
        from .config import settings as env

        cfg = store.section("llm")
        return {
            "base_url": cfg.get("base_url") or env.llm_base_url,
            "api_key": cfg.get("api_key") or env.llm_api_key,
            "model": cfg.get("model") or env.llm_model,
            "temperature": float(cfg.get("temperature", env.llm_temperature)),
            "max_tokens": int(cfg.get("max_tokens", env.llm_max_tokens)),
            "prompt_mode": (
                str(cfg.get("prompt_mode") or "auto").lower()
                if str(cfg.get("prompt_mode") or "auto").lower()
                in ("auto", "system", "user") else "auto"
            ),
            "price_in": max(0.0, float(cfg.get("price_in", 2.0))),
            "price_out": max(0.0, float(cfg.get("price_out", 8.0))),
            "currency": str(cfg.get("currency") or "¥"),
        }

    @staticmethod
    def pricing() -> dict:
        c = Effective.llm()
        return {"input_per_m": c["price_in"], "output_per_m": c["price_out"]}

    @staticmethod
    def tts() -> dict:
        from .config import settings as env

        cfg = store.section("tts")
        return {
            "engine": cfg.get("engine") or env.tts_engine,
            "voice": cfg.get("voice") or env.tts_voice,
            "rate": cfg.get("rate") or env.tts_rate,
            "volume": cfg.get("volume") or env.tts_volume,
        }

    @staticmethod
    def pace() -> dict:
        from .config import settings as env

        cfg = store.section("pace")
        return {
            "min_speak_gap": float(cfg.get("min_speak_gap", env.min_speak_gap)),
            "idle_chat_seconds": float(cfg.get("idle_chat_seconds", env.idle_chat_seconds)),
            "announce_enter": bool(cfg.get("announce_enter", env.announce_enter)),
            "announce_like": bool(cfg.get("announce_like", env.announce_like)),
            "announce_follow": bool(cfg.get("announce_follow", True)),
            "enter_merge_window": float(cfg.get("enter_merge_window", env.enter_merge_window)),
            "max_reply_chars": int(cfg.get("max_reply_chars", 60)),
        }

    @staticmethod
    def ingest() -> dict:
        from .config import settings as env

        cfg = store.section("ingest")
        return {
            "enabled": True if env.demo else bool(cfg.get("enabled", True)),
            "source": "mock" if env.demo else (cfg.get("source") or env.ingest),
            "douyin_room": cfg.get("douyin_room") or env.douyin_room,
            "bridge_port": int(cfg.get("bridge_port", env.bridge_port)),
        }

    @staticmethod
    def filters() -> dict:
        cfg = store.section("filters")
        return {
            "banned_words": store.banned_words(),
            "max_input_chars": int(cfg.get("max_input_chars", 120)),
            "per_user_cooldown": float(cfg.get("per_user_cooldown", 4.0)),
            "dedupe_seconds": float(cfg.get("dedupe_seconds", 90)),
        }

    @staticmethod
    def scoring() -> dict:
        return store.section("scoring")

    @staticmethod
    def stage() -> dict:
        cfg = store.section("stage")
        return {
            "model": str(cfg.get("model") or ""),
            "scale": _clamp(cfg.get("scale", 1.0), 0.2, 3.0, 1.0),
            "offset_x": _clamp(cfg.get("offset_x", 0.0), -0.6, 0.6, 0.0),
            "offset_y": _clamp(cfg.get("offset_y", 0.0), -0.5, 0.5, 0.0),
            "flip": bool(cfg.get("flip", False)),
            "opacity": _clamp(cfg.get("opacity", 1.0), 0.1, 1.0, 1.0),
            "auto_layout": bool(cfg.get("auto_layout", True)),
            "show_danmu": bool(cfg.get("show_danmu", True)),
            "show_subtitle": bool(cfg.get("show_subtitle", True)),
            "subtitle_size": max(14, min(26, int(cfg.get("subtitle_size", 20)))),
            "subtitle_backing": bool(cfg.get("subtitle_backing", False)),
            "show_datapods": bool(cfg.get("show_datapods", True)),
            "show_tokens": bool(cfg.get("show_tokens", True)),
            "show_ranking": bool(cfg.get("show_ranking", True)),
            "show_ballpit": bool(cfg.get("show_ballpit", True)),
            "show_badges": bool(cfg.get("show_badges", True)),
            "native_effects": bool(cfg.get("native_effects", True)),
            "native_music_motion": bool(cfg.get("native_music_motion", True)),
            "scene_reactions": bool(cfg.get("scene_reactions", True)),
            "costume": cfg.get("costume") if cfg.get("costume") in ("original", "crown", "cool", "festival") else "original",
            "outfit": cfg.get("outfit") if cfg.get("outfit") in SKINS else "sailor",
            "avatar_mode": "image" if cfg.get("avatar_mode") == "image" else "layered",
            "show_bones": bool(cfg.get("show_bones", False)),
            "video_enabled": bool(cfg.get("video_enabled", True)),
            "video_path": str(cfg.get("video_path") or "data/interaction-media/奶蛙.webm"),
            "video_keywords": str(cfg.get("video_keywords") or "奶蛙,奶娃,小奶蛙")[:300],
            "video_volume": _clamp(cfg.get("video_volume", 0.8), 0, 1, 0.8),
            "video_scale": _clamp(cfg.get("video_scale", 1.0), 0.4, 1.4, 1.0),
            "ball_speed": _clamp(cfg.get("ball_speed", 1.0), 0.1, 3.0, 1.0),
            "ball_max": int(_clamp(cfg.get("ball_max", 10), 0, 10, 10)),
            "ball_rank_by": "count" if cfg.get("ball_rank_by") == "count" else "value",
            "ball_size": _clamp(cfg.get("ball_size", 1.0), 0.6, 1.8, 1.0),
            "ball_bounce": bool(cfg.get("ball_bounce", True)),
            "rank_max": int(_clamp(cfg.get("rank_max", 3), 1, 10, 3)),
            "rank_effect": bool(cfg.get("rank_effect", True)),
        }

    @staticmethod
    def fallback() -> dict:
        cfg = store.section("fallback")
        return {
            "offline_voice": bool(cfg.get("offline_voice", True)),
            "offline_reply": bool(cfg.get("offline_reply", True)),
        }

    @staticmethod
    def world() -> dict:
        from .world import CAMERAS, SCENES, WEATHERS
        cfg = store.section("world")
        return {"enabled": bool(cfg.get("enabled", True)),
                "scene": cfg.get("scene") if cfg.get("scene") in SCENES else "room",
                "weather": cfg.get("weather") if cfg.get("weather") in WEATHERS else "clear",
                "camera": cfg.get("camera") if cfg.get("camera") in CAMERAS else "front",
                "chat_control": bool(cfg.get("chat_control", True)),
                "cooldown": _clamp(cfg.get("cooldown", 2), 0, 120, 2),
                "particles": bool(cfg.get("particles", True))}

    @staticmethod
    def fishing() -> dict:
        cfg = store.section("fishing")
        timing = {}
        for prefix, low, high in (("bite", 12, 28), ("fight", 25, 45)):
            minimum = int(_clamp(cfg.get(prefix + "_min", low), 3, 300, low))
            timing[prefix + "_min"] = minimum
            timing[prefix + "_max"] = max(minimum, int(_clamp(cfg.get(prefix + "_max", high), 3, 300, high)))
        return {"enabled": bool(cfg.get("enabled", True)),
                "dedicated": bool(cfg.get("dedicated", False)), "voice": bool(cfg.get("voice", True)),
                "likes_per_pull": int(_clamp(cfg.get("likes_per_pull", 10), 1, 10000, 10)),
                "escape_chance": _clamp(cfg.get("escape_chance", .06), 0, .5, .06),
                "gift_pulls": int(_clamp(cfg.get("gift_pulls", 5), 2, 30, 5)), **timing,
                "chat_switch": bool(cfg.get("chat_switch", True)), "rank_control": cfg.get("rank_control", "either"),
                "admin_ids": str(cfg.get("admin_ids", "")),
                "item_chance": _clamp(cfg.get("item_chance", .18), 0, .5, .18),
                "day_mode": cfg.get("day_mode", "cycle") if cfg.get("day_mode", "cycle") in ("cycle", "realtime", "day", "night") else "cycle",
                "day_cycle": int(_clamp(cfg.get("day_cycle", 600), 120, 7200, 600)),
                "net_enabled": bool(cfg.get("net_enabled", True)),
                "net_multiplier": int(_clamp(cfg.get("net_multiplier", 20), 1, 1000, 20)),
                "loosen_enabled": bool(cfg.get("loosen_enabled", True)),
                "loosen_amount": _clamp(cfg.get("loosen_amount", .08), .01, .25, .08)}

    @staticmethod
    def gift_show() -> dict:
        return _deep_merge(DEFAULTS["gift_show"], store.section("gift_show"))

    @staticmethod
    def bgm() -> dict:
        return _deep_merge(DEFAULTS["bgm"], store.section("bgm"))

    @staticmethod
    def ticker() -> dict:
        cfg = store.section("ticker")
        return {"enabled": bool(cfg.get("enabled", True)), "text": str(cfg.get("text", ""))[:2000],
                "speed": _clamp(cfg.get("speed", 55), 5, 240, 55)}

    @staticmethod
    def like_goal() -> dict:
        cfg = store.section("like_goal")
        mode = cfg.get("mode", "cycle")
        return {"enabled": bool(cfg.get("enabled", True)),
                "step": int(_clamp(cfg.get("step", 100), 1, 1000000, 100)),
                "cooldown": _clamp(cfg.get("cooldown", 5), 0, 120, 5),
                "mode": mode if mode in ("cycle", "random", "swim", "bicycle", "web_swing", "rocket") else "cycle",
                "show_progress": bool(cfg.get("show_progress", True))}

    @staticmethod
    def templates() -> dict:
        cfg = store.section("templates")
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "gift": bool(cfg.get("gift", True)),
            "follow": bool(cfg.get("follow", True)),
            "welcome": bool(cfg.get("welcome", True)),
            "idle": bool(cfg.get("idle", True)),
            "prefer_llm": bool(cfg.get("prefer_llm", True)),
        }

    @staticmethod
    def music() -> dict:
        cfg = store.section("music")
        prov = str(cfg.get("providers") or "local,netease,qq")
        provs = [p.strip() for p in prov.split(",") if p.strip() in ("local", "netease", "qq")]
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "autoplay": bool(cfg.get("autoplay", True)),
            "gift_priority": bool(cfg.get("gift_priority", True)),
            "gift_min": max(0, int(_clamp(cfg.get("gift_min", 1), 0, 100000, 1))),
            "ai_dj": bool(cfg.get("ai_dj", True)),
            "ai_allowlist": str(cfg.get("ai_allowlist") or ""),
            "blocked_keywords": str(cfg.get("blocked_keywords") or ""),
            "volume": _clamp(cfg.get("volume", 0.6), 0.0, 1.0, 0.6),
            "duck": _clamp(cfg.get("duck", 0.35), 0.0, 1.0, 0.35),
            "show_player": bool(cfg.get("show_player", True)),
            "cookie": str(cfg.get("cookie") or ""),
            "providers": provs or ["local", "netease", "qq"],
        }

    @staticmethod
    def interact() -> dict:
        cfg = store.section("interact")
        rules = cfg.get("rules") or []
        clean = []
        for r in rules:
            if not isinstance(r, dict):
                continue
            on = str(r.get("on") or "chat")
            if on not in ("chat", "gift", "follow", "enter", "like", "any"):
                continue
            clean.append({
                "id": str(r.get("id") or ""),
                "on": on,
                "match": str(r.get("match") or ""),
                "min_value": _clamp(r.get("min_value", 0), 0, 1e9, 0, cast=int),
                "motion": str(r.get("motion") or ""),
                "expression": str(r.get("expression") or ""),
                "effect": str(r.get("effect") or ""),
                "reply": str(r.get("reply") or ""),
                "cooldown": _clamp(r.get("cooldown", 0), 0, 600, 0),
            })
        return {"enabled": bool(cfg.get("enabled", True)), "rules": clean}


def _clamp(v, lo, hi, default, cast=float):
    try:
        x = cast(v)
    except Exception:
        return default
    return max(lo, min(hi, x))
