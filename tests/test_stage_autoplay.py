"""自动播放与后台运行：两条容易退化的前端行为。

这两个问题都不是"报错"，而是"静默不工作"，所以特意用静态检查锁住：
一旦有人把 play() 的 catch 改回只打日志、或删掉后台定时器，这里就会失败。
"""
from __future__ import annotations

from pathlib import Path

import pytest

STAGE_JS = Path(__file__).resolve().parent.parent / "web" / "assets" / "stage.js"


@pytest.fixture(scope="module")
def src() -> str:
    assert STAGE_JS.is_file(), f"找不到 {STAGE_JS}"
    return STAGE_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 自动播放
def test_play_goes_through_try_play(src):
    """play() 必须走统一的 tryPlay。

    之前是 a.play().catch(console.warn)，被浏览器拦下后就此放弃，
    队列里明明有歌却一直不响。
    """
    assert "function tryPlay(" in src, "缺少 tryPlay 封装"
    assert "Player.needPlay = true" in src, "被拦截后要记账，否则不会重试"
    assert "a.play().catch(() =>" not in src, "不要再用静默 catch"


def test_blocked_playback_is_retried(src):
    """被拦截后必须有补偿路径：用户交互 + 定时器。"""
    assert "armAutoplayResume" in src, "缺少交互恢复"
    assert "['pointerdown', 'keydown', 'touchstart']" in src, "交互事件没绑"
    # 定时器里也要补播，因为 OBS 浏览器源永远等不到点击
    assert "Player.needPlay && a && a.paused && a.src" in src, "定时器里没补播"


# ---------------------------------------------------------------- 后台运行
def test_tick_delegates_to_step_mouth(src):
    """rAF 循环只负责调度，实际推进抽成 stepMouth 供后台复用。"""
    assert "function stepMouth(" in src
    assert "function tick() {" in src
    assert "stepMouth();" in src


def test_visibility_change_switches_driver(src):
    """切后台时 rAF 会完全停住，必须换成定时器继续推进。"""
    assert "visibilitychange" in src, "没监听可见性变化"
    assert "setInterval(stepMouth" in src, "后台没有接替驱动"
    assert "startBgFallback()" in src.split("/* ============================== 启动")[-1], \
        "startBgFallback 没在启动处调用"


def test_ducking_survives_background(src):
    """压低音量也不能只靠 rAF，否则后台会永远停在压低状态。"""
    assert "function stopDuckDrive(" in src
    assert "document.hidden" in src
    assert "setInterval(() => { if (!step()" in src


# ---------------------------------------------------------------- 队列显示
def test_queue_shows_song_names(src):
    """播放器要列出队列歌名，而不是只给个数量。"""
    assert "pl-qi" in src, "队列行没渲染"
    assert "requestedBy" in src, "队列没显示点歌人"
    assert "escapeHtml" in src, "歌名来自观众，必须转义"
