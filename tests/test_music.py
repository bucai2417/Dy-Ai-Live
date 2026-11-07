"""音乐播放器：队列优先级、指令解析、配置开关。"""
from __future__ import annotations

import pytest

from app import settings_store
from app.brain import songcmd
from app.playlist import Queue, make_track
from app.settings_store import Effective, store


def _t(title: str, user: str = "", gift: int = 0):
    return make_track({"title": title, "by": "", "url": "x"}, user=user, gift_value=gift)


# ---------------------------------------------------------------- 指令解析
@pytest.mark.parametrize("text,action,query", [
    ("点歌 晴天", "add", "晴天"),
    ("点一首 周杰伦 晴天", "add", "周杰伦 晴天"),
    ("来一首孤勇者", "add", "孤勇者"),
    ("我想听 起风了", "add", "起风了"),
    ("能不能点歌 稻香", "add", "稻香"),
    ("切歌", "next", ""),
    ("下一首", "next", ""),
    ("跳过", "next", ""),
    ("停止音乐", "stop", ""),
    ("继续播放", "resume", ""),
    ("在放什么", "what", ""),
])
def test_parse_song_command(text, action, query):
    r = songcmd.parse_request(text)
    assert r is not None, f"{text} 应该被识别"
    assert r.action == action
    if query:
        assert r.query == query


@pytest.mark.parametrize("text", [
    "主播好漂亮", "今天天气不错", "这首歌真好听",
    "点歌", "点歌   ", "哈哈哈哈", "",
])
def test_parse_ignores_chitchat(text):
    """闲聊和空的「点歌」不能误触发，否则会把正常对话吞掉。"""
    assert songcmd.parse_request(text) is None


# ---------------------------------------------------------------- 队列
def test_gift_song_jumps_ahead():
    q = Queue()
    q.add(_t("普通1", "A"))
    q.add(_t("普通2", "B"))
    q.add(_t("土豪", "C", gift=5000))
    assert [t.title for t in q.pending()] == ["土豪", "普通1", "普通2"]


def test_bigger_gift_outranks_smaller():
    q = Queue()
    q.add(_t("小礼", "A", gift=100))
    q.add(_t("大礼", "B", gift=9999))
    assert [t.title for t in q.pending()] == ["大礼", "小礼"]


def test_per_user_limit():
    q = Queue()
    for i in range(3):
        assert q.add(_t(f"歌{i}", "刷屏王"))[0] is True
    ok, why = q.add(_t("第四首", "刷屏王"))
    assert ok is False
    assert "3 首" in why


def test_queue_capacity():
    q = Queue()
    for i in range(30):
        q.add(_t(f"歌{i}", f"用户{i}"))
    ok, why = q.add(_t("溢出", "新人"))
    assert ok is False
    assert "满" in why


def test_next_track_moves_to_history():
    """history 只记「已经唱完的」，不是「即将播的」。"""
    q = Queue()
    q.add(_t("第一", "A"))
    q.add(_t("第二", "B"))

    q.next_track()                     # 开始播第一首
    assert list(q.history) == []       # 还没唱完，不该进历史

    q.next_track()                     # 切到第二首，第一首唱完
    assert q.current.title == "第二"
    assert [t.title for t in q.pending()] == []
    assert list(q.history)[-1]["title"] == "第一"
    assert q.playing is True


def test_played_same_track_twice_not_duplicated():
    """前端 ended 回调可能重发，同一首不能重复排队切歌。"""
    q = Queue()
    q.add(_t("唯一一首", "A"))
    q.next_track()
    assert q.current.title == "唯一一首"
    q.next_track()
    assert q.current is None
    assert q.playing is False


def test_stop_clears_current_but_keeps_queue():
    q = Queue()
    q.add(_t("歌", "A"))
    q.next_track()
    q.stop()
    assert q.current is None
    assert q.playing is False


def test_remove_by_id():
    q = Queue()
    tr = _t("要删的", "A")
    q.add(tr)
    assert q.remove(tr.id) is True
    assert q.pending() == []
    assert q.remove("不存在") is False


# ---------------------------------------------------------------- 配置
@pytest.fixture()
def iso(monkeypatch):
    """隔离运行时配置。

    直接替换 store._data（并屏蔽 save），而不是 monkeypatch update——
    真实的 update 会深合并后重新赋值 _data，夹具拦不住它，测出来还是旧值。
    """
    import copy

    baseline = copy.deepcopy(settings_store.DEFAULTS)
    monkeypatch.setattr(store, "_data", copy.deepcopy(baseline), raising=False)
    monkeypatch.setattr(store, "save", lambda *a, **k: None, raising=False)
    yield


def test_music_enabled_by_default(iso):
    assert Effective.music()["enabled"] is True


def test_music_cookie_default_empty(iso):
    assert Effective.music()["cookie"] == ""


def test_music_providers_parsed(iso):
    store.update({"music": {"providers": "local,netease"}})
    assert Effective.music()["providers"] == ["local", "netease"]


def test_music_invalid_providers_fallback(iso):
    """乱填的音源名要过滤掉，不能全空。"""
    store.update({"music": {"providers": "xxx,yyy"}})
    assert Effective.music()["providers"] == ["local", "netease", "qq"]


def test_music_volume_clamped(iso):
    store.update({"music": {"volume": 5}})
    assert Effective.music()["volume"] == 1.0
    store.update({"music": {"volume": -3}})
    assert Effective.music()["volume"] == 0.0
