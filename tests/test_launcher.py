from __future__ import annotations

import socket

from app import __main__ as launcher, renderer


def test_desktop_reuses_existing_service(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "service_ready", lambda base: True)
    monkeypatch.setattr(renderer, "main", lambda arguments: calls.append(arguments) or 0)

    def unexpected_start(*args, **kwargs):
        raise AssertionError("不应启动第二个服务")

    monkeypatch.setattr(launcher.uvicorn, "Server", unexpected_start)
    assert launcher.desktop(["--fps", "20"]) == 0
    assert calls[0][-2:] == ["--fps", "20"]


def test_desktop_stops_owned_service_after_window_closes(monkeypatch):
    import copy
    from app import settings_store
    from app.settings_store import store

    config = copy.deepcopy(settings_store.DEFAULTS)
    config["ingest"]["enabled"] = False
    config["tts"]["engine"] = "none"
    config["templates"]["enabled"] = False
    monkeypatch.setattr(store, "_data", config)
    for key in ("llm_api_key", "llm_base_url", "llm_model"):
        monkeypatch.setattr(launcher.settings, key, "")
    monkeypatch.setattr(launcher.settings, "demo", False)
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        monkeypatch.setattr(launcher.settings, "port", port_socket.getsockname()[1])

    def window(arguments):
        assert launcher.service_ready(arguments[1])
        return 0

    monkeypatch.setattr(renderer, "main", window)
    assert launcher.desktop([]) == 0
    assert not launcher.service_ready(f"http://127.0.0.1:{launcher.settings.port}")
