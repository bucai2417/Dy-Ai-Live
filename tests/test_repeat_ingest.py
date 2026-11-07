from __future__ import annotations

import asyncio

import pytest

from app.ingest import douyin_cdp
from app.ingest.bridge import normalize


def test_dom_node_identity_allows_repeated_text():
    async def scenario():
        ingest = douyin_cdp.DouyinCDPIngestor("123")
        events = []

        async def emit(event):
            events.append(event)

        ingest._emit = emit
        ingest._history_skipped = True
        page = {"href": "https://live.douyin.com/123", "lines": ["小鱼：播放晴天"] * 2,
                "entries": [{"id": "one", "text": "小鱼：播放晴天"},
                            {"id": "two", "text": "小鱼：播放晴天"}]}
        await ingest._handle_page(page)
        await ingest._handle_page(page)
        assert [event.text for event in events] == ["播放晴天", "播放晴天"]

    asyncio.run(scenario())


def test_same_source_repeats_are_not_cross_channel_duplicates():
    ingest = douyin_cdp.DouyinCDPIngestor("123")
    assert not ingest._channel_duplicate("ws", "小鱼", "你好")
    assert not ingest._channel_duplicate("ws", "小鱼", "你好")
    assert not ingest._channel_duplicate("ws", "蓝鲸", "你好")
    assert ingest._channel_duplicate("dom", "小鱼", "你好")
    assert ingest._channel_duplicate("dom", "小鱼", "你好")
    assert not ingest._channel_duplicate("dom", "小鱼", "你好")


def test_websocket_repeated_chat_is_delivered(monkeypatch):
    ingest = douyin_cdp.DouyinCDPIngestor("123")
    emitted = []
    monkeypatch.setattr(douyin_cdp, "chat_of", lambda payload: ("播放晴天", {"name": "小鱼"}))
    monkeypatch.setattr(ingest, "_emit_async", emitted.append)
    ingest._on_chat(b"first message")
    ingest._on_chat(b"second message")
    assert len(emitted) == 2 and emitted[0].id != emitted[1].id


@pytest.mark.parametrize("avatar", [
    {"avatar": "https://avatar.invalid/viewer.png"},
    {"AvatarUrl": "https://avatar.invalid/viewer.png"},
    {"HeadImgUrl": "https://avatar.invalid/viewer.png"},
    {"AvatarThumb": {"UrlList": ["https://avatar.invalid/viewer.png"]}},
])
def test_bridge_preserves_sender_avatar(avatar):
    event = normalize({"Type": 2, "Data": {"User": {"Id": "42", "Nickname": "中文观众", **avatar},
                                           "GiftName": "小心心"}})
    assert event.user_id == "42" and event.user_name == "中文观众"
    assert event.avatar == "https://avatar.invalid/viewer.png"
