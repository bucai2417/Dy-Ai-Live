"""LLM 客户端：优先走 OpenAI 兼容接口，未配置时回落到内置离线话术引擎。"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import AsyncIterator

import httpx

from ..config import settings
from ..settings_store import Effective, store
log = logging.getLogger(__name__)

SYSTEM_TEMPLATE = """你是抖音直播间里的虚拟主播「{name}」，形象是一条胖胖的卡通鱼。
人设：{style}
直播场景：无人值守直播，你在实时看弹幕并口头回应观众。

必须遵守：
{rules}"""


class LLM:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._cfg_key = None
        # system 是否被上游丢弃的探测缓存：(base_url, model) → bool
        self._sys_probe: dict[tuple[str, str], bool] = {}
        self._probe_lock = asyncio.Lock()
        self._fail = 0          # 连续失败次数
        self._until = 0.0       # 熔断到期时间
        self.context_provider = None

    def _context_block(self):
        if self.context_provider is None:
            return ""
        from .capabilities import STATE_RULES
        try:
            context = self.context_provider()
        except Exception:
            log.exception("读取人物运行状态失败")
            return "人物实时状态暂时不可用，不要臆测人物正在执行的行为。"
        return STATE_RULES + "\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))

    # ---- 配置随时可变，这里按需重建 ----
    def _cfg(self) -> dict:
        return Effective.llm()

    @property
    def enabled(self) -> bool:
        """只是「配置齐全」，不代表 Key 一定有效——有效与否要看 active。"""
        c = self._cfg()
        return bool(c["base_url"] and c["api_key"] and c["model"])

    @property
    def active(self) -> bool:
        """真正在调模型：配置齐全且没被熔断。

        状态展示要区分这两个概念，否则 Key 已失效时界面还显示"大脑在线"，
        用户会以为 AI 在思考，其实一直是离线模板在回话。
        """
        return self.enabled and not self.tripped

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return "离线（未配置）"
        if self.tripped:
            left = int(self._until - time.time())
            return f"熔断中（Key 无效或连续失败，{left} 秒后重试）"
        return "在线"

    @property
    def tripped(self) -> bool:
        """熔断中：Key 无效或连续失败，暂时不请求，直接用离线话术。"""
        return time.time() < self._until

    def _trip(self, seconds: float, why: str) -> None:
        self._until = time.time() + seconds
        log.warning("LLM 暂时熔断 %.0f 秒（%s），期间使用离线话术", seconds, why)

    def reset_breaker(self) -> None:
        """测试连接成功后立即恢复调用，不必等熔断到期。"""
        self._fail = 0
        self._until = 0.0

    def _ensure_client(self) -> httpx.AsyncClient | None:
        if not self.enabled:
            return None
        key = (self._cfg()["base_url"], self._cfg()["api_key"])
        if self._client is None or key != self._cfg_key:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
            self._cfg_key = key
            self._fail = 0
            self._until = 0.0   # 换了 Key 就重置熔断
        return self._client

    def system_prompt(self) -> str:
        p = store.section("persona")
        return SYSTEM_TEMPLATE.format(
            name=p.get("name") or settings.persona_name,
            style=p.get("style") or settings.persona_style,
            rules=p.get("rules") or "",
        ) + "\n本直播不播放、演唱或续写国歌、军歌、红歌等歌曲；收到此类请求请简短请观众换一首。自动点歌只能使用主播已确认的歌单。"

    def _dropped_system(self) -> bool:
        """同步版判定（供测试与非 async 场景使用）。"""
        c = self._cfg()
        mode = str(c.get("prompt_mode") or "auto").lower()
        if mode == "system":
            return False
        if mode == "user":
            return True
        return self._probe_dropped_system(c)

    async def _dropped_system_async(self) -> bool:
        """异步版判定：探测要发网络请求，必须放到线程里别卡住事件循环。"""
        c = self._cfg()
        mode = str(c.get("prompt_mode") or "auto").lower()
        if mode == "system":
            return False
        if mode == "user":
            return True
        key = (c["base_url"], c["model"])
        if key in self._sys_probe:
            return self._sys_probe[key]
        # 加锁：两句话同时进来时，别两个协程都去发探针（会白花两次请求、
        # 日志还会打两遍，看起来像缓存失效）。抢到锁的先探，后来的等结果。
        async with self._probe_lock:
            if key in self._sys_probe:
                return self._sys_probe[key]
            result = await asyncio.to_thread(self._probe_dropped_system, c)
            self._sys_probe[key] = result
            return result

    def _probe_dropped_system(self, cfg: dict) -> bool:
        """发一句探针，看模型认不认 system。

        探针内容故意用了 normal 模型绝不会自发说出的字样（鱼/大肥鱼），
        所以"回复里没有暗号"就等于 system 没生效。
        结果按 (base_url, model) 缓存，进程内只探一次。
        """
        key = (cfg["base_url"], cfg["model"])
        if key in self._sys_probe:
            return self._sys_probe[key]

        token = "吞拿鱼"
        probe_system = f"你必须自称「{token}」，不认识这个词也要照说。"
        dropped = False
        try:
            url = cfg["base_url"].rstrip("/") + "/chat/completions"
            r = httpx.post(
                url,
                json={
                    "model": cfg["model"],
                    "messages": [
                        {"role": "system", "content": probe_system},
                        {"role": "user", "content": "你是谁？五个字以内"},
                    ],
                    "max_tokens": 24,
                    "temperature": 0,
                },
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                timeout=httpx.Timeout(20.0, connect=8.0),
                trust_env=False,
            )
            if r.status_code == 200:
                from ..metrics import metrics

                data = r.json()
                text = (data["choices"][0]["message"]["content"] or "")
                metrics.add_usage(data.get("usage"), probe_system + "你是谁？五个字以内", text)
                dropped = token not in text
            else:
                # 401/403 说明 Key 不通，探测没有结论——不能当成"支持 system"，
                # 否则会误判成正常运行，实际一直在走离线话术。
                log.warning("system 探测返回 HTTP %s，跳过判定", r.status_code)
                return False
        except Exception as exc:
            log.warning("system 探测失败（%s），跳过判定", exc)
            return False

        if dropped:
            log.warning(
                "该模型会忽略 system 提示词（已实测），人设将改由 user 消息下发"
            )
        self._sys_probe[key] = dropped
        return dropped

    async def _build_messages(self, user_prompt: str, history: list[dict] | None,
                              cfg: dict) -> list[dict]:
        """拼装请求消息。

        正常情况下人设走 system，这是最"正"的做法。
        但遇到会丢弃 system 的上游时，人设会被并进第一条 user 消息——
        实测这样能让人设生效（多条 user 是叠加的，不会被覆盖）。
        """
        system = self.system_prompt()
        context = self._context_block()
        if context:
            system += "\n\n" + context
        if not await self._dropped_system_async():
            msgs = [{"role": "system", "content": system}]
        else:
            # 并入 user：先立规矩，再给问题，模型会照着演
            msgs = [{
                "role": "user",
                "content": f"【角色设定，务必严格遵守，不要跳出】\n{system}",
            }]
        for h in (history or [])[-6:]:
            msgs.append(h)
        msgs.append({"role": "user", "content": user_prompt})
        return msgs

    async def classify_intent(self, text: str):
        from .intent import PROMPT, decode_intent
        from ..metrics import metrics

        client = self._ensure_client()
        if client is None or self.tripped:
            return None
        cfg = self._cfg()
        mode = str(cfg.get("prompt_mode") or "auto").lower()
        dropped = mode == "user" or (mode == "auto" and self._sys_probe.get(
            (cfg["base_url"], cfg["model"]), False))
        messages = [{"role": "user" if dropped else "system", "content": PROMPT + "\n" + self._context_block()},
                    {"role": "user", "content": json.dumps({"弹幕": text}, ensure_ascii=False)}]
        try:
            response = await asyncio.wait_for(client.post(
                cfg["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={"model": cfg["model"], "messages": messages, "temperature": 0,
                      "max_tokens": 200, "stream": False}, timeout=6.0), timeout=6.5)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"].get("content") or ""
            if not isinstance(content, str):
                raise ValueError("意图内容不是文本")
            usage = data.get("usage") or {}
            metrics.add_usage(usage, "".join(message["content"] for message in messages), content)
            result = decode_intent(content)
            if result is None:
                log.warning("意图识别返回无效格式，使用本地规则")
            self._fail = 0
            return result
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                self._trip(600, f"意图识别鉴权失败 HTTP {status}")
            elif status == 429:
                self._trip(60, "意图识别被限流")
            else:
                self._fail += 1
            log.warning("意图识别 HTTP %s，使用本地规则", status)
        except (TimeoutError, asyncio.TimeoutError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            self._fail += 1
            log.warning("意图识别超时或返回异常，使用本地规则")
        if self._fail >= 3:
            self._trip(60, "意图识别连续失败")
        return None

    async def stream(
        self, user_prompt: str, history: list[dict] | None = None, name: str = ""
    ) -> AsyncIterator[str]:
        """产出文本增量；无 LLM 时走本地模板（一次性产出）。"""
        from ..metrics import metrics

        client = self._ensure_client()
        if client is None or self.tripped:
            metrics.add_offline()
            # 后台关掉「无模型时用内置话术」就干脆不说，避免刷一堆无意义台词
            if not _fallback_on():
                return
            from .utterance import FallbackText
            yield FallbackText(offline_reply(user_prompt, name))
            return

        cfg = self._cfg()
        messages = await self._build_messages(user_prompt, history, cfg)

        payload = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "stream": True,
            # 让 OpenAI 兼容服务在流末尾回传真实 token 用量（不支持的会忽略此项）
            "stream_options": {"include_usage": True},
        }
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"}

        try:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")[:300]
                    log.error("LLM %s: %s", resp.status_code, body)
                    # 401/403 = Key 无效，重试没意义，直接长熔断，等用户改配置
                    if resp.status_code in (401, 403):
                        self._trip(600, f"Key 无效 HTTP {resp.status_code}")
                    elif resp.status_code == 429:
                        self._trip(60, "被限流")
                    else:
                        self._fail += 1
                        if self._fail >= 3:
                            self._trip(60, f"连续 {self._fail} 次失败")
                    metrics.add_offline()
                    if _fallback_on():
                        from .utterance import FallbackText
                        yield FallbackText(offline_reply(user_prompt, name))
                    return
                self._fail = 0
                usage = None
                produced: list[str] = []
                try:
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data in ("", "[DONE]"):
                            continue
                        try:
                            import json
    
                            obj = json.loads(data)
                            # 部分服务在流末尾单独发一帧 usage（需 stream_options）
                            if obj.get("usage"):
                                usage = obj["usage"]
                            delta = obj["choices"][0]["delta"].get("content") or ""
                        except Exception:
                            continue
                        if delta:
                            produced.append(delta)
                            yield delta
                finally:
                    from ..metrics import metrics
    
                    prompt_text = "".join(m.get("content", "") for m in messages)
                    metrics.add_usage(usage, prompt_text, "".join(produced))
        except Exception as exc:
            log.error("LLM 请求失败：%s", exc)
            self._fail += 1
            if self._fail >= 3:
                self._trip(60, f"连续 {self._fail} 次网络失败")
            metrics.add_offline()
            if not _fallback_on():
                return
            from .utterance import FallbackText
            yield FallbackText(offline_reply(user_prompt, name))

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()


# ---------------------------------------------------------------- 离线引擎
# 语义相近的模板：不直接复读弹幕，避免"复读机"感
# 注意：{name} 只在自然的位置出现一次，念起来才像真人
from ..lines import OFFLINE_DEFAULTS as _TEMPLATES


# ---------------------------------------------------------------- 防重复
_recent_lines: list[str] = []
REPEAT_MEMORY = 40  # 记住最近说过的 40 句，避免短时间内重复


def _non_repeat(pool: list[str]) -> str:
    """从候选里挑一条最近没说过的；全都说过了就挑最早说过的。"""
    if not pool:
        return "嗯，我在听着呢"
    fresh = [p for p in pool if p not in _recent_lines]
    if fresh:
        pick_one = random.choice(fresh)
    else:
        # 全都说过了：优先挑不是刚说过的那句，避免"上一句刚说完又说一遍"
        last = _recent_lines[-1] if _recent_lines else None
        cands = [p for p in pool if p != last] or pool
        counts = {p: _recent_lines.count(p) for p in cands}
        least = min(counts.values())
        pick_one = random.choice([p for p in cands if counts[p] == least])
    _recent_lines.append(pick_one)
    if len(_recent_lines) > REPEAT_MEMORY:
        _recent_lines.pop(0)
    return pick_one


def _fallback_on() -> bool:
    """后台「无模型时用内置话术」开关，读失败时按开启处理（保持可用）。"""
    try:
        return bool(Effective.fallback().get("offline_reply", True))
    except Exception:
        return True


def offline_reply(text: str, name: str = "") -> str:
    """无 LLM 时的兜底：靠关键词意图匹配 + 模板生成，保证直播不哑火。

    关键：不直接复读弹幕原文（那是"复读机"），而是识别意图后给出自然的回应。
    另外同一句话不会连续重复——直播里连续两条一样的回复特别假。
    """
    q = text.strip()
    if not q:
        return random_topic()
    low = q.lower()
    n = name or "这位鱼友"

    def pick(key: str) -> str:
        from ..settings_store import store

        tpl = _non_repeat(store.lines("offline_" + key) or _TEMPLATES[key])
        s = tpl.format(name=n)
        # 只在模板本身带人名时才补停顿，避免每条回复都用名字开头（很像机器人）
        if "{name}" in tpl:
            s = s.replace(n, n + "，", 1).replace(n + "，，", n + "，")
        return s

    # 顺序即优先级：先判断更具体的意图，"你好漂亮"不能被当成打招呼
    if re.search(r"介绍|你是谁|干什么|做什么|名字|叫什么", low):
        return pick("intro")
    if re.search(r"漂亮|好看|可爱|帅|喜欢你|真美|好听", low):
        return pick("praise")
    if re.search(r"几岁|多大|年龄", low):
        return pick("age")
    if re.search(r"唱|歌|才艺|表演", low):
        return pick("sing")
    if re.search(r"吃|饿|饭|宵夜|外卖", low):
        return pick("food")
    if re.search(r"累|烦|难过|失眠|加班|压力|难受", low):
        return pick("mood")
    if re.search(r"你好|在吗|hi|hello|哈喽|晚上好|早上好|主播好", low):
        return pick("greet")
    if re.search(r"怎么|为什么|如何|能不能|可不可以|吗|？|\?", low):
        return pick("how")
    # 纯表情弹幕（已由 normalize 转成 emoji）
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", q):
        return pick("emoji")
    if len(q) <= 4:
        return pick("short")
    return pick("fallback")


def random_topic() -> str:
    from ..settings_store import store

    topics = store.lines("idle_topics")
    return random.choice(topics) if topics else "你们今天都吃的啥？"
