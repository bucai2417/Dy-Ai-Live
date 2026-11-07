"""互动规则引擎测试：礼物分级、关键词匹配、冷却、占位符替换。"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.brain.interact import InteractEngine, pick_expression
from app.events import Event
from app.settings_store import store


@pytest.fixture(autouse=True)
def clean_rules(monkeypatch):
    """每个用例前装一套确定的规则。

    注意：只改内存中的副本，**不能调 store.update()**——那会把测试数据
    写进真实的 data/runtime.json，把用户的生产配置覆盖掉。
    """
    original = store._data

    def install(rules_obj):
        data = copy.deepcopy(original)
        data["interact"] = rules_obj
        store._data = data

    install({
        "enabled": True,
        "rules": [
            {"id": "big", "on": "gift", "min_value": 500, "match": "",
             "motion": "happy", "expression": "auto", "effect": "confetti",
             "reply": "大礼！谢谢{name}的{gift}", "cooldown": 0},
            {"id": "mid", "on": "gift", "min_value": 50, "match": "",
             "motion": "happy", "expression": "auto", "effect": "hearts",
             "reply": "谢谢{name}的{gift}", "cooldown": 0},
            {"id": "small", "on": "gift", "min_value": 0, "match": "",
             "motion": "shake", "expression": "auto", "effect": "sparkle",
             "reply": "谢啦", "cooldown": 0},
            {"id": "k666", "on": "chat", "min_value": 0, "match": "666,牛,厉害",
             "motion": "happy", "expression": "auto", "effect": "sparkle",
             "reply": "", "cooldown": 0},
            {"id": "kdance", "on": "chat", "min_value": 0, "match": "跳个舞,跳舞",
             "motion": "happy", "expression": "auto", "effect": "confetti",
             "reply": "行吧我扭两下", "cooldown": 0},
            {"id": "kflower", "on": "chat", "min_value": 0, "match": "送花",
             "motion": "happy", "expression": "auto", "effect": "hearts",
             "reply": "谢谢你的花", "cooldown": 0},
        ],
    })
    monkeypatch.setattr(store, "update", lambda patch: (install(patch.get("interact", {})), store.get_all())[1])
    yield install
    store._data = original


def gift(value: int, name: str = "土豪", gift_name: str = "嘉年华", count: int = 1) -> Event:
    return Event(kind="gift", user_name=name, gift_name=gift_name,
                 gift_count=count, gift_value=value, text=f"送出了{gift_name}")


def chat(text: str, name: str = "小明") -> Event:
    return Event(kind="chat", user_name=name, text=text)


engine = InteractEngine()


# ---------------------------------------------------------------- 礼物分级
def test_gift_big_hits_highest_rule():
    hit = engine.match(gift(3000))
    assert hit is not None
    assert hit.rule["id"] == "big", "3000 价值必须命中最高档，不能被小心心那条截走"
    assert hit.effect == "confetti"


def test_gift_mid_and_small():
    assert engine.match(gift(80)).rule["id"] == "mid"
    assert engine.match(gift(1)).rule["id"] == "small"


def test_gift_order_independent_of_config_order():
    """规则在配置里乱序也要按价值正确分级。"""
    store.update({"interact": {"enabled": True, "rules": [
        {"id": "s", "on": "gift", "min_value": 0, "motion": "shake", "reply": "小", "cooldown": 0},
        {"id": "b", "on": "gift", "min_value": 1000, "motion": "happy", "reply": "大", "cooldown": 0},
    ]}})
    e = InteractEngine()
    assert e.match(gift(2000)).rule["id"] == "b"


# ---------------------------------------------------------------- 关键词
def test_chat_keyword_match():
    hit = engine.match(chat("主播真牛逼666"))
    assert hit is not None and hit.rule["id"] == "k666"
    # 这条规则没配台词 → 只做视觉反馈
    assert hit.reply == ""
    assert hit.has_visual


def test_chat_no_match_returns_none():
    assert engine.match(chat("今天天气不错")) is None


def test_chat_rule_with_reply():
    hit = engine.match(chat("主播跳个舞"))
    assert hit.rule["id"] == "kdance"
    assert hit.reply == "行吧我扭两下"


def test_match_separators():
    """关键词支持中英文逗号、顿号、竖线分隔。"""
    store.update({"interact": {"enabled": True, "rules": [
        {"id": "k", "on": "chat", "match": "苹果，香蕉、橘子|西瓜", "reply": "水果", "cooldown": 0},
    ]}})
    e = InteractEngine()
    for txt in ("我爱吃苹果", "香蕉好吃", "橘子酸", "西瓜甜"):
        assert e.match(chat(txt)) is not None, f"{txt} 应命中"


# ---------------------------------------------------------------- 占位符
def test_placeholder_render():
    hit = engine.match(gift(3000, name="王老板", gift_name="嘉年华"))
    assert "王老板" in hit.reply
    assert "嘉年华" in hit.reply


def test_gift_count_in_placeholder():
    hit = engine.match(gift(3000, gift_name="小心心", count=5))
    assert "小心心x5" in hit.reply


def test_bad_placeholder_does_not_crash():
    store.update({"interact": {"enabled": True, "rules": [
        {"id": "x", "on": "gift", "min_value": 0, "reply": "有问题的{unknown_field}", "cooldown": 0},
    ]}})
    e = InteractEngine()
    hit = e.match(gift(1))
    assert hit is not None  # 不抛异常，原文返回


# ---------------------------------------------------------------- 冷却
def test_cooldown_blocks_repeat():
    store.update({"interact": {"enabled": True, "rules": [
        {"id": "cd", "on": "chat", "match": "666", "reply": "牛", "cooldown": 300},
    ]}})
    e = InteractEngine()
    assert e.match(chat("666")) is not None
    assert e.match(chat("666")) is None, "冷却期内不应再次触发"


def test_cooldown_zero_always_fires():
    store.update({"interact": {"enabled": True, "rules": [
        {"id": "nc", "on": "chat", "match": "666", "reply": "牛", "cooldown": 0},
    ]}})
    e = InteractEngine()
    assert e.match(chat("666")) is not None
    assert e.match(chat("666")) is not None


# ---------------------------------------------------------------- 开关
def test_disabled_engine_returns_none():
    store.update({"interact": {"enabled": False, "rules": [
        {"id": "k", "on": "chat", "match": "666", "reply": "牛", "cooldown": 0},
    ]}})
    assert InteractEngine().match(chat("666")) is None


# ---------------------------------------------------------------- 视觉反馈
def test_visual_only_skips_rules_with_reply():
    e = InteractEngine()
    assert e.fire_visual_only(chat("666")) is not None       # 无台词 → 可只放视觉
    # 有台词的规则不在此返回，交给主流程播报
    e2 = InteractEngine()
    assert e2.fire_visual_only(chat("跳个舞")) is None


# ---------------------------------------------------------------- 表情
def test_auto_expression_is_numeric():
    got = pick_expression({"expression": "auto"})
    assert got.isdigit() and 0 <= int(got) <= 7


def test_fixed_expression_passthrough():
    assert pick_expression({"expression": "3"}) == "3"
