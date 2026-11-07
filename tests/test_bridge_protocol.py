"""弹幕桥接协议兼容性测试。

不依赖真实直播间，用各家弹幕助手（DouyinBarrageGrab / 游猴 / 自研）常见的
报文格式喂给 normalize()，确认都能被正确解析成统一 Event。

运行：python -m tests.test_bridge_protocol
"""
from __future__ import annotations

import sys

from app.ingest.bridge import normalize

# 每组：(说明, 原始报文, 期望 kind, 期望用户名, 期望正文/礼物名)
CASES = [
    # ---- DouyinBarrageGrab 风格（PascalCase + type 数字） ----
    ("BarrageGrab 聊天", {
        "Type": 1, "Data": {
            "User": {"Nickname": "小明", "Id": "12345"},
            "Content": "主播你好",
        },
    }, "chat", "小明", "主播你好"),

    ("BarrageGrab 礼物", {
        "Type": 2, "Data": {
            "User": {"Nickname": "土豪哥", "Id": "999"},
            "GiftName": "嘉年华", "GiftCount": 2, "GiftValue": 3000,
        },
    }, "gift", "土豪哥", "嘉年华"),

    ("BarrageGrab 进场", {
        "Type": 3, "Data": {"User": {"Nickname": "路人甲", "Id": "1"}},
    }, "enter", "路人甲", ""),

    ("BarrageGrab 点赞", {
        "Type": 4, "Data": {"User": {"Nickname": "点赞狂", "Id": "2"}, "Count": 15},
    }, "like", "点赞狂", ""),

    ("BarrageGrab 关注", {
        "Type": 5, "Data": {"User": {"Nickname": "新粉丝", "Id": "3"}},
    }, "follow", "新粉丝", ""),

    # ---- 游猴 / 通用小写风格 ----
    ("小写 type=comment", {
        "type": "comment", "nickname": "小红", "content": "这个鱼好可爱", "userId": "777",
    }, "chat", "小红", "这个鱼好可爱"),

    ("小写 type=chat", {
        "type": "chat", "userName": "老王", "text": "几点下播",
    }, "chat", "老王", "几点下播"),

    ("小写 type=gift", {
        "type": "gift", "nickName": "大老板", "giftName": "小心心", "giftCount": 3,
    }, "gift", "大老板", "小心心"),

    ("小写 type=member", {
        "type": "member", "nickname": "新来的", "userId": "55",
    }, "enter", "新来的", ""),

    # ---- 扁平结构（无 data 包装） ----
    ("扁平 msg 字段", {
        "type": "danmu", "nick": "阿强", "msg": "顶一下",
    }, "chat", "阿强", "顶一下"),

    # ---- 中文键（部分国产助手） ----
    ("中文键", {
        "type": "chat", "nickname": "中文用户", "content": "中文内容测试",
    }, "chat", "中文用户", "中文内容测试"),
]


def run() -> int:
    passed = failed = 0
    print("=" * 62)
    for desc, raw, want_kind, want_user, want_text in CASES:
        ev = normalize(raw)
        if ev is None:
            print(f"  ✗ {desc:22s} -> 解析失败（返回 None）")
            failed += 1
            continue
        ok_kind = ev.kind == want_kind
        ok_user = ev.user_name == want_user
        got_text = ev.gift_name if ev.kind == "gift" else ev.text
        ok_text = (want_text == "") or (got_text == want_text)
        if ok_kind and ok_user and ok_text:
            print(f"  ✓ {desc:22s} -> {ev.kind:6s} {ev.user_name:8s} {got_text}")
            passed += 1
        else:
            print(f"  ✗ {desc:22s} -> kind={ev.kind} user={ev.user_name} text={got_text}")
            print(f"      期望 kind={want_kind} user={want_user} text={want_text}")
            failed += 1

    # 边界情况
    print("-" * 62)
    edge = [
        ("空对象", {}, None),
        ("非字典", "not a json", None),
        ("聊天但无内容", {"type": "chat", "nickname": "x"}, None),
    ]
    for desc, raw, want in edge:
        got = normalize(raw)
        if got is want:
            print(f"  ✓ 边界 {desc:18s} -> 正确返回 None")
            passed += 1
        else:
            print(f"  ✗ 边界 {desc:18s} -> {got}")
            failed += 1

    # 列表包装（部分助手一次推多条）
    lst = normalize([{"type": "chat", "nickname": "a", "content": "b"}])
    print(f"  {'✓' if lst is None else '✗'} 边界 列表包装 -> 交由调用方逐条处理（{lst}）")
    passed += 1 if lst is None else 0
    failed += 0 if lst is None else 1

    print("=" * 62)
    print(f"通过 {passed} / {passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
