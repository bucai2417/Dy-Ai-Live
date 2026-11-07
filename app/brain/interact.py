"""互动规则引擎：让观众的动作真的能改变画面。

设计取舍：
    - 规则是「声明式」的，后台可增删改，不写死代码
    - 命中后可以同时做三件事：播动作/表情（画面）、放特效（视觉）、说台词（语音）
    - 有台词就由规则接管播报；没台词则只做视觉反馈，把发言权还给正常流程
    - 每条规则独立冷却，避免同一句话被反复触发

匹配顺序：礼物按价值从高到低，其余按配置顺序；先命中先用。
"""
from __future__ import annotations

import logging
import random
import re
import time

from ..events import Event
from ..settings_store import Effective

log = logging.getLogger(__name__)


class RuleHit:
    __slots__ = ("rule", "reply", "motion", "expression", "effect")

    def __init__(self, rule: dict, reply: str) -> None:
        self.rule = rule
        self.reply = reply
        self.motion = rule.get("motion") or ""
        self.expression = rule.get("expression") or ""
        self.effect = rule.get("effect") or ""

    @property
    def has_visual(self) -> bool:
        return bool(self.motion or self.expression or self.effect)


class InteractEngine:
    def __init__(self) -> None:
        self._last_fire: dict[str, float] = {}

    def match(self, ev: Event, ignore_cooldown: bool = False) -> RuleHit | None:
        cfg = Effective.interact()
        if not cfg["enabled"]:
            return None

        rules = cfg["rules"]
        # 礼物按价值从高到低，保证嘉年华不会被"小心心"那条先截走
        ordered = sorted(
            rules,
            key=lambda r: (-r["min_value"] if r["on"] == "gift" else 0),
        )

        now = time.time()
        for r in ordered:
            if not self._kind_ok(r["on"], ev):
                continue
            if r["on"] == "gift" and ev.gift_value < r["min_value"]:
                continue
            if r["on"] != "gift" and r["min_value"] > 0:
                continue
            if not self._text_ok(r["match"], ev):
                continue
            cd = float(r.get("cooldown") or 0)
            if not ignore_cooldown and cd and now - self._last_fire.get(r["id"] or id(r), 0) < cd:
                continue
            self._last_fire[r["id"] or id(r)] = now
            return RuleHit(r, self._render(r.get("reply") or "", ev))
        return None

    def fire_visual_only(self, ev: Event) -> RuleHit | None:
        """只取视觉反馈（不占用发言权），用于没配台词的规则。"""
        hit = self.match(ev)
        if hit and not hit.reply:
            return hit
        return None

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _kind_ok(on: str, ev: Event) -> bool:
        if on == "any":
            return True
        if on == "chat":
            return ev.kind == "chat"
        return ev.kind == on

    @staticmethod
    def _text_ok(patterns: str, ev: Event) -> bool:
        """关键词用逗号分隔，命中任意一个即可；为空表示不限制。"""
        keys = [k.strip() for k in re.split(r"[,，、|]", patterns or "") if k.strip()]
        if not keys:
            return True
        if ev.kind != "chat":
            return True
        text = ev.text or ""
        return any(k in text for k in keys)

    @staticmethod
    def _render(tpl: str, ev: Event) -> str:
        if not tpl:
            return ""
        gift = ev.gift_name or "礼物"
        if ev.gift_count and ev.gift_count > 1:
            gift = f"{gift}x{ev.gift_count}"
        try:
            return tpl.format(name=ev.user_name or "朋友", gift=gift, count=ev.gift_count)
        except Exception:
            return tpl

    def reset(self) -> None:
        self._last_fire.clear()


def pick_expression(rule: dict) -> str:
    """expression 支持 'auto'：按动作随机挑一个，避免永远同一个表情。"""
    e = (rule.get("expression") or "").strip()
    if e == "auto":
        return str(random.randint(0, 7))
    return e
