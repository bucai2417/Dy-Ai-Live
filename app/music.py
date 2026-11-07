"""音乐：多音源解析 + 本地曲库 + 播放队列。

为什么是多音源逐候选探测：
  实测「同名歌的不同版本」可用性差别极大——「海阔天空」有一条 3.7MB 可播，
  另几条直接返回 text/html（无版权）。所以必须搜一批候选、逐个探活、
  取第一条真能播的，而不是拿搜索结果第一条直接用。

音源顺序：
  1. 本地曲库（music/ 目录）—— 永远最稳，没有版权和失效问题
  2. 网易云外链        —— 免登录，实测约 1/3 可播
  3. QQ 音乐现签直链    —— 免登录基本只有试听/VIP，作为最后兜底

注意：只解析播放地址，不下载、不落盘音频。
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx

from .music_policy import blocked_reason, query_reason

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav", ".flac", ".webm", ".opus"}
MIN_AUDIO_BYTES = 300 * 1024      # 小于这个基本是试听占位/错误页

QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
QQ_VKEY = "https://u.y.qq.com/cgi-bin/musicu.fcg"
NETEASE_SEARCH = "https://music.163.com/api/search/get/web"

_CACHE: dict[str, tuple[float, dict | None]] = {}
_CACHE_MS = 600.0


# ---------------------------------------------------------------- 工具
def norm(s: str) -> str:
    """归一化歌名，便于本地匹配和缓存键。"""
    s = str(s or "").lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[（(【\[].*?[）)】\]]", "", s)
    s = re.sub(r"[·・,，。!！?？'\"“”‘’\-—_/\\|:：;；]+", "", s)
    return s


def _cookie_dict(cookie: str) -> dict[str, str]:
    out = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


async def _probe(url: str, referer: str = "") -> int:
    """探测直链是不是真音频。返回字节数，0 表示不可用。"""
    headers = {"User-Agent": UA, "Range": "bytes=0-1023"}
    if referer:
        headers["Referer"] = referer
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
        ct = (r.headers.get("content-type") or "").lower()
        cr = r.headers.get("content-range") or ""
        total = 0
        if "/" in cr:
            try:
                total = int(cr.split("/")[-1])
            except ValueError:
                total = 0
        if not total:
            total = int(r.headers.get("content-length") or 0)
        if not any(k in ct for k in ("audio", "mpeg", "octet-stream")):
            return 0
        return total if total >= MIN_AUDIO_BYTES else 0
    except Exception:
        return 0


# ---------------------------------------------------------------- 本地曲库
def music_dir() -> Path:
    from .config import ROOT

    d = Path(os.environ.get("MUSIC_DIR") or (ROOT / "music"))
    d.mkdir(parents=True, exist_ok=True)
    return d


_lib = {"at": 0.0, "tracks": []}


def list_local(force: bool = False) -> list[dict]:
    """列出 music/ 下所有音频，文件名支持「歌手 - 歌名.mp3」。"""
    if not force and time.time() - _lib["at"] < 60:
        return _lib["tracks"]

    root = music_dir()
    tracks: list[dict] = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXT:
            continue
        stem = p.stem.strip()
        by, title = "", stem
        m = re.match(r"^(.{1,40}?)\s*[-–—]\s*(.{1,80})$", stem)
        if m:
            by, title = m.group(1).strip(), m.group(2).strip()
        rel = p.relative_to(root).as_posix()
        tracks.append({
            "id": base64.urlsafe_b64encode(rel.encode()).decode().rstrip("="),
            "file": rel,
            "title": title,
            "by": by,
            "size": p.stat().st_size,
            "kind": "local",
        })
    tracks.sort(key=lambda t: t["file"].lower())
    _lib.update(at=time.time(), tracks=tracks)
    return tracks


def find_local(query: str) -> dict | None:
    q = norm(query)
    if not q:
        return None
    best, best_score = None, 0
    for t in list_local():
        if blocked_reason(t):
            continue
        title, full = norm(t["title"]), norm(t["by"] + t["title"])
        score = 0
        if title == q or full == q:
            score = 100
        elif q in title or title in q:
            score = 80 - abs(len(title) - len(q))
        elif q in full or full in q:
            score = 60 - abs(len(full) - len(q))
        if score > best_score:
            best, best_score = t, score
    return best if best_score > 0 else None


def local_path(track_id: str) -> Path | None:
    """把曲目 id 还原成磁盘路径，并挡住 ../ 越权。"""
    root = music_dir().resolve()
    for t in list_local():
        if t["id"] == track_id:
            if blocked_reason(t):
                return None
            p = (root / t["file"]).resolve()
            if str(p).startswith(str(root)) and p.is_file():
                return p
    return None


# ---------------------------------------------------------------- 在线音源
async def _netease_candidates(query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(NETEASE_SEARCH,
                        params={"s": query, "type": 1, "limit": 6, "offset": 0},
                        headers={"User-Agent": UA, "Referer": "https://music.163.com/"})
    if r.status_code != 200:
        return []
    songs = ((r.json() or {}).get("result") or {}).get("songs") or []
    out = []
    for s in songs:
        sid = s.get("id")
        if not sid:
            continue
        out.append({
            "provider": "netease",
            "id": str(sid),
            "title": (s.get("name") or "").strip(),
            "by": "/".join(a.get("name", "") for a in (s.get("artists") or [])),
            "album": (s.get("album") or {}).get("name", ""),
            "aliases": s.get("alias") or [],
            "durationSec": round((s.get("duration") or 0) / 1000) or 0,
            "url": f"https://music.163.com/song/media/outer/url?id={sid}.mp3",
            "referer": "https://music.163.com/",
        })
    return out


async def _qq_candidates(query: str, cookie: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(QQ_SEARCH,
                        params={"p": 1, "n": 6, "w": query, "format": "json"},
                        headers={"User-Agent": "okhttp/5.5.0", "Referer": "https://y.qq.com/"},
                        cookies=_cookie_dict(cookie))
    if r.status_code != 200:
        return []
    d = r.json()
    if d.get("code") != 0:
        return []
    lst = ((d.get("data") or {}).get("song") or {}).get("list") or []
    return [{
        "provider": "qq",
        "id": s["songmid"],
        "title": (s.get("songname") or "").strip(),
        "by": "/".join(x.get("name", "") for x in (s.get("singer") or [])),
        "album": s.get("albumname") or "",
        "durationSec": int(s.get("interval") or 0),
    } for s in lst if s.get("songmid")]


async def _qq_url(songmid: str, cookie: str) -> str:
    """QQ 音乐现签直链（必须 GET，POST 会被 500001 拦）。"""
    cookies = _cookie_dict(cookie)
    uin = "".join(ch for ch in (cookies.get("uin") or cookies.get("qqmusic_uin")
                                or cookies.get("p_uin") or "0") if ch.isdigit()) or "0"
    payload = {
        "req_0": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey",
                  "param": {"guid": str(random.randint(1, 10 ** 10)),
                            "songmid": [songmid], "songtype": [0],
                            "uin": uin, "platform": "20"}},
        "comm": {"uin": int(uin) if uin.isdigit() else 0,
                 "format": "json", "ct": 24, "cv": 0},
    }
    async with httpx.AsyncClient(timeout=12) as c:
        r = await c.get(QQ_VKEY,
                        params={"format": "json",
                                "data": json.dumps(payload, separators=(",", ":"))},
                        headers={"User-Agent": "okhttp/5.5.0", "Referer": "https://y.qq.com/"},
                        cookies=cookies)
    data = ((r.json() or {}).get("req_0") or {}).get("data") or {}
    mi = (data.get("midurlinfo") or [{}])[0]
    purl = mi.get("purl") or ""
    if not purl:
        return ""
    sip = (data.get("sip") or ["https://ws.stream.qqmusic.qq.com/"])[0]
    url = sip + purl
    if uin != "0":
        url += ("&" if "?" in url else "?") + f"uin={uin}"
    return url


# ---------------------------------------------------------------- 解析入口
async def resolve(query: str, *, cookie: str = "",
                  providers: list[str] | None = None,
                  allow_local: bool = True) -> dict | None:
    """把「歌名 / 歌名 歌手」解析成可播放的曲目。没有可用音源返回 None。"""
    q = (query or "").strip()
    if not q:
        return None
    if query_reason(q):
        return None

    key = norm(q)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_MS:
        return hit[1] if hit[1] and not blocked_reason(hit[1]) else None

    order = providers or ["local", "netease", "qq"]
    result: dict | None = None

    # 1) 本地优先：命中就直接用，不必联网
    if allow_local and "local" in order:
        loc = find_local(q)
        if loc:
            result = {**loc, "provider": "local",
                      "url": f"/api/music/stream/{loc['id']}"}

    # 2) 在线逐候选探活
    if result is None:
        for prov in order:
            if prov == "netease":
                try:
                    for cand in await _netease_candidates(q):
                        if blocked_reason(cand):
                            continue
                        size = await _probe(cand["url"], cand["referer"])
                        if size:
                            result = {**cand, "kind": "remote", "size": size}
                            break
                except Exception:
                    pass
            elif prov == "qq":
                try:
                    for cand in await _qq_candidates(q, cookie):
                        if blocked_reason(cand):
                            continue
                        url = await _qq_url(cand["id"], cookie)
                        if not url:
                            continue
                        size = await _probe(url, "https://y.qq.com/")
                        if size:
                            result = {**cand, "url": url, "kind": "remote", "size": size}
                            break
                except Exception:
                    pass
            if result:
                break

    _CACHE[key] = (time.time(), result)
    if len(_CACHE) > 200:
        for k in [k for k, v in _CACHE.items() if time.time() - v[0] > _CACHE_MS]:
            _CACHE.pop(k, None)
    return result


async def search(query: str, *, cookie: str = "", limit: int = 8) -> list[dict]:
    """搜索（不探活，快），给点歌列表用。"""
    q = (query or "").strip()
    if not q:
        return []
    reason = query_reason(q)
    if reason:
        raise ValueError(reason)
    out: list[dict] = []

    loc = find_local(q)
    if loc:
        out.append({**loc, "provider": "local",
                    "url": f"/api/music/stream/{loc['id']}"})
    for t in list_local():
        if len(out) >= 3:
            break
        if norm(q) in norm(t["by"] + t["title"]) and t not in out:
            out.append({**t, "provider": "local",
                        "url": f"/api/music/stream/{t['id']}"})

    try:
        out.extend(await _netease_candidates(q))
    except Exception:
        pass
    try:
        out.extend(await _qq_candidates(q, cookie))
    except Exception:
        pass
    return [row for row in out if not blocked_reason(row)][:limit]


def clear_cache() -> None:
    _CACHE.clear()
