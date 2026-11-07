from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BATCH_FILES = sorted(ROOT.glob("*.bat"))


@pytest.mark.parametrize("batch_path", BATCH_FILES, ids=lambda value: value.name)
def test_batch_files_use_utf8_without_bom_and_windows_newlines(batch_path):
    data = batch_path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "\ufffd" not in data.decode("utf-8")
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"\r" not in data.replace(b"\r\n", b"")


def run_batch(batch_path, arguments, cwd, env):
    command_processor = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    command = f'"{command_processor}" /d /s /c ""{batch_path}" {arguments} <nul"'
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=30,
                          encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows CMD")
@pytest.mark.parametrize("name", ["启动.bat", "演示.bat", "直播画面.bat"])
def test_desktop_batches_run_from_another_directory(name, tmp_path):
    environment = os.environ.copy()
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    result = run_batch(ROOT / name, "--help", tmp_path, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "深蓝 AI 直播" in result.stdout
    assert "--desktop" in result.stdout
    assert "启动服务" in result.stdout
    assert "\ufffd" not in result.stdout
    assert "not recognized" not in result.stderr
    assert "不是内部或外部命令" not in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows CMD")
def test_missing_python_shows_help_and_reaches_pause(tmp_path):
    environment = os.environ.copy()
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    result = run_batch(ROOT / "启动.bat", "--help", tmp_path, environment)
    assert result.returncode == 1
    assert "找不到可运行的 Python" in result.stdout
    assert "continue" in result.stdout.lower() or "按任意键" in result.stdout
