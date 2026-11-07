"""真实协议帧回归测试：用抓到的抖音帧结构验证解析器，防止以后改坏。

运行：python -m pytest tests/test_douyin_parse.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.ingest.douyin_cdp import DouyinCDPIngestor, is_junk_name, parse_line, parse_room
from app.ingest.protobuf_lite import chat_of, is_masked_nick, is_real_avatar


# ---------------------------------------------------------------- protobuf 构造
def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def fld_str(num: int, s: str) -> bytes:
    b = s.encode("utf-8")
    return varint(num << 3 | 2) + varint(len(b)) + b


def fld_msg(num: int, b: bytes) -> bytes:
    if isinstance(b, str):
        b = b.encode("utf-8")
    return varint(num << 3 | 2) + varint(len(b)) + b


def fld_int(num: int, n: int) -> bytes:
    return varint(num << 3 | 0) + varint(n)


AVATAR = "https://p3-webcast.douyinpic.com/aweme/100x100/abc123.jpeg"


def user_struct(nick: str, avatar: str = AVATAR) -> bytes:
    # #3 昵称，#9 头像容器（内部再套一层 #2 放 URL）
    return fld_str(3, nick) + fld_msg(9, fld_msg(2, avatar))


def chat_message(nick: str, text: str) -> bytes:
    # 真实布局：#1 Common  #2 User  #3 正文  #15 时间戳
    return fld_msg(1, fld_str(1, "x")) + fld_msg(2, user_struct(nick)) + fld_str(3, text) + fld_int(15, 1793634703)


# ---------------------------------------------------------------- 房间号
def test_parse_room():
    assert parse_room("123456789") == "123456789"
    assert parse_room("https://live.douyin.com/541482891580") == "541482891580"
    assert parse_room("https://live.douyin.com/541482891580?x=1") == "541482891580"


# ---------------------------------------------------------------- 弹幕正文
def test_chat_of_reads_text_from_field3():
    from app.ingest.protobuf_lite import chat_of

    text, user = chat_of(chat_message("小明", "主播你好呀"))
    assert text == "主播你好呀", "正文必须取自 #3"
    assert user["name"] == "小明"
    assert user["avatar"] == AVATAR


def test_chat_of_does_not_confuse_text_with_nick():
    """回归：曾把 #3 正文误当成昵称。#2 的 #3 才是昵称。"""
    from app.ingest.protobuf_lite import chat_of

    text, user = chat_of(chat_message("阿强", "这是弹幕内容"))
    assert user["name"] == "阿强"
    assert text == "这是弹幕内容"


# ---------------------------------------------------------------- 打码昵称
def test_masked_nick_detected():
    assert is_masked_nick("金***")
    assert is_masked_nick("真***")
    assert not is_masked_nick("小明")
    assert not is_masked_nick("")


def test_masked_nick_becomes_readable():
    # 未登录时抖音返回打码昵称，不能让画面出现"金***"
    got = DouyinCDPIngestor._display_name("金***")
    assert got.startswith("观众") and "***" not in got
    assert DouyinCDPIngestor._display_name("小明") == "小明"


# ---------------------------------------------------------------- 头像过滤
def test_avatar_blacklist():
    assert is_real_avatar(AVATAR)
    # 徽章/默认图不能当头像
    assert not is_real_avatar("https://p11-webcast.douyinpic.com/img/webcast/small_DefAvatar.png")
    assert not is_real_avatar("https://p11-webcast.douyinpic.com/img/webcast/userlabel_regular_watch.png")
    assert not is_real_avatar("")


# ---------------------------------------------------------------- 行解析
def test_parse_line_chat():
    assert parse_line("小明：主播你好呀") == {"kind": "chat", "name": "小明", "text": "主播你好呀"}
    assert parse_line("小红: 几点开始")['text'] == "几点开始"


def test_parse_line_gift():
    ev = parse_line("土豪哥：送出 嘉年华 x2")
    assert ev["kind"] == "gift" and ev["gift"] == "嘉年华" and ev["count"] == 2
    ev2 = parse_line("小鲤鱼 送出 小心心 x5")
    assert ev2["kind"] == "gift" and ev2["gift"] == "小心心" and ev2["count"] == 5


def test_parse_line_enter_follow():
    assert parse_line("路人甲 来了") == {"kind": "enter", "name": "路人甲"}
    assert parse_line("新粉丝：关注了主播")["kind"] == "follow"


def test_parse_line_rejects_junk():
    for junk in ("充钻石", "本场点赞 1.5万", "小时榜", "分享[视频]"):
        ev = parse_line(junk)
        assert ev is None or ev["kind"] == "system"
    assert is_junk_name("送出了")
    assert is_junk_name("")


# ---------------------------------------------------------------- 帧 -> 事件
def make_envelope(method: str, payload: bytes) -> bytes:
    """复刻抖音帧的真实嵌套：

        PushFrame(#8 payload)
          └ Response(#1 第一条 Message)
               └ Message{ #1 = "WebcastXxxMessage", #2 = 消息体 }
    """
    message = fld_str(1, method) + fld_msg(2, payload) + fld_int(3, 7)
    response = fld_msg(1, message)
    return fld_msg(8, response)


def test_iter_envelopes_finds_method():
    from app.ingest.protobuf_lite import iter_envelopes

    frame = make_envelope("WebcastChatMessage", chat_message("小明", "在吗"))
    found = [(m, p) for m, p in iter_envelopes(frame)]
    assert found, "应能识别出 WebcastXxxMessage 信封"
    method, got = found[0]
    assert method == "WebcastChatMessage"
    # 拿到的必须是消息体本身，而不是外壳
    text, user = chat_of(got)
    assert text == "在吗" and user["name"] == "小明"

# ---------------------------------------------------------------- DOM 噪音剥离
# 真实直播间里 innerText 会把进场提示、登录提示拼到弹幕尾巴上，
# 导致 AI 对着半条系统提示作答。这些用例来自实际日志。
@pytest.mark.parametrize("raw,want_kind,want", [
    ("剪光头了？ 健身boy 来了 需先登录，", "chat", "剪光头了？"),
    ("剪光头了？ 睡觉的时候不困 来了", "chat", "剪光头了？"),
    ("打暗光", "chat", "打暗光"),
    ("我回来了", "chat", "我回来了"),
    ("他来了吗", "chat", "他来了吗"),
    ("张三 来了", "enter", "张三"),
    ("小明：这个好看 需先登录", "chat", "这个好看"),
    ("6", "chat", "6"),
    ("666", "chat", "666"),
])
def test_parse_line_strips_dom_noise(raw, want_kind, want):
    from app.ingest.douyin_cdp import parse_line
    got = parse_line(raw)
    assert got is not None, f"{raw!r} 不应被丢弃"
    assert got["kind"] == want_kind
    assert (got.get("text") or got.get("name")) == want
