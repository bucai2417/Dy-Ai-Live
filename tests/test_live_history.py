from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3

from fastapi.testclient import TestClient

from app import main
from app.events import Event
from app.hub import Hub
from app.like_goal import LikeGoal
from app.live_history import LiveHistory
from app.metrics import Metrics
from tests.test_runtime import isolated_runtime


def history(path):
    return LiveHistory(path, Metrics(), Hub(), LikeGoal())


def fill(journal, enqueue=True):
    journal.metrics.add_tokens(12345, 678, estimated=True)
    journal.metrics.add_offline()
    journal.metrics.add_reply()
    for index in range(14):
        gift = Event(kind="gift", user_name="中文观众" + str(index), gift_value=index + 1, avatar="https://example.org/头像.png")
        journal.metrics.add_gift(gift)
        if enqueue:
            journal.hub.feed(gift)
        else:
            journal.hub.stats["gift_total"] += 1
    for _ in range(3):
        if enqueue:
            journal.hub.feed(Event(kind="chat", text="同一句弹幕"))
        else:
            journal.hub.stats["danmu_total"] += 1
    journal.likes.add(Event(kind="like", like_count=125, like_total=3000, like_source="douyin:123"))


def test_restart_restores_all_daily_counts_full_ranking_and_threshold(tmp_path):
    path = tmp_path / "中文统计.sqlite3"
    first = history(path)
    fill(first)
    first.write(*first.capture())
    second = history(path)
    assert second.restore()
    assert second.metrics.snapshot()["tokens"] == 13023
    assert second.metrics.estimated and second.metrics.offline_calls == 1
    assert second.metrics.replies == 1 and second.hub.stats["danmu_total"] == 3
    assert len(second.metrics.gift_board.records) == 14
    assert second.metrics.ranking()[0]["name"] == "中文观众13"
    assert second.likes.total == 125 and second.likes.next_target == 200
    assert second.likes.active is None and not second.likes.pending
    second.likes.add(Event(kind="like", like_count=125, like_total=3000, like_source="douyin:123"))
    assert second.likes.total == 125
    second.likes.add(Event(kind="like", like_count=5, like_total=3005, like_source="douyin:123"))
    assert second.likes.total == 130
    second.metrics.add_gift(Event(kind="gift", user_name="中文观众0", gift_value=100))
    assert second.metrics.ranking()[0]["name"] == "中文观众0"


def test_gift_retransmission_after_restart_does_not_inflate_tribute(tmp_path):
    first = history(tmp_path / "礼物记忆.sqlite3")
    gift = Event(kind="gift", id="gift-unique", user_id="supporter", user_name="星光鱼友", gift_count=2, gift_value=10)
    assert first.metrics.add_gift(gift)
    first.write(*first.capture())
    second = history(first.path)
    assert second.restore()
    assert not second.metrics.add_gift(gift)
    assert second.metrics.gift_board.tribute(gift.to_dict()) == {"first": True, "daily_count": 2}
    gift.id = "gift-another"
    assert second.metrics.add_gift(gift)
    assert second.metrics.gift_board.tribute(gift.to_dict()) == {"first": False, "daily_count": 4}


def test_rollover_keeps_history_and_starts_today_at_zero(tmp_path, monkeypatch):
    journal = history(tmp_path / "daily.sqlite3")
    fill(journal)
    old_day = journal.metrics.day
    journal.write(*journal.capture())
    original = dt.date
    class Tomorrow(original):
        @classmethod
        def today(cls):
            return original.fromisoformat(old_day) + dt.timedelta(days=1)
    monkeypatch.setattr(dt, "date", Tomorrow)
    assert not history(journal.path).restore()
    day, data = journal.capture()
    assert day != old_day
    assert data["metrics"]["tokens_in"] == data["events"]["danmu_total"] == data["likes"]["total"] == 0
    assert data["gift_board"] == {}
    assert journal.likes.tick()
    assert not journal.likes.tick()
    journal.write(day, data)
    with sqlite3.connect(journal.path) as connection:
        assert connection.execute("SELECT count(*) FROM days").fetchone()[0] == 2
        previous = json.loads(connection.execute("SELECT payload FROM days WHERE day=?", (old_day,)).fetchone()[0])
        assert previous["metrics"]["tokens_in"] == 12345


def test_corrupt_database_is_reported_and_preserved(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    damaged = "损坏的数据库".encode("utf-8")
    path.write_bytes(damaged)
    journal = history(path)
    assert not journal.restore()
    journal.write(*journal.capture())
    assert path.read_bytes() == damaged
    assert "读取失败" in journal.metrics.snapshot()["storage"]["error"]


def test_invalid_payload_does_not_partially_restore(tmp_path):
    journal = history(tmp_path / "invalid.sqlite3")
    fill(journal)
    day, data = journal.capture()
    data["likes"]["total"] = -8
    journal.write(day, data)
    restored = history(journal.path)
    assert not restored.restore()
    assert restored.metrics.tokens_in == restored.likes.total == 0
    assert restored.metrics.gift_board.records == {}


def test_shutdown_flushes_latest_changes(tmp_path):
    async def scenario():
        journal = history(tmp_path / "flush.sqlite3")
        stop = asyncio.Event()
        fill(journal)
        stop.set()
        await journal.run(stop)
        restored = history(journal.path)
        assert restored.restore() and restored.hub.stats["danmu_total"] == 3
    asyncio.run(scenario())


def test_save_failure_is_visible_then_retries(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")
    journal = history(parent / "daily.sqlite3")
    fill(journal)
    journal.write(*journal.capture())
    assert journal.status["error"] and journal.previous is None
    journal.path = tmp_path / "recovered.sqlite3"
    journal.write(*journal.capture())
    assert not journal.status["error"] and journal.status["savedAt"]


def test_explicit_session_clear_preserves_day_and_server_restart(tmp_path):
    with TestClient(main.app) as client:
        journal = main.app.state.history
        fill(journal, enqueue=False)
        journal.likes.tick()
        assert client.post("/api/session/boot").json()["ok"]
        assert journal.metrics.gifts == 14 and journal.likes.total == 125
        assert len(journal.metrics.gift_board.records) == 14
        assert not journal.likes.active and not journal.likes.pending
    restored = history(journal.path)
    assert restored.restore()
    assert restored.likes.total == 125 and restored.metrics.tokens_in == 12345
