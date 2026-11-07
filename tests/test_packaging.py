from pathlib import Path

import pytest

from app import paths
from app.launch import dispatch
from tools.build_windows import build_name


def test_source_paths_ignore_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPY_HOME", raising=False)
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    monkeypatch.delattr(paths.sys, "_MEIPASS", raising=False)
    runtime, resources = paths.runtime_paths()
    assert runtime == Path(paths.__file__).resolve().parents[1]
    assert resources == runtime


def test_frozen_paths_keep_user_data_outside_resources(tmp_path, monkeypatch):
    executable = tmp_path / "便携版" / "Dy-Ai-Live.exe"
    resources = executable.parent / "_internal"
    monkeypatch.delenv("DEEPY_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(resources), raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(executable))
    assert paths.runtime_paths() == (executable.parent, resources)
    assert paths.renderer_command() == [str(executable), "--renderer"]
    override = tmp_path / "用户数据"
    monkeypatch.setenv("DEEPY_HOME", str(override))
    assert paths.runtime_paths() == (override, resources)


def test_frozen_renderer_dispatch_does_not_start_another_server(monkeypatch):
    from app import renderer

    calls = []
    monkeypatch.setattr(renderer, "main", lambda arguments: calls.append(arguments) or 0)
    assert dispatch(["--renderer", "--mute"]) == 0
    assert calls == [["--mute"]]


@pytest.mark.parametrize("arguments,expected", [([], ["--desktop"]), (["--demo"], ["--desktop", "--demo"]), (["--server"], []), (["--version"], ["--version"])])
def test_executable_dispatch(arguments, expected, monkeypatch):
    from app import __main__

    calls = []
    monkeypatch.setattr(__main__, "main", lambda options: calls.append(options) or 0)
    assert dispatch(arguments) == 0
    assert calls == [expected]


def test_release_name_requires_matching_version():
    assert build_name("1.0.0", "a" * 40, "v1.0.0") == "Dy-Ai-Live-1.0.0-windows-x64"
    assert build_name("1.0.0", "a" * 40) == "Dy-Ai-Live-1.0.0+aaaaaaa-windows-x64"
    with pytest.raises(ValueError):
        build_name("1.0.0", "a" * 40, "v1.0.1")
    with pytest.raises(ValueError):
        build_name("../outside", "a" * 40)
