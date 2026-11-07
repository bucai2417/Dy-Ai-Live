"""服务与原生桌面入口。"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import urllib.request

import uvicorn

from . import __version__
from .config import settings


def service_ready(base: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/api/health", timeout=1) as response:
            data = json.load(response)
            return response.status == 200 and data.get("ok") is True and "ingest" in data.get("tasks", {})
    except (OSError, ValueError):
        return False


def desktop(renderer_args: list[str]) -> int:
    from .renderer import main as renderer_main

    base = f"http://127.0.0.1:{settings.port}"
    server = None
    thread = None
    try:
        if not service_ready(base):
            server = uvicorn.Server(uvicorn.Config("app.main:app", host=settings.host,
                                                   port=settings.port, log_level="info"))
            thread = threading.Thread(target=server.run, name="live-service", daemon=True)
            thread.start()
            deadline = time.monotonic() + 20
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    logging.error("直播服务启动失败，请检查端口 %s 和 logs/app.log", settings.port)
                    return 1
                time.sleep(0.1)
        elif settings.demo:
            logging.warning("已有服务正在运行，将沿用其弹幕设置；关闭已有工作台后再启动演示可使用模拟弹幕")
        return renderer_main(["--url", base, *renderer_args])
    finally:
        if server is not None:
            server.should_exit = True
            if thread is not None:
                thread.join(timeout=10)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="深蓝 AI 直播")
    parser.add_argument("--version", action="version", version="Dy-Ai-Live " + __version__)
    parser.add_argument("--desktop", action="store_true", help="启动原生直播工作台")
    parser.add_argument("--open", action="store_true", help="兼容旧参数，启动原生工作台")
    parser.add_argument("--demo", action="store_true", help="临时使用模拟弹幕")
    args, renderer_args = parser.parse_known_args(argv)
    if args.demo:
        settings.demo = True
    if args.desktop or args.open:
        return desktop(renderer_args)
    if renderer_args:
        parser.error("未知参数：" + " ".join(renderer_args))
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
