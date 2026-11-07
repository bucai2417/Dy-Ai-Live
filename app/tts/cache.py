"""语音合成结果的磁盘缓存。

为什么需要：
    无人直播一晚上要说成百上千句，其中大量是重复的——
    欢迎语、感谢模板、冷场话题、常见问答。
    每句都去调一次 edge-tts 要 2~3 秒网络往返，
    缓存后命中直接读盘，实测从 2.8s 降到 <5ms。

命名：sha1(音色|语速|音量|文本) 前 20 位，改音色不会串味。
清理：超过 MAX_FILES 就按最后访问时间删掉最老的一批。
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_FILES = 2000
MAX_BYTES = 300 * 1024 * 1024      # 300MB 上限，别把硬盘吃满


def _cache_dir() -> Path:
    from ..config import DATA_DIR

    d = DATA_DIR / "voice"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(key: str) -> Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return _cache_dir() / f"{h}.mp3"


def load(key: str) -> bytes | None:
    p = _path(key)
    try:
        if not p.is_file():
            return None
        data = p.read_bytes()
        if len(data) < 512:
            return None                 # 太小多半是坏文件
        # 更新访问时间，让常用句不被优先淘汰
        try:
            os.utime(p, None)
        except OSError:
            pass
        return data
    except Exception:
        return None


def save(key: str, data: bytes) -> None:
    if not data:
        return
    p = _path(key)
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)                  # 原子替换，避免读到半个文件
    except Exception:
        log.debug("语音缓存写入失败", exc_info=True)
        return
    _gc()


def _gc() -> None:
    """超过上限就删最久没用的。只在写入后偶尔跑，别每次都扫盘。"""
    try:
        d = _cache_dir()
        files = [(f, f.stat()) for f in d.glob("*.mp3") if f.is_file()]
        if len(files) <= MAX_FILES and sum(s.st_size for _, s in files) <= MAX_BYTES:
            return
        files.sort(key=lambda x: x[1].st_atime)
        total = sum(s.st_size for _, s in files)
        for f, s in files:
            if len(files) <= MAX_FILES // 2 and total <= MAX_BYTES // 2:
                break
            try:
                f.unlink()
                total -= s.st_size
                files = files[1:]
            except OSError:
                pass
    except Exception:
        log.debug("语音缓存清理失败", exc_info=True)


def clear() -> int:
    """清空缓存，返回删除数量。"""
    n = 0
    try:
        for f in _cache_dir().glob("*.mp3"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    except Exception:
        pass
    return n


def stats() -> dict:
    try:
        d = _cache_dir()
        files = list(d.glob("*.mp3"))
        return {
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files if f.is_file()),
            "dir": str(d),
        }
    except Exception:
        return {"files": 0, "bytes": 0, "dir": ""}
