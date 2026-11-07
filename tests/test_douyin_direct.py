"""抖音直连：签名子进程、房间解析、WSS 地址拼装。

这些测试不需要真的开播，也不联网——
重点是保证「拼错的参数」这类问题能被提前抓到，因为抖音对参数缺失
返回的是 HTTP 200 而不是 401，非常难排查（实测踩过）。
"""
from __future__ import annotations

import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

from app.config import ROOT
from app.ingest import douyin_direct
from app.ingest.douyin_direct import (
    SDK_VERSION,
    VERSION_CODE,
    build_ws_url,
    sig_params,
    signer,
)

SIGN_FILE = ROOT / "tools" / "douyin_sig" / "sign_server.mjs"


# ---------------------------------------------------------------- 签名组件
def test_sign_file_exists():
    """签名接口随仓库存在，外部 SDK 为可选组件。"""
    assert SIGN_FILE.is_file(), f"缺少 {SIGN_FILE}"


@pytest.mark.parametrize("missing", [None, "entry", "sdk", "node"])
def test_signer_detects_optional_dependencies(tmp_path, monkeypatch, missing):
    entry = tmp_path / "sign_server.mjs"
    sdk = tmp_path / "webmssdk.js"
    if missing != "entry":
        entry.touch()
    if missing != "sdk":
        sdk.touch()
    monkeypatch.setattr(douyin_direct, "SIGN_FILE", entry)
    monkeypatch.setattr(douyin_direct, "SIGN_SDK", sdk)
    monkeypatch.setattr(douyin_direct.shutil, "which", lambda command: None if missing == "node" else "node")
    candidate = douyin_direct.Signer()
    assert candidate.available is (missing is None)
    if missing is not None:
        assert candidate._start() is False
        assert candidate._proc is None


def test_sign_subprocess_roundtrip():
    """真启一次子进程，确认一问一答的 JSON 行协议能跑通。"""
    if not signer.available:
        pytest.skip("可选签名 SDK 或 Node.js 未配置，跳过真实签名子进程")
    proc = subprocess.run(
        ["node", str(SIGN_FILE)],
        cwd=str(SIGN_FILE.parent),
        input='{"id":1,"op":"ping"}\n',
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if proc.returncode != 0 and "node" in (proc.stderr or "").lower():
        pytest.skip("本机没有 node，跳过签名测试")
    assert '"pong":true' in proc.stdout.replace(" ", "")


# ---------------------------------------------------------------- WSS 参数
def test_sig_params_has_identity_fields():
    """签名参数字段不能少：缺了会让签名对不上。"""
    p = sig_params("123", "999")
    for k in ("live_id", "aid", "version_code", "webcast_sdk_version",
              "room_id", "did_rule", "user_unique_id", "device_platform",
              "identity"):
        assert k in p, f"签名参数缺少 {k}"
    assert p["identity"] == "audience"
    assert p["version_code"] == VERSION_CODE
    assert p["webcast_sdk_version"] == SDK_VERSION


def test_ws_url_contains_all_required_params():
    """WSS URL 必须带齐这些参数。

    抖音对缺参数的连接回 HTTP 200 而不是 401，
    真机上表现为"连上了但立刻断开"，所以这里逐项断言。
    """
    url = build_ws_url("7703620986561661731", "abc123", "7408149185103249000")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    required = [
        "app_name", "room_id", "compress", "version_code",
        "webcast_sdk_version", "update_version_code", "live_id",
        "did_rule", "user_unique_id", "identity", "signature",
        "device_platform", "cookie_enabled", "browser_name", "browser_version",
        "aid", "endpoint", "support_wrds", "im_path", "tz_name",
        "need_persist_msg_count", "heartbeatDuration",
    ]
    missing = [k for k in required if k not in q]
    assert not missing, f"WSS 参数缺少：{missing}"

    assert q["signature"][0] == "abc123"
    assert q["room_id"][0] == "7703620986561661731"
    assert q["identity"][0] == "audience"
    assert q["live_id"][0] == "1"


def test_ws_url_uses_wss_scheme():
    url = build_ws_url("1", "s", "u")
    assert url.startswith("wss://")
    assert "/webcast/im/push/v2/" in url


def test_ws_url_quotes_signature():
    """签名里可能有 + / = 之类字符，必须转义，否则会被截断。"""
    url = build_ws_url("1", "a+b/c=d", "u")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["signature"][0] == "a+b/c=d"
