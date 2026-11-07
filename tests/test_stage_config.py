"""后台可配置项测试：画面开关、气泡手感、兜底与接入总开关。

这些配置直接决定直播画面上有什么，改错了主播会很难受，
所以把边界和默认值都钉死。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app import settings_store
from app.ingest.base import OffIngestor, build_ingestor
from app.settings_store import Effective, store


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """所有用例都在内存副本上跑，绝不碰真实 runtime.json。

    注意：基线必须用 DEFAULTS（代码里的出厂默认），不能用当前 store._data——
    否则用户把某个开关关掉后，这些"默认应为开"的断言就会假失败。
    """
    baseline = copy.deepcopy(settings_store.DEFAULTS)
    monkeypatch.setattr(store, "_data", copy.deepcopy(baseline), raising=False)
    monkeypatch.setattr(store, "update", lambda patch: {**store._data, **(patch or {})},
                        raising=False)
    yield
    store._data.clear()
    store._data.update(copy.deepcopy(baseline))


# ------------------------------------------------------------------ 画面开关
def test_blocks_shown_by_default():
    st = Effective.stage()
    for k in ("show_danmu", "show_subtitle", "show_datapods",
              "show_ranking", "show_ballpit", "show_badges"):
        assert st[k] is True, f"{k} 默认应为开"


def test_blocks_can_be_turned_off():
    store._data["stage"] = {"show_datapods": False, "show_ranking": False,
                            "show_ballpit": False, "show_badges": False}
    st = Effective.stage()
    assert st["show_datapods"] is False
    assert st["show_ranking"] is False
    assert st["show_ballpit"] is False
    assert st["show_badges"] is False


# ------------------------------------------------------------------ 气泡手感
def test_ball_speed_clamped():
    store._data["stage"] = {"ball_speed": 99}
    assert Effective.stage()["ball_speed"] == 3.0
    store._data["stage"] = {"ball_speed": -5}
    assert Effective.stage()["ball_speed"] == 0.1


def test_ball_speed_passthrough():
    store._data["stage"] = {"ball_speed": 0.4}
    assert Effective.stage()["ball_speed"] == pytest.approx(0.4)


def test_ball_max_clamped_and_zero_allowed():
    store._data["stage"] = {"ball_max": 999}
    assert Effective.stage()["ball_max"] == 10
    # 0 是合法值，表示不显示，不能被夹成 1
    store._data["stage"] = {"ball_max": 0}
    assert Effective.stage()["ball_max"] == 0
    store._data["stage"] = {"ball_max": -3}
    assert Effective.stage()["ball_max"] == 0


def test_ball_size_clamped():
    store._data["stage"] = {"ball_size": 5}
    assert Effective.stage()["ball_size"] == 1.8
    store._data["stage"] = {"ball_size": 0.1}
    assert Effective.stage()["ball_size"] == 0.6


def test_ball_bounce_toggle():
    store._data["stage"] = {"ball_bounce": False}
    assert Effective.stage()["ball_bounce"] is False
    store._data["stage"] = {"ball_bounce": True}
    assert Effective.stage()["ball_bounce"] is True


# ------------------------------------------------------------------ 排行榜
def test_rank_max_clamped():
    store._data["stage"] = {"rank_max": 99}
    assert Effective.stage()["rank_max"] == 10
    store._data["stage"] = {"rank_max": 0}
    assert Effective.stage()["rank_max"] == 1


def test_rank_effect_toggle():
    store._data["stage"] = {"rank_effect": False}
    assert Effective.stage()["rank_effect"] is False


# ------------------------------------------------------------------ 兜底
def test_fallback_defaults_on():
    fb = Effective.fallback()
    assert fb["offline_reply"] is True
    assert fb["offline_voice"] is True


def test_fallback_can_be_disabled():
    store._data["fallback"] = {"offline_reply": False, "offline_voice": False}
    fb = Effective.fallback()
    assert fb["offline_reply"] is False
    assert fb["offline_voice"] is False


def test_fallback_on_helper_follows_setting():
    from app.brain.llm import _fallback_on

    store._data["fallback"] = {"offline_reply": False}
    assert _fallback_on() is False
    store._data["fallback"] = {"offline_reply": True}
    assert _fallback_on() is True


# ------------------------------------------------------------------ 接入总开关
def test_ingest_enabled_by_default():
    assert Effective.ingest()["enabled"] is True


def test_ingest_off_builds_off_ingestor():
    store._data["ingest"] = {"enabled": False, "source": "douyin", "douyin_room": "123"}
    ing = build_ingestor()
    assert isinstance(ing, OffIngestor)
    assert ing.name == "off"


def test_ingest_off_beats_source():
    """即使 source=douyin，总开关关掉也必须不连抖音。"""
    store._data["ingest"] = {"enabled": False, "source": "douyin",
                             "douyin_room": "699666002335"}
    assert build_ingestor().name == "off"


def test_ingest_on_builds_real_ingestor():
    store._data["ingest"] = {"enabled": True, "source": "douyin",
                             "douyin_room": "699666002335"}
    ing = build_ingestor()
    assert ing.name == "douyin"
    assert ing.room == "699666002335"


# ------------------------------------------------------------------ 提交过滤
def test_unknown_keys_dropped():
    """提交没声明的键不应污染配置。"""
    store.update({"stage": {"ball_speed": 1.5, "bogus_key": 1}})
    assert "bogus_key" not in Effective.stage()
