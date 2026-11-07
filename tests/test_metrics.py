"""统计模块测试：token 计费、礼物榜合并、跨天重置。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.events import Event
from app.metrics import Metrics, estimate_tokens


def gift(name: str, value: int, count: int = 1, avatar: str = "") -> Event:
    return Event(kind="gift", user_name=name, gift_name="礼物", gift_count=count,
                 gift_value=value, avatar=avatar)


@pytest.fixture()
def m():
    return Metrics()


def test_no_cost_when_no_tokens(m):
    assert m.cost() == 0


def test_cost_uses_separate_in_out_prices(m):
    m.add_tokens(1_000_000, 0)
    assert m.cost({"input_per_m": 2.0, "output_per_m": 8.0}) == 2.0
    m2 = Metrics()
    m2.add_tokens(0, 1_000_000)
    assert m2.cost({"input_per_m": 2.0, "output_per_m": 8.0}) == 8.0


def test_cost_scales_with_price(m):
    m.add_tokens(500_000, 500_000)
    cheap = m.cost({"input_per_m": 2.0, "output_per_m": 8.0})
    pricey = m.cost({"input_per_m": 20.0, "output_per_m": 80.0})
    assert pricey == pytest.approx(cheap * 10, rel=1e-6)


def test_gift_merges_same_user(m):
    """同一个人刷多次必须合并成一条，否则榜单会被小心心刷屏。"""
    m.add_gift(gift("王老板", 3000))
    m.add_gift(gift("王老板", 1200))
    rows = m.ranking()
    assert len(rows) == 1
    assert rows[0]["value"] == 4200
    assert rows[0]["count"] == 2


def test_ranking_sorted_desc(m):
    m.add_gift(gift("A", 100))
    m.add_gift(gift("B", 900))
    m.add_gift(gift("C", 500))
    rows = m.ranking()
    assert [r["name"] for r in rows] == ["B", "C", "A"]
    assert [r["rank"] for r in rows] == [1, 2, 3]


def test_ranking_limited_to_three(m):
    for i in range(6):
        m.add_gift(gift(f"u{i}", (i + 1) * 10))
    assert len(m.ranking()) == 3
    assert m.ranking()[0]["name"] == "u5"


def test_avatar_kept(m):
    m.add_gift(gift("王老板", 100, avatar="https://x/a.jpg"))
    assert m.ranking()[0]["avatar"] == "https://x/a.jpg"


def test_avatar_not_overwritten_by_empty(m):
    m.add_gift(gift("王老板", 100, avatar="https://x/a.jpg"))
    m.add_gift(gift("王老板", 100, avatar=""))
    assert m.ranking()[0]["avatar"] == "https://x/a.jpg"


def test_snapshot_counts(m):
    m.add_tokens(100, 50)
    m.add_reply()
    m.add_reply()
    m.add_gift(gift("A", 10))
    s = m.snapshot()
    assert s["tokens"] == 150
    assert s["replies"] == 2
    assert s["gifts"] == 1
    assert s["giftValue"] == 10


def test_day_rollover_resets(m, monkeypatch):
    """跨天必须归零，否则「今日消耗」会一直累加。"""
    m.add_tokens(1000, 500)
    m.add_reply()
    m.add_gift(gift("A", 100))
    monkeypatch.setattr("app.metrics._today", lambda: "2099-01-01")
    s = m.snapshot()
    assert s["tokens"] == 0
    assert s["replies"] == 0
    assert s["ranking"] == []


def test_estimate_tokens_chinese():
    assert estimate_tokens("你好世界") > 0
    assert estimate_tokens("") == 0


def test_estimated_flag_sticky(m):
    m.add_tokens(10, 10, estimated=True)
    m.add_tokens(10, 10)
    assert m.snapshot()["estimated"] is True
