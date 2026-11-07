from __future__ import annotations


def clock(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "--:--" if seconds is None else "00:00"
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes:02d}:{remaining:02d}"


def music_view(state: dict, now: float) -> dict:
    music = state.get("music") or {}
    current = music.get("current") or {}
    queue = music.get("queue") or []
    upcoming = queue[0] if queue else {}
    config = (state.get("config") or {}).get("music") or {}
    playback = state.get("playback") or {}
    actual = bool(current.get("id") and playback.get("id") == current.get("id"))
    enabled = config.get("enabled", True)
    connected = state.get("connected", False)
    requested = bool(current and music.get("playing") and enabled and connected and not state.get("paused"))
    playing = requested and actual and playback.get("status") == "playing"
    position = max(0, float(playback.get("position", 0) if actual else music.get("elapsed") or 0))
    if playing:
        position += min(0.25, max(0, now - playback.get("at", now)))
    duration = float((playback.get("duration") if actual else 0) or current.get("duration") or 0)
    if duration > 0:
        position = min(position, duration)
    if not enabled:
        status = "音乐已关闭"
    elif not connected:
        status = "等待服务连接"
    elif state.get("paused"):
        status = "直播休息中"
    elif not current:
        status = "等待播放" if queue else "等待点歌"
    elif not music.get("playing"):
        status = "已暂停"
    else:
        status = {"playing": "正在播放", "buffering": "正在缓冲", "error": "播放失败",
                  "ended": "正在切歌", "paused": "已暂停"}.get(playback.get("status") if actual else "", "正在加载")
    requester = current.get("requestedBy") or ("AI 主播" if current.get("fromAi") else "自由播放")
    return {"title": current.get("title") or "还没有正在播放的歌曲", "artist": current.get("by") or "",
            "requester": requester if current else "发弹幕「点歌 歌名」加入歌单", "status": status,
            "playing": playing, "position": position, "duration": duration,
            "progress": position / duration if duration > 0 else None,
            "time": clock(position) + " / " + clock(duration if duration > 0 else None),
            "next_title": upcoming.get("title") or "暂无下一首，等你来点歌",
            "next_artist": upcoming.get("by") or "", "queue_count": len(queue)}
