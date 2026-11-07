from __future__ import annotations

import asyncio
import logging
import re
from contextlib import aclosing


log = logging.getLogger(__name__)
SENTENCE_END = re.compile(r"[。！？!?；;](?:[”’\"']*)")


class FallbackText(str):
    pass


async def complete_utterance(stream, target: int | None = None) -> str:
    chunks = []
    cut = None
    incomplete = False

    async def collect():
        nonlocal cut, incomplete
        async with aclosing(stream):
            async for chunk in stream:
                if isinstance(chunk, FallbackText):
                    chunks[:] = [str(chunk)]
                    break
                chunks.append(chunk)
                text = "".join(chunks)
                if target is None:
                    continue
                ends = [match.end() for match in SENTENCE_END.finditer(text)]
                complete = next((end for end in ends if end >= target), None)
                if complete is not None:
                    cut = complete
                    break
                if len(text) >= max(target * 3, target + 80):
                    cut = ends[-1] if ends else 0
                    incomplete = True
                    break

    try:
        await asyncio.wait_for(collect(), timeout=20)
    except asyncio.TimeoutError:
        ends = [match.end() for match in SENTENCE_END.finditer("".join(chunks))]
        cut = ends[-1] if ends else 0
        incomplete = True
    if incomplete:
        log.warning("模型未及时完成句子，仅使用已完成的句子；无完整句时跳过此次播报")
    text = "".join(chunks)
    return (text if cut is None else text[:cut]).strip()
