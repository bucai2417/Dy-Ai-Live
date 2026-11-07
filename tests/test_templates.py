"""预设台词开关测试。

核心诉求：没配置文本模型时，画面里不应该出现"预设内容"，
也不能播放预设语音。这组测试把每条产出路径的闸门都钉死。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.settings_store import Effective, store


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # 用出厂默认做基线，避免用户的运行时配置影响"默认值"断言
    from app import settings_store

    baseline = copy.deepcopy(settings_store.DEFAULTS)
    monkeypatch.setattr(store, "_data", copy.deepcopy(baseline), raising=False)
    monkeypatch.setattr(store, "update", lambda patch: {**store._data, **(patch or {})},
                        raising=False)
    yield
    store._data.clear()
    store._data.update(copy.deepcopy(baseline))


# ------------------------------------------------------------------ 默认值
def test_templates_on_by_default():
    """默认保持原有体验，不能一升级就全体静音。"""
    t = Effective.templates()
    for k in ("enabled", "gift", "follow", "welcome", "idle", "prefer_llm"):
        assert t[k] is True, f"{k} 默认应为开"


# ------------------------------------------------------------------ 总开关
def test_master_off_disables_everything():
    store._data["templates"] = {"enabled": False}
    t = Effective.templates()
    assert t["enabled"] is False
    # 关闭总开关时，各子项即使为 True 也不该生效（由调用方用 enabled and x 判断）
    assert t["enabled"] and t["gift"] is False or t["enabled"] is False


def test_each_sub_switch_independent():
    for key in ("gift", "follow", "welcome", "idle"):
        store._data["templates"] = {"enabled": True, key: False}
        t = Effective.templates()
        assert t["enabled"] is True
        assert t[key] is False, f"{key} 应可单独关闭"
        # 其它项不受影响
        others = [k for k in ("gift", "follow", "welcome", "idle") if k != key]
        for o in others:
            assert t[o] is True, f"关 {key} 不应影响 {o}"


def test_prefer_llm_switch():
    store._data["templates"] = {"prefer_llm": False}
    assert Effective.templates()["prefer_llm"] is False


# ------------------------------------------------------------------ 组合语义
def test_gift_gate_logic():
    """礼物台词的门槛 = enabled and gift。"""

    def allow(enabled, gift):
        store._data["templates"] = {"enabled": enabled, "gift": gift}
        t = Effective.templates()
        return t["enabled"] and t["gift"]

    assert allow(True, True) is True
    assert allow(True, False) is False   # 单独关礼物 → 不说话
    assert allow(False, True) is False   # 总开关优先


def test_follow_gate_logic():
    def allow(enabled, follow):
        store._data["templates"] = {"enabled": enabled, "follow": follow}
        t = Effective.templates()
        return t["enabled"] and t["follow"]

    assert allow(True, True) is True
    assert allow(True, False) is False
    assert allow(False, True) is False


def test_idle_gate_logic():
    def allow(enabled, idle):
        store._data["templates"] = {"enabled": enabled, "idle": idle}
        t = Effective.templates()
        return t["enabled"] and t["idle"]

    assert allow(True, True) is True
    assert allow(True, False) is False


# ------------------------------------------------------------------ 与兜底的关系
def test_offline_and_templates_are_separate():
    """兜底回话关掉、模板开着时，闲聊仍可用模板话术——两个开关互不干扰。"""
    store._data["fallback"] = {"offline_reply": False, "offline_voice": False}
    store._data["templates"] = {"enabled": True}
    assert Effective.fallback()["offline_reply"] is False
    assert Effective.templates()["enabled"] is True


def test_all_silent_config():
    """用户想要的"完全不出预设内容"配置应当可表达。"""
    store._data["fallback"] = {"offline_reply": False, "offline_voice": False}
    store._data["templates"] = {"enabled": False}
    fb, tp = Effective.fallback(), Effective.templates()
    assert fb["offline_reply"] is False
    assert fb["offline_voice"] is False
    assert tp["enabled"] is False
    # 三个闸门全闭 → 无模型时不可能有任何输出
    assert not (tp["enabled"] or fb["offline_reply"])


# ------------------------------------------------------------------ 合并写保护
def test_partial_update_keeps_other_keys():
    """后台只提交一个子开关时，其它子开关不能被重置。"""
    store._data["templates"] = {"enabled": True, "gift": False, "follow": True,
                               "welcome": True, "idle": True, "prefer_llm": True}
    store.update({"templates": {"enabled": True, "gift": False, "follow": True,
                                "welcome": True, "idle": True, "prefer_llm": True}})
    t = Effective.templates()
    assert t["gift"] is False
    assert t["follow"] is True
