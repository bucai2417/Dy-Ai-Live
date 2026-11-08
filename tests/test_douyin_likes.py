from __future__ import annotations

import asyncio
import time

import pytest

from app.brain import scheduler
from app.events import Event
from app.hub import Hub
from app.ingest import douyin_cdp, douyin_direct
from app.like_goal import LikeGoal
from app.live_history import LiveHistory
from app.metrics import Metrics
from tests.test_douyin_parse import fld_int, fld_msg, user_struct
from tests.test_runtime import isolated_runtime


def like_payload(identifier, count, total):
    payload = fld_msg(1, fld_int(2, identifier)) + fld_int(2, count)
    if total is not None:
        payload += fld_int(3, total)
    return payload + fld_msg(5, fld_int(1, 246801) + user_struct("点赞鱼友", ""))


def collector(monkeypatch, factory=douyin_cdp.DouyinCDPIngestor):
    ingest = factory()
    ingest.room = "123"
    events = []
    monkeypatch.setattr(douyin_cdp, "gunzip_any", lambda raw: raw)
    monkeypatch.setattr(douyin_cdp, "iter_envelopes", lambda raw: [("WebcastLikeMessage", raw)])
    monkeypatch.setattr(ingest, "_emit_async", events.append)
    return ingest, events


@pytest.mark.parametrize("factory", [douyin_cdp.DouyinCDPIngestor, douyin_direct.DouyinDirectIngestor])
@pytest.mark.parametrize("totals", [(0, 0), (1000, 1000), (1000, 995), (None, None)])
def test_distinct_protocol_likes_advance_fishing_despite_stale_totals(monkeypatch, factory, totals):
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("start")
        brain.fishing.config["escape_chance"] = 0
        brain.fishing.tick(brain.fishing.deadline)
        brain.fishing.deadline = time.monotonic() + 60
        brain.fishing.fish["pulls"] = 20
        ingest, events = collector(monkeypatch, factory)
        ingest._handle_frame(like_payload(101, 3, totals[0]))
        ingest._handle_frame(like_payload(102, 7, totals[1]))
        ingest._handle_frame(like_payload(102, 7, totals[1]))
        assert len(events) == 2
        for event in events:
            await brain._publish_event(event)
        assert brain.likes.total == 10
        assert brain.fishing.progress == pytest.approx(1 / 20)
        assert brain.fishing.contributors["246801"]["likes"] == 10
        assert brain.fishing.winner == {"id": "246801", "name": "点赞鱼友"}
        previous = brain.fishing.progress
        reconnected, repeated = collector(monkeypatch, factory)
        reconnected._handle_frame(like_payload(102, 7, totals[1]))
        await brain._publish_event(repeated[0])
        assert brain.likes.total == 10
        assert brain.fishing.progress == previous

    asyncio.run(scenario())


def test_protocol_counts_do_not_backfill_room_history_or_rounded_totals(monkeypatch):
    ingest, events = collector(monkeypatch)
    for identifier, count, total in ((201, 0, 50000), (202, 2, 50000), (203, 3, 50100), (204, 5, 50100)):
        ingest._handle_frame(like_payload(identifier, count, total))
    goal = LikeGoal()
    for event in events:
        goal.add(event)
    assert goal.total == 10
    assert goal.snapshot()["remaining"] == 90


@pytest.mark.parametrize("total", [0, 1000, None])
def test_protocol_retransmissions_remain_deduplicated_after_restart(monkeypatch, tmp_path, total):
    ingest, events = collector(monkeypatch)
    ingest._handle_frame(like_payload(301, 5, total))
    path = tmp_path / "点赞记录.sqlite3"
    first = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    first.likes.add(events[0])
    first.write(*first.capture())
    restored = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    assert restored.restore()
    reconnected, repeated = collector(monkeypatch)
    reconnected._handle_frame(like_payload(301, 5, total))
    reconnected._handle_frame(like_payload(302, 3, total))
    restored.likes.add(repeated[0])
    assert restored.likes.total == 5
    restored.likes.add(repeated[1])
    assert restored.likes.total == 8


def test_missing_protocol_count_does_not_invent_a_like():
    assert douyin_cdp.DouyinCDPIngestor._like_values(fld_int(3, 50000)) == (0, 50000)


def test_old_daily_history_does_not_suppress_new_session_likes(monkeypatch, tmp_path):
    path = tmp_path / "旧点赞记录.sqlite3"
    first = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    first.likes.add(Event(kind="like", like_count=10, like_total=9000, like_source="douyin:123"))
    day, payload = first.capture()
    payload["likes"].pop("event_ids")
    first.write(day, payload)
    restored = LiveHistory(path, Metrics(), Hub(), LikeGoal())
    assert restored.restore()
    ingest, events = collector(monkeypatch)
    ingest._handle_frame(like_payload(401, 3, 0))
    ingest._handle_frame(like_payload(402, 2, 5))
    for event in events:
        restored.likes.add(event)
    assert restored.likes.total == 15


def test_protocol_likes_respect_waiting_and_live_pause(monkeypatch):
    async def scenario():
        brain = scheduler.Brain()
        await brain.fishing_command("start")
        brain.fishing.config["escape_chance"] = 0
        ingest, events = collector(monkeypatch)
        ingest._handle_frame(like_payload(501, 5, 1000))
        await brain._publish_event(events[-1])
        assert brain.likes.total == 5
        assert brain.fishing.progress == 0 and not brain.fishing.contributors
        brain.fishing.tick(brain.fishing.deadline)
        brain.fishing.deadline = time.monotonic() + 60
        brain.fishing.fish["pulls"] = 20
        await brain.set_paused(True)
        ingest._handle_frame(like_payload(502, 7, 1000))
        await brain._publish_event(events[-1])
        assert brain.likes.total == 12
        assert brain.fishing.progress == 0
        await brain.set_paused(False)
        ingest._handle_frame(like_payload(503, 3, 1000))
        await brain._publish_event(events[-1])
        assert brain.likes.total == 15
        assert brain.fishing.progress == pytest.approx(.3 / 20)
        assert brain.fishing.contributors["246801"]["likes"] == 3

    asyncio.run(scenario())
