"""接入层：所有数据源实现同一个小接口，便于随时切换。

配置来源统一走 Effective.ingest()（= .env 默认值 + data/runtime.json 后台覆盖），
这样在后台设置页改了房间号/接入方式，点「保存并重连接入」就能立刻生效。
"""
from __future__ import annotations

from typing import Protocol

from ..settings_store import Effective


class Ingestor(Protocol):
    name: str

    async def run(self, emit) -> None:
        """持续产生 Event，通过 emit(Event) 投递。应自行处理重连。"""


class OffIngestor:
    """弹幕接入关闭时的空实现：不连任何东西，也不产出事件。

    保留这个类而不是让上层判 None，是为了让「关闭接入」和「接入失败」
    走同一条代码路径，重启逻辑不用分叉。
    """

    name = "off"

    async def run(self, emit) -> None:
        import asyncio

        while True:
            await asyncio.sleep(3600)


def build_ingestor():
    cfg = Effective.ingest()

    # 总开关：后台关掉后就不连抖音，避免无人值守时白开浏览器
    if not cfg.get("enabled", True):
        return OffIngestor()

    source = cfg["source"]

    if source == "mock":
        from .mock import MockIngestor

        return MockIngestor()
    if source == "douyin_direct":
        # 直连：进程里连抖音 WSS，不开浏览器（需要 node 算签名）
        from .douyin_direct import DouyinDirectIngestor, signer

        if not signer.available:
            from .douyin_cdp import DouyinCDPIngestor

            log_msg = "抖音直连不可用（缺签名入口、webmssdk.js 或 Node.js），已自动改用浏览器采集；不使用浏览器可选择 bridge"
            import logging

            logging.getLogger(__name__).warning(log_msg)
            return DouyinCDPIngestor(room=cfg["douyin_room"])
        return DouyinDirectIngestor()
    if source == "douyin":
        # 真实接入：CDP 抓浏览器里的 webcast 帧 + DOM 弹幕
        from .douyin_cdp import DouyinCDPIngestor

        return DouyinCDPIngestor(room=cfg["douyin_room"])
    from .bridge import BridgeIngestor

    return BridgeIngestor(port=cfg["bridge_port"])
