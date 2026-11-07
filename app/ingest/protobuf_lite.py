"""通用 protobuf 解码器（无需 .proto 定义）。

为什么手写：
抖音 webcast 的 protobuf schema 未公开且经常变，如果用生成的 pb2 类，
一旦对方改字段整个接入就废了。手写的线格式解析器只依赖 protobuf 的稳定规则
（wire type 0/1/2/5），字段号变了也能继续跑，只是取值可能为空。

表现层只关心几件事：方法名（WebcastChatMessage 等）、用户昵称、正文。
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass

# 允许作为"方法名"的形态，用于在字节流里认出消息信封
_METHOD_RE = re.compile(rb"^Webcast[A-Za-z0-9_]{1,32}Message$")


@dataclass(slots=True)
class Field:
    num: int
    wire: int
    varint: int | None = None
    buf: bytes | None = None

    def text(self) -> str:
        if self.buf is None:
            return ""
        try:
            return self.buf.decode("utf-8")
        except UnicodeDecodeError:
            return ""


def pb_decode(buf: bytes) -> list[Field]:
    """把一段 protobuf 字节解析成字段列表；格式错误时返回已解析出的部分。"""
    fields: list[Field] = []
    i, n = 0, len(buf)

    def read_varint(pos: int) -> tuple[int, int]:
        val, shift = 0, 0
        while pos < n:
            b = buf[pos]
            pos += 1
            val |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                return val, pos
            if shift > 63:
                raise ValueError("varint too long")
        raise ValueError("truncated varint")

    try:
        while i < n:
            key, i = read_varint(i)
            num, wire = key >> 3, key & 0x7
            if num == 0:
                break
            if wire == 0:  # varint
                val, i = read_varint(i)
                fields.append(Field(num, wire, varint=val))
            elif wire == 1:  # 64-bit
                if i + 8 > n:
                    break
                fields.append(Field(num, wire, buf=buf[i : i + 8]))
                i += 8
            elif wire == 2:  # length-delimited
                ln, i = read_varint(i)
                if i + ln > n:
                    break
                fields.append(Field(num, wire, buf=buf[i : i + ln]))
                i += ln
            elif wire == 5:  # 32-bit
                if i + 4 > n:
                    break
                fields.append(Field(num, wire, buf=buf[i : i + 4]))
                i += 4
            else:
                break  # 3/4 组已废弃，遇到即停
    except (ValueError, IndexError):
        pass
    return fields


def gunzip_any(buf: bytes) -> bytes:
    """抖音的帧是 gzip 压缩的，但帧头前面可能还有 push 头，先找 gzip 魔数。"""
    idx = buf.find(b"\x1f\x8b")
    if idx < 0:
        return buf
    try:
        return gzip.decompress(buf[idx:])
    except Exception:
        return buf


def iter_envelopes(buf: bytes, depth: int = 0):
    """在解压后的字节里找出所有 (方法名, 消息体)。

    信封特征：某个子结构里 #1 是 "WebcastXxxMessage" 字符串、#2 是消息体。
    不能只用"含 Webcast 字样"来判断——消息体内部的 Common 结构也嵌着同名串，
    那样会把真正的消息体误跳过。
    """
    if depth > 4:
        return
    for f in pb_decode(buf):
        if f.wire != 2 or not f.buf or len(f.buf) < 24:
            continue
        sub = pb_decode(f.buf)
        method = None
        for s in sub:
            if s.num == 1 and s.wire == 2 and s.buf and _METHOD_RE.match(s.buf):
                method = s.buf.decode("ascii", "ignore")
                break
        if method:
            payload = next(
                (s.buf for s in sub if s.num == 2 and s.wire == 2 and s.buf and len(s.buf) >= 8),
                None,
            )
            if payload is not None:
                yield method, payload
                continue
        if len(f.buf) > 40:
            yield from iter_envelopes(f.buf, depth + 1)


def collect_users(buf: bytes, depth: int = 0) -> list[dict]:
    """递归找出所有"用户形状"的结构：昵称在 #3，头像 URL 在 #9/10/11/6 内嵌。

    注意不能在消息体顶层直接找 #3——ChatMessage 的 #3 是弹幕正文，会张冠李戴。
    """
    out: list[dict] = []
    if depth > 8:
        return out
    fields = pb_decode(buf)
    nick = next((f.text() for f in fields if f.num == 3 and f.wire == 2), "")
    if nick and 0 < len(nick) <= 40 and not any(ord(c) < 32 for c in nick) and not nick.startswith("http"):
        for num in (9, 10, 11, 6):
            f = next((x for x in fields if x.num == num and x.wire == 2), None)
            avatar = _find_avatar(f.buf) if f and f.buf else ""
            if avatar:
                identifier = next((field.varint for field in fields if field.num == 1 and field.varint), "")
                out.append({"name": nick, "avatar": avatar, "id": str(identifier), "is_admin": _user_admin(fields)})
                break
    for f in fields:
        if f.wire == 2 and f.buf and len(f.buf) >= 16:
            out.extend(collect_users(f.buf, depth + 1))
    return out


def chat_of(payload: bytes) -> tuple[str, dict | None]:
    """解析 WebcastChatMessage：返回 (正文, 用户信息)。

    已用真实帧验证的布局：
        #1 Common        #2 User（昵称在其 #3，头像在其 #9）
        #3 弹幕正文       #9 附加信息       #15 时间戳
    注意：未登录时抖音会把 User#3 昵称打码成"金***"，这是服务端隐私保护，
    不是解码错误；登录后即可拿到完整昵称。
    """
    fields = pb_decode(payload)
    text = next((f.text() for f in fields if f.num == 3 and f.wire == 2), "")
    user_field = next((f.buf for f in fields if f.num == 2 and f.wire == 2), None)
    user = extract_user(user_field) if user_field else None
    return text, user


_PLACEHOLDER_NICK = re.compile(r"^.{1,3}\*{2,}$")


def is_masked_nick(name: str) -> bool:
    """抖音对未登录观众返回的昵称形如 '金***' / '真***'。"""
    return bool(_PLACEHOLDER_NICK.match((name or "").strip()))


# 头像 URL 黑名单：这些是徽章/默认图，不是真人头像
_BAD_AVATAR = re.compile(
    r"(small_DefAvatar|userlabel|grade|badge|medal|icon|label|gift|effect|frame|banner|\.svg)",
    re.I,
)


def is_real_avatar(url: str) -> bool:
    s = url or ""
    if not s or "douyinpic" not in s and "byteimg" not in s:
        return False
    if _BAD_AVATAR.search(s):
        return False
    return bool(re.search(r"(aweme-avatar|tos-cn-avt|/aweme/100x100/|img/webcast/[a-f0-9]{16,})", s, re.I))


def _user_admin(fields):
    attributes = next((field.buf for field in fields if field.num == 32 and field.wire == 2), None)
    return bool(attributes and any(field.num in (2, 3) and field.wire == 0 and field.varint == 1
                                   for field in pb_decode(attributes)))


def extract_user(buf: bytes, depth: int = 0) -> dict | None:
    """从 User 结构里取昵称 + 头像（头像要过黑名单，避免拿到默认图）。"""
    fields = pb_decode(buf)
    nick = next((f.text() for f in fields if f.num == 3 and f.wire == 2), "").strip()
    if not nick or len(nick) > 40:
        return None
    avatar = ""
    for num in (9, 10, 11, 6):
        f = next((x for x in fields if x.num == num and x.wire == 2), None)
        if f and f.buf:
            got = _find_avatar(f.buf)
            if got and is_real_avatar(got):
                avatar = got
                break
    identifier = next((field.varint for field in fields if field.num == 1 and field.varint), "")
    return {"name": nick, "avatar": avatar, "id": str(identifier), "is_admin": _user_admin(fields)}


_AVATAR_HINT = re.compile(rb"https?://[^\x00-\x20\"']{10,300}")


def _find_avatar(buf: bytes, depth: int = 0) -> str:
    if depth > 5:
        return ""
    m = _AVATAR_HINT.search(buf)
    if m:
        url = m.group(0).decode("utf-8", "ignore")
        if re.search(r"douyinpic|byteimg|douyinstatic|aweme", url):
            return url
    for f in pb_decode(buf):
        if f.wire == 2 and f.buf:
            got = _find_avatar(f.buf, depth + 1)
            if got:
                return got
    return ""


# ---------------------------------------------------------------- 表情符号
_EMOJI = {
    "[微笑]": "😊", "[大笑]": "😄", "[嘿哈]": "😆", "[捂脸]": "🤦", "[机智]": "🤓",
    "[赞]": "👍", "[666]": "🔥", "[比心]": "🫶", "[玫瑰]": "🌹", "[爱心]": "❤️",
    "[流泪]": "😢", "[我想静静]": "😑", "[看]": "👀", "[鼓掌]": "👏", "[点亮]": "✨",
    "[比耶]": "✌️", "[送心]": "💝", "[思考]": "🤔", "[耶]": "✌️", "[灵机一动]": "💡",
}


# 只处理"开头被 @ 的打码昵称"，且星号后面必须跟空白/标点/结尾，
# 这样正文里的 ** 强调（如"这个算法**很重要"）不会被误删
_MASKED_INLINE = re.compile(r"^\s*@?\s*[^\s*@]{1,4}\*{2,}(?=[\s,，。、:：!！?？]|$)")


def normalize_text(text: str) -> str:
    """把抖音的 [表情] 占位符换成真 emoji，并压缩空白。

    还要清掉弹幕里引用的打码昵称（如"@金*** 说得对"）——那是回复别人时
    带上的前缀，念出来是"艾特金星星星星"，很怪。
    """
    s = text or ""
    for k, v in _EMOJI.items():
        s = s.replace(k, v)
    # 只有清洗后还剩内容才替换，避免把"666**"这种整条删空
    cleaned = _MASKED_INLINE.sub("", s)
    if re.sub(r"[\s,，。、:：!！?？@]", "", cleaned):
        s = cleaned
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"^[，,、:：\s@]+", "", s).strip()
    return s
