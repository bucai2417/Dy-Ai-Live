"""独立弹幕桥接服务（可选）。

当你用的抖音弹幕助手只能「HTTP 回调」而不能主动连 WebSocket 时，
让它 POST 到本服务，本服务再转发给主程序（ws://127.0.0.1:8848 的内部通道）。

用法：
    python -m app.bridge_server --port 5002

助手端配置回调地址：http://127.0.0.1:5002/danmu

也支持浏览器直接访问 /danmu?type=chat&nickname=张三&content=你好 做快速自测。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bridge")

MAIN_WS = "ws://127.0.0.1:8848/ws-feed"  # 由主程序提供，见 app/main.py


class Forwarder:
    """把收到的消息转发到主程序；断线自动重连并暂存。"""

    def __init__(self, url: str) -> None:
        self.url = url
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        import websockets

        while True:
            try:
                async with websockets.connect(self.url) as ws:
                    log.info("已连接主程序 %s", self.url)
                    while True:
                        item = await self.queue.get()
                        await ws.send(json.dumps(item, ensure_ascii=False))
            except Exception as exc:
                log.warning("主程序未就绪（%s），3 秒后重试", exc)
                await asyncio.sleep(3)

    def push(self, item: dict) -> None:
        self.queue.put_nowait(item)


def main() -> None:
    port = 5002
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])

    from fastapi import FastAPI, Request
    import uvicorn

    app = FastAPI()
    fwd = Forwarder(MAIN_WS)

    @app.on_event("startup")
    async def _startup() -> None:
        fwd.start()

    @app.post("/danmu")
    async def danmu(request: Request):
        try:
            body = await request.json()
        except Exception:
            return {"ok": False, "msg": "需要 JSON"}
        items = body if isinstance(body, list) else [body]
        for it in items:
            if isinstance(it, dict):
                fwd.push(it)
        return {"ok": True, "count": len(items)}

    @app.get("/danmu")
    async def danmu_get(type: str = "chat", nickname: str = "自测观众", content: str = "你好"):
        fwd.push({"type": type, "nickname": nickname, "content": content})
        return {"ok": True}

    log.info("桥接服务启动：POST http://127.0.0.1:%s/danmu", port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
