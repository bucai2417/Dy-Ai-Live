"""桥接接入层：接收外部弹幕助手（如 DouyinBarrageGrab）经 WebSocket/HTTP 推来的消息。

为什么要有这个：
- 抖音网页版 wss 逆向接口经常失效，且需要 Cookie/签名环境；
- 现成的抓取工具（C# 客户端）稳定性更好，只要把消息转发过来即可。

协议：监听 ws://127.0.0.1:5001，接受 JSON 数组或单对象。
兼容字段：type/msgType/kind、nickname/nickName/userName、content/msg/text 等常见命名。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..config import settings
from ..events import Event

log = logging.getLogger(__name__)

_KIND_MAP = {
    "chat": "chat", "comment": "chat", "danmu": "chat", "msg": "chat", "1": "chat",
    "gift": "gift", "2": "gift",
    "member": "enter", "enter": "enter", "3": "enter",
    "like": "like", "4": "like",
    "follow": "follow", "social": "follow", "5": "follow",
    "room_stats": "system", "stats": "system",
}


def _pick(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return ""


# DouyinBarrageGrab 的数字 Type 编码
_NUMERIC_KIND = {
    "1": "chat", "2": "gift", "3": "enter", "4": "like", "5": "follow",
    "6": "follow", "7": "system",
}


def _unwrap(raw: dict) -> dict:
    """剥掉外层信封，返回真正装着业务字段的那一层。

    兼容两种主流封装：
      A) {"Type": 1, "Data": {"User": {...}, "Content": "..."}}   人形/PascalCase
      B) {"type": "chat", "data": {"nickname": "小码", "content": "..."}}  小写
    扁平结构（字段直接在顶层）原样返回。
    """
    for key in ("Data", "data", "payload", "message"):
        inner = raw.get(key)
        if isinstance(inner, dict):
            return inner
    return raw


def _avatar_url(value: Any) -> str:
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else ""
    if isinstance(value, list):
        return next((url for entry in value if (url := _avatar_url(entry))), "")
    if isinstance(value, dict):
        return _avatar_url(_pick(value, "url_list", "UrlList", "urlList", "urls", "url", "Url"))
    return ""


def normalize(raw: Any) -> Event | None:
    """把外部助手的消息归一化为 Event；无法识别返回 None。"""
    if isinstance(raw, list):
        # 部分助手一次推多条，由调用方逐条处理后传入
        return None
    if not isinstance(raw, dict):
        return None

    # kind 可能在信封层（PascalCase 的 Type 在外层），也可能在内层
    kind_raw = str(
        _pick(raw, "type", "Type", "msgType", "MsgType", "kind")
        or ""
    ).lower()

    data = _unwrap(raw)
    if not kind_raw:
        kind_raw = str(_pick(data, "type", "Type", "msgType", "MsgType", "kind") or "").lower()

    # 数字编码优先（BarrageGrab 用 1/2/3/4/5）
    if kind_raw.isdigit():
        kind = _NUMERIC_KIND.get(kind_raw, "chat")
    else:
        kind = _KIND_MAP.get(kind_raw, "chat")

    # 用户信息：可能在 data.User / data.user 里嵌套
    user_node = None
    for key in ("User", "user", "userInfo", "UserInfo", "author"):
        node = data.get(key)
        if isinstance(node, dict):
            user_node = node
            break
    where_user = user_node if user_node is not None else data

    user = _pick(
        where_user, "nickname", "Nickname", "NickName", "nickName", "userName",
        "UserName", "user_name", "username", "nick", "Nick",
    )
    uid = str(
        _pick(where_user, "userId", "UserId", "user_id", "uid", "id", "Id", "openid", "web_rid") or ""
    )
    name = str(user or "观众")
    avatar_keys = ("avatar", "Avatar", "avatarUrl", "AvatarUrl", "avatar_url", "headUrl", "HeadUrl",
                   "HeadImgUrl", "headimgurl", "ProfilePicture", "avatarThumb", "AvatarThumb", "avatar_thumb")
    avatar = _avatar_url(_pick(where_user, *avatar_keys)) or _avatar_url(_pick(data, *avatar_keys))

    if kind == "chat":
        text = str(
            _pick(data, "content", "Content", "msg", "Msg", "text", "Text", "danmu") or ""
        ).strip()
        if not text:
            return None
        attributes = _pick(where_user, "user_attr", "userAttr", "UserAttr")
        nodes = [where_user, data] + ([attributes] if isinstance(attributes, dict) else [])
        flags = [node.get(key) for node in nodes for key in
                 ("is_admin", "isAdmin", "IsAdmin", "is_super_admin", "isSuperAdmin", "IsSuperAdmin")]
        admin = any(value is True or type(value) is int and value == 1 or isinstance(value, str) and value.lower() in ("true", "1") for value in flags)
        return Event(kind="chat", user_id=uid, user_name=name, text=text, avatar=avatar, is_admin=admin)

    if kind == "gift":
        gname = str(
            _pick(data, "giftName", "GiftName", "gift_name", "name", "Name") or "礼物"
        )
        cnt = _pick(
            data, "giftCount", "GiftCount", "gift_count", "repeatCount", "RepeatCount",
            "count", "Count", "num",
        ) or 1
        val = _pick(data, "giftValue", "GiftValue", "diamond", "Diamond", "price", "Price")
        estimated = val in (None, "", 0)
        try:
            cnt, val = int(cnt), int(val or 1)
            estimated |= val <= 0
        except Exception:
            cnt, val = 1, 1
            estimated = True
        return Event(kind="gift", user_id=uid, user_name=name, gift_name=gname,
                     gift_count=cnt, gift_value=val * cnt, gift_value_estimated=estimated, text=f"送出了{gname}x{cnt}", avatar=avatar)

    if kind == "enter":
        return Event(kind="enter", user_id=uid, user_name=name, avatar=avatar)

    if kind == "like":
        cnt = _pick(data, "count", "likeCount", "Count")
        total = _pick(data, "total", "Total", "totalLikeCount", "TotalLikeCount", "likeTotal")
        if cnt in (None, ""):
            cnt = 0 if total not in (None, "") else 1
        try:
            cnt = int(cnt)
        except Exception:
            cnt = 1
        try:
            total = max(0, int(total)) if total is not None else None
        except (ValueError, TypeError):
            total = None
        count = max(0, cnt)
        return Event(kind="like", user_id=uid, user_name=name, gift_count=count, avatar=avatar,
                     like_count=count, like_total=total, like_source="bridge")

    if kind == "follow":
        return Event(kind="follow", user_id=uid, user_name=name, avatar=avatar)

    return None


class BridgeIngestor:
    name = "bridge"

    def __init__(self, port: int = 0) -> None:
        # 端口以后台配置为准，端口为 0 时回落到 .env
        self.port = int(port or settings.bridge_port)
        self._emit = None

    async def run(self, emit) -> None:
        self._emit = emit
        try:
            import websockets
        except ImportError:
            log.error("缺少 websockets 库，bridge 通道无法启动")
            return

        while True:
            try:
                log.info("弹幕桥接监听 ws://%s:%s", settings.bridge_host, self.port)
                async with websockets.serve(
                    self._handler, settings.bridge_host, self.port, max_size=8 * 1024 * 1024
                ):
                    await asyncio.Future()  # 永久运行
            except OSError as exc:
                log.error("桥接端口 %s 被占用或不可用：%s", self.port, exc)
                await asyncio.sleep(5)
            except Exception as exc:
                log.error("桥接异常：%s", exc)
                await asyncio.sleep(3)

    async def _handler(self, ws, path=None) -> None:
        peer = getattr(ws, "remote_address", ("?", 0))
        log.info("弹幕助手已连接：%s", peer)
        try:
            async for message in ws:
                try:
                    payload = json.loads(message)
                except Exception:
                    log.debug("非 JSON 消息，忽略")
                    continue
                items = payload if isinstance(payload, list) else [payload]
                for item in items:
                    ev = normalize(item)
                    if ev and self._emit:
                        await self._emit(ev)
        except Exception as exc:
            log.info("弹幕助手断开：%s", exc)
