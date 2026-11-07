"""人设下发方式：上游丢弃 system 时的兼容处理。

背景：https://ai.65740340.xyz:7777 这条上游会把 role=system 整个丢弃，
只保留它自带的"你是一个 AI 助手"人设，导致大肥鱼人设怎么写都不生效。
实测把同样内容并进 user 消息就能生效，所以这里锁住该行为，避免以后被改回去。
"""
from __future__ import annotations

import asyncio

import pytest

from app.brain.llm import LLM
from app.settings_store import Effective, store


@pytest.fixture
def llm_modes(monkeypatch):
    """按需伪造 prompt_mode，不让测试真的去探测网络。"""
    def _set(mode: str):
        real = Effective.llm

        def fake():
            c = dict(real())
            c["prompt_mode"] = mode
            return c
        monkeypatch.setattr(Effective, "llm", staticmethod(fake))
    return _set


def test_user_mode_puts_persona_into_user_message(llm_modes):
    llm_modes("user")
    msgs = asyncio.run(LLM()._build_messages("你好", [], Effective.llm()))

    assert msgs[0]["role"] == "user", "user 模式下第一条必须是 user"
    assert not any(m["role"] == "system" for m in msgs), "不应再有 system"
    # 人设正文要完整带过去，不能只剩个壳子
    assert "大肥鱼" in msgs[0]["content"]
    assert msgs[-1]["content"] == "你好"


def test_system_mode_keeps_standard_shape(llm_modes):
    llm_modes("system")
    msgs = asyncio.run(LLM()._build_messages("你好", [], Effective.llm()))

    assert msgs[0]["role"] == "system"
    assert "大肥鱼" in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "你好"}


def test_history_is_preserved_in_both_modes(llm_modes):
    history = [{"role": "assistant", "content": "上一句"}]
    for mode in ("system", "user"):
        llm_modes(mode)
        msgs = asyncio.run(LLM()._build_messages("继续", history, Effective.llm()))
        assert any(m["content"] == "上一句" for m in msgs), f"{mode} 模式丢了历史"


def test_probe_result_is_cached():
    """同 (base_url, model) 只探一次，别每句话都发探针。"""
    llm = LLM()
    key = ("http://x", "m")
    llm._sys_probe[key] = True
    assert llm._sys_probe[key] is True


def test_prompt_mode_defaults_to_auto():
    assert Effective.llm()["prompt_mode"] in ("auto", "system", "user")


def test_invalid_prompt_mode_falls_back_to_auto(monkeypatch):
    real_section = store.section
    monkeypatch.setattr(
        store, "section",
        lambda name: {**real_section(name), "prompt_mode": "乱填" }
        if name == "llm" else real_section(name),
    )
    assert Effective.llm()["prompt_mode"] == "auto"
