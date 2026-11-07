"""通过 Chrome DevTools Protocol 采集抖音直播间弹幕（真实接入，已验证可用）。

原理（关键，别走弯路）：
    不去破解 a_bogus / signature 去直连 wss——那套签名随时会变，维护成本极高。
    而是启动一个带 --remote-debugging-port 的浏览器，打开真实直播间页面，
    用 CDP 的 Network.webSocketFrameReceived 抓到页面自己发出的 webcast 帧，
    再用本地 protobuf 解析器拆出「送礼/进场/点赞/关注」等结构化事件；
    弹幕正文则从页面 DOM 文本里增量采集。

优点：
    - 用真实登录态，命中率高，不依赖签名算法
    - 协议帧 + DOM 双通道，一条挂了另一条还能出内容
    - 浏览器可见，便于人工登录/过验证

前置：本机有 Chrome 或 Edge。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..events import Event
from .protobuf_lite import (
    chat_of,
    collect_users,
    extract_user,
    gunzip_any,
    is_masked_nick,
    is_real_avatar,
    iter_envelopes,
    normalize_text,
    pb_decode,
    pb_decode,
)

log = logging.getLogger(__name__)

CDP_PORT = 9333
PROFILE_DIR = DATA_DIR / "browser-profile"
ROOM_RE = re.compile(r"live\.douyin\.com/(\d+)")

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


# ------------------------------------------------------------------ 工具
def _http_json(url: str, method: str = "GET", timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _list_pages(port: int) -> list[dict]:
    try:
        data = _http_json(f"http://127.0.0.1:{port}/json/list")
        return [t for t in data if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    except Exception:
        return []


def find_browser() -> str | None:
    """返回本机可用的浏览器路径。

    不能只看文件是否存在：有些机器的 Chrome 安装损坏（并行配置错误 -14001），
    文件在但起不来。这里做一次真实启动探测，能起来才算数。
    """
    for p in BROWSER_CANDIDATES:
        if p and Path(p).exists():
            if _probe_browser(p):
                return p
            log.warning("浏览器无法启动，跳过：%s", p)
    for name in ("chrome", "msedge"):
        got = shutil.which(name)
        if got and _probe_browser(got):
            return got
    return None


_PROBE_CACHE: dict[str, bool] = {}


def _probe_browser(exe: str) -> bool:
    """真实探测：用独立端口起一个 headless 实例，能连上 CDP 才算可用。

    不能用 --version：Windows 上 Chromium 系浏览器常直接开窗口、不返回版本号。
    """
    if exe in _PROBE_CACHE:
        return _PROBE_CACHE[exe]

    probe_port = 9400 + (abs(hash(exe)) % 40)
    profile = DATA_DIR / f"probe-{probe_port}"
    proc = None
    ok = False
    try:
        profile.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={probe_port}",
                f"--user-data-dir={profile}",
                "--headless=new", "--no-first-run", "--no-default-browser-check",
                "--disable-gpu", "--mute-audio", "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for _ in range(24):
            time.sleep(0.25)
            if proc.poll() is not None:
                break  # 进程已退出，说明起不来
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{probe_port}/json/version", timeout=1.2
                ) as resp:
                    if resp.status == 200:
                        ok = True
                        break
            except Exception:
                continue
    except Exception:
        ok = False
    finally:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        shutil.rmtree(profile, ignore_errors=True)

    _PROBE_CACHE[exe] = ok
    return ok


def parse_room(value: str) -> str:
    """从链接或纯数字里取房间号。"""
    s = (value or "").strip()
    if not s:
        return ""
    if s.isdigit():
        return s
    m = ROOM_RE.search(s)
    if m:
        return m.group(1)
    try:
        return urllib.parse.urlparse(s).path.strip("/").split("/")[-1]
    except Exception:
        return ""


# ------------------------------------------------------------------ 页面采集脚本
# 不依赖抖音混淆的类名，按文本特征定位弹幕区：找到"子元素都是单行消息"的那个容器。
COLLECT_JS = r"""
(() => {
  const out = { likes: null, lines: [], avatars: {}, owner: '', href: location.href, live: false };
  const om = document.title.match(/^(.+?)的抖音直播间/);
  if (om) out.owner = om[1];

  for (const e of document.querySelectorAll('span,div,b,p')) {
    if (e.children.length) continue;
    const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 20 && t.indexOf('本场点赞') >= 0) {
      const m = t.match(/([\d.]+)(万)?\s*本场点赞/);
      if (m) { out.likes = Math.round(parseFloat(m[1]) * (m[2] ? 10000 : 1)); break; }
    }
  }

  const single = t => /^.{1,24}[：:][^：:]{0,80}$/.test(t)
    || /^.{1,24}(送出|送了)\s*[^：:]{1,20}(\s*[xX×]\s*\d{1,3})?$/.test(t)
    || /^.{1,20}来了$/.test(t);

  const tick = (window.__scanTick = (window.__scanTick || 0) + 1);
  if (tick % 4 === 1 || !window.__chatRoot || !window.__chatRoot.isConnected) {
    let best = null, bestN = 0;
    for (const c of document.querySelectorAll('div')) {
      let n = 0;
      for (const k of c.children) {
        const t = (k.innerText || '').replace(/\s+/g, ' ').trim();
        if (t && t.length <= 90 && single(t)) n++;
      }
      if (n > bestN) { bestN = n; best = c; }
    }
    window.__chatRoot = best;
  }
  const chatRoot = window.__chatRoot;
  const scope = chatRoot || document;
  const leaves = [...scope.querySelectorAll('span,div')].filter(e => !e.children.length);
  const seeds = leaves.filter(e => {
    const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
    return t && t.length <= 90 && (/[：:]/.test(t) || /(送出|送了)/.test(t) || /来了$/.test(t));
  });
  const bestSet = new Set();
  for (const e of seeds) {
    let best = e, n = e.parentElement;
    while (n && n !== document.body && (!chatRoot || chatRoot.contains(n))) {
      const t = (n.innerText || '').replace(/\s+/g, ' ').trim();
      if (t && t.length <= 90 && single(t)) { best = n; n = n.parentElement; } else break;
    }
    bestSet.add(best);
  }
  const chosen = [...bestSet].filter(a => ![...bestSet].some(b => b !== a && b.contains(a)));
  window.__liveNodes = window.__liveNodes || new WeakMap();
  window.__liveNodeSerial = window.__liveNodeSerial || 0;
  window.__liveNodeEpoch = window.__liveNodeEpoch || String(Date.now());
  out.entries = chosen.map(n => {
    const text = (n.innerText || '').replace(/\s+/g, ' ').trim();
    let entry = window.__liveNodes.get(n);
    if (!entry || entry.text !== text) {
      entry = {id: window.__liveNodeEpoch + ':' + String(++window.__liveNodeSerial), text};
      window.__liveNodes.set(n, entry);
    }
    return entry;
  }).filter(entry => entry.text);
  out.lines = out.entries.map(entry => entry.text);

  if (tick % 2 === 0 || !window.__avMap) {
    const av = {};
    for (const img of document.querySelectorAll('img')) {
      const src = img.currentSrc || img.src || '';
      if (src.indexOf('douyinpic.com') < 0) continue;
      if (!/(aweme-avatar|tos-cn-avt|\/aweme\/100x100\/)/.test(src)
          && !/img\/webcast\/[a-f0-9]{16,}\.(p|png|jpe?g|webp)/i.test(src)) continue;
      if (/grade_level|badge|medal|icon|\.svg|ranklist/i.test(src)) continue;
      let n = img.parentElement, name = '';
      for (let i = 0; i < 5 && n && !name; i++) {
        const t = (n.innerText || '').replace(/\s+/g, ' ').trim();
        if (/\d+\s*钻|充值|礼物周榜|小时榜|人气榜/.test(t) || t.length > 40) { n = n.parentElement; continue; }
        const m = t.match(/^([^：:\s]{1,24})/);
        if (m && m[1]) {
          const c = m[1]
            .replace(/关注\d*粉丝\d*.*$/, '').replace(/\d{1,2}\/\d{1,2}.*$/, '')
            .replace(/暂不支持.*$/, '').replace(/来了$/, '').replace(/\d+天前.*/, '')
            .replace(/分享\[视频\]/, '').replace(/在线$/, '').trim();
          if (c.length >= 1 && c.length <= 20 && /[\u4e00-\u9fa5A-Za-z]/.test(c)
              && !/^用户\d{6,}$/.test(c)
              && !/^(送出了?|送了|来了|关注|粉丝|评论|分享)$/.test(c)
              && !/分享\[|前天|小时榜|人气榜|本场点赞|在线观众|贡献/.test(c)) name = c;
        }
        n = n.parentElement;
      }
      if (name && !av[name]) av[name] = src;
    }
    window.__avMap = av;
  }
  Object.assign(out.avatars, window.__avMap || {});
  out.live = !!(chatRoot || (window.__avMap && Object.keys(window.__avMap).length));
  return out;
})()
"""


# ------------------------------------------------------------------ CDP 客户端
class CDP:
    """极简 CDP 客户端：发命令、收事件。"""

    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._ws = None
        self._mid = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._event_cb = None
        self._reader: asyncio.Task | None = None

    async def connect(self) -> None:
        import websockets

        self._ws = await websockets.connect(self.ws_url, max_size=64 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_loop())
        await self.call("Runtime.enable")
        await self.call("Network.enable")

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if "id" in msg and msg["id"] in self._pending:
                    fut = self._pending.pop(msg["id"])
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
                elif "method" in msg and self._event_cb:
                    try:
                        self._event_cb(msg)
                    except Exception:
                        pass
        except Exception:
            pass

    def on_event(self, cb) -> None:
        self._event_cb = cb

    async def call(self, method: str, params: dict | None = None, timeout: float = 10.0) -> Any:
        if self._ws is None:
            raise RuntimeError("CDP 未连接")
        self._mid += 1
        mid = self._mid
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return await asyncio.wait_for(fut, timeout=timeout)

    async def evaluate(self, expression: str) -> Any:
        res = await self.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=15
        )
        return (res or {}).get("result", {}).get("value")

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass


# ------------------------------------------------------------------ 行解析
_JUNK = re.compile(
    r"充钻石|客户端|壁纸通知|消息投稿|互相关注|粉丝团|账号已在其他地方|进入直播间|扫码登录"
    r"|分享\[视频\]|前天分享|小时榜|人气榜|本场点赞|在线观众|贡献"
)
_SYSTEM = re.compile(
    r"欢迎来到直播间|严禁|未成年人|网络诈骗|理性消费|新功能|三屏|点击体验"
)


def is_junk_name(n: str) -> bool:
    if not n:
        return True
    if _JUNK.search(n):
        return True
    if not (1 <= len(n) <= 24):
        return True
    if re.match(r"^(送出了?|送了|来了|关注|粉丝|评论|分享)$", n):
        return True
    return not re.search(r"[\u4e00-\u9fffa-zA-Z]", n)


def _strip_tail_noise(text: str) -> str:
    """切掉被 DOM 拼到弹幕尾巴上的界面噪音。

    抖音输入框提示、进场提示、登录提示紧挨着聊天区，innerText 会把它们
    连成一句。这些后缀有固定措辞，逐个剥掉即可。
    """
    for _ in range(4):
        before = text
        # 「XXX 来了」——进场提示；要求「来了」前面有空格分隔，
        # 避免把「我回来了」「他来了吗」这类正常弹幕误伤。
        # 用 (?<=\s) 强制要求前导空格，且名字里不含空格。
        text = re.sub(r"\s+(?=\S+\s*来了\s*$)\S{1,20}\s*来了\s*$", "", text)
        text = re.sub(r"\s*(需先登录|请先登录|说点什么|发送|聊天室|本场点赞[\d.万]*)[，,。\s]*$", "", text)
        text = text.strip(" ，,、")
        if text == before:
            break
    return text


# 纯进场行只允许「名字 + 空格 + 来了」。必须要求中间有空白，
# 否则「我回来了」会被拆成 name="我回" 误判成进场。
_ENTER_ONLY = re.compile(r"^([^\s，,。？！!?]{1,20})\s+来了$")


def parse_line(raw: str) -> dict | None:
    """把页面上一行聊天文本解析成结构化事件。"""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        return None

    # DOM 的 innerText 常把相邻节点串成一行，例如：
    #   「剪光头了？ 健身boy 来了 需先登录，」
    # 这里把尾部的「XXX 来了」「需先登录」「说点什么…」等界面噪音切掉，
    # 否则 AI 会对着半条系统提示作答，观众看得出是驴唇不对马嘴。
    text = _strip_tail_noise(text)
    if not text:
        return None

    if _SYSTEM.search(text):
        return {"kind": "system", "text": text}

    m = re.match(r"^(.{1,24}?)\s*[：:]\s*(.*)$", text)
    if m:
        name, body = m.group(1).strip(), m.group(2).strip()
        if not name or is_junk_name(name):
            return None
        g = re.match(r"^(?:送出|送了|送给主播)了?\s*(.+?)(?:\s*[xX×]\s*(\d{1,3}))?$", body)
        if g and g.group(1).strip():
            return {"kind": "gift", "name": name, "gift": g.group(1).strip(),
                    "count": int(g.group(2)) if g.group(2) else 1}
        if re.match(r"^(关注了主播|关注了直播间|成为新粉丝)", body):
            return {"kind": "follow", "name": name}
        if not body:
            return None
        return {"kind": "chat", "name": name, "text": body}

    m = re.match(r"^(.{1,24}?)\s*(?:送出|送了|送给主播)了?\s*(.+?)(?:\s*[xX×]\s*(\d{1,3}))?$", text)
    if m and m.group(2).strip() and not is_junk_name(m.group(1).strip()):
        return {"kind": "gift", "name": m.group(1).strip(), "gift": m.group(2).strip(),
                "count": int(m.group(3)) if m.group(3) else 1}

    m = _ENTER_ONLY.match(text)
    if m:
        return {"kind": "enter", "name": m.group(1).strip()}

    m = re.match(r"^(.{1,24}?)\s*(?:关注了主播|关注了直播间|成为新粉丝|推荐了直播)", text)
    if m and not is_junk_name(m.group(1).strip()):
        return {"kind": "follow", "name": m.group(1).strip()}

    # 兜底：剥离噪音后可能只剩正文（昵称节点被一起切掉了）。
    # 这里不能套用 is_junk_name——那是给「昵称」用的，会把「6」「666」
    # 「233」这类纯数字弹幕判成垃圾，而它们恰恰是直播间最高频的互动。
    if len(text) <= 80 and not _JUNK.search(text):
        return {"kind": "chat", "name": "", "text": text}

    return None


# ------------------------------------------------------------------ 接入器
class DouyinCDPIngestor:
    name = "douyin"

    def __init__(self, room: str = "") -> None:
        """room 由调用方从后台配置传入（不再是 .env 的静态值）。

        这样用户在设置页改了房间号，点「保存并重连接入」就能立刻采新房间。
        """
        from ..config import settings

        raw = room or settings.douyin_room
        self.room = parse_room(raw)
        if not self.room:
            log.warning("没有配置抖音房间号，接入层将只等待配置，不会采集任何内容")
        self.port = CDP_PORT
        self._emit = None
        self._seen: set[str] = set()
        self._seen_order: list[str] = []
        self._channel_events: list[tuple[str, str, str, float]] = []
        self._previous_lines: list[str] = []
        self._message_ids: dict[str, None] = {}
        self._avatars: dict[str, str] = {}
        self._roster: dict[str, float] = {}
        self._enter_at: dict[str, float] = {}
        self._last_likes: int | None = None
        self._last_ws_like = 0.0
        self._history_skipped = False
        self._ws_like_seen = False
        self._frame_count = 0
        self._browser_proc: subprocess.Popen | None = None
        self._stopped = False

    # ---------------- 浏览器 ----------------
    def shutdown_browser(self) -> None:
        """关掉本接入层启动的浏览器进程。

        后台把「弹幕接入」总开关关掉时必须调这里，否则 Edge 会一直挂着——
        当初就是因为没保存 Popen 句柄，导致开关关了浏览器还在。
        """
        proc = self._browser_proc
        self._browser_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=4)
                log.info("已关闭采集用浏览器")
            except Exception:
                log.debug("关闭浏览器失败", exc_info=True)
        # 兜底：按端口/user-data-dir 把残留进程清干净。
        # 这一步不能省——浏览器可能在上一轮服务里启动，本对象的 _browser_proc
        # 是 None，只 terminate 自己启动的那个根本关不掉。
        self._kill_by_port()

    def _kill_by_port(self) -> None:
        """确保采集浏览器被彻底清掉。

        双保险：
          ① 按 --remote-debugging-port 匹配主进程
          ② 按 --user-data-dir 匹配（渲染/扩展子进程也带这个参数）

        用 PowerShell 的 Get-CimInstance 而不是 wmic：wmic 在 Win11 已弃用，
        它 /format:csv 的输出首行是空的、字段顺序也不固定，按"最后一个逗号
        后面是 PID"去解析经常取到空串，结果一个都没杀掉——这就是
        "关掉接入总开关、直播间窗口还在"的真正原因。
        """
        port_marker = f"remote-debugging-port={self.port}"
        marker = f"--user-data-dir={PROFILE_DIR}"
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
            "Where-Object { $_.CommandLine -like $env:DSH_M1 -or "
            "$_.CommandLine -like $env:DSH_M2 } | "
            "ForEach-Object { $_.ProcessId }"
        )
        try:
            env = dict(os.environ)
            env["DSH_M1"] = f"*{port_marker}*"
            env["DSH_M2"] = f"*{marker}*"
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=25, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except Exception:
            log.debug("枚举浏览器进程失败", exc_info=True)
            return

        killed = 0
        for line in out.splitlines():
            pid = line.strip()
            if not pid.isdigit():
                continue
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", pid],
                    capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                killed += 1
            except Exception:
                log.debug("结束进程 %s 失败", pid, exc_info=True)
        if killed:
            log.info("已清理采集浏览器进程 %d 个", killed)

    async def _ensure_browser(self) -> dict | None:
        """确保有一个带调试端口的浏览器，且打开了目标直播间页面。"""
        room_url = f"https://live.douyin.com/{self.room}"

        pages = _list_pages(self.port)
        hit = None
        for t in pages:
            m = ROOM_RE.search(t.get("url", ""))
            if m and m.group(1) == self.room:
                hit = t
                break

        if hit is None and not pages:
            exe = find_browser()
            if not exe:
                log.error("没有找到 Chrome / Edge，无法采集抖音弹幕")
                return None
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            log.info("启动浏览器采集直播间：%s", room_url)
            # 必须留句柄：否则关闭接入时无法结束浏览器，它会一直挂着
            self._browser_proc = subprocess.Popen(
                [
                    exe,
                    f"--remote-debugging-port={self.port}",
                    f"--user-data-dir={PROFILE_DIR}",
                    "--no-first-run", "--no-default-browser-check", "--lang=zh-CN",
                    "--window-position=80,60", "--window-size=1200,820",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-background-timer-throttling",
                    "--autoplay-policy=no-user-gesture-required",
                    room_url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            for _ in range(60):
                await asyncio.sleep(0.5)
                if _list_pages(self.port):
                    break
            pages = _list_pages(self.port)

        if hit is None:
            # 已有浏览器但没开这个房间：新开一个标签页，不动用户已开的页面
            try:
                _http_json(
                    f"http://127.0.0.1:{self.port}/json/new?" + urllib.parse.quote(room_url, safe=""),
                    method="PUT",
                )
            except Exception:
                try:
                    _http_json(f"http://127.0.0.1:{self.port}/json/new?{room_url}", method="PUT")
                except Exception as exc:
                    log.warning("打开直播间标签页失败：%s", exc)
            for _ in range(30):
                await asyncio.sleep(0.5)
                for t in _list_pages(self.port):
                    if self.room in (t.get("url") or ""):
                        hit = t
                        break
                if hit:
                    break

        return hit

    # ---------------- 主循环 ----------------
    async def run(self, emit) -> None:
        self._emit = emit
        if not self.room:
            log.error("接入方式=抖音，但没有配置房间号。请到后台「弹幕接入」填写房间号并保存。")
            return

        log.info("抖音真实接入已启用，房间号 %s", self.room)
        fails = 0
        while not self._stopped:
            cdp = None
            try:
                target = await self._ensure_browser()
                if not target:
                    fails += 1
                    if fails in (1, 20) or fails % 60 == 0:
                        log.warning("直播间页面未就绪（第 %d 次重试）", fails)
                    await asyncio.sleep(3)
                    continue

                cdp = CDP(target["webSocketDebuggerUrl"])
                await cdp.connect()
                cdp.on_event(self._on_cdp_event)
                log.info("已挂接浏览器，开始采集：%s", target.get("url", ""))
                fails = 0

                # 等页面把 webcast wss 建起来
                await asyncio.sleep(2)
                await self._poll_loop(cdp)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("采集连接中断（%s），3 秒后重连", exc)
                await asyncio.sleep(3)
            finally:
                if cdp:
                    await cdp.close()
        # 循环结束（被 stop 或取消）：确保浏览器不残留
        self.shutdown_browser()

    async def stop(self) -> None:
        """外部主动停止（关总开关/换房间）：关浏览器并退出 run 循环。"""
        self._stopped = True
        self.shutdown_browser()

    async def _poll_loop(self, cdp: CDP) -> None:
        """每 0.4 秒扫一次页面 DOM，增量产出弹幕/礼物/进场。"""
        while True:
            data = await cdp.evaluate(COLLECT_JS)
            if isinstance(data, dict):
                await self._handle_page(data)
            await asyncio.sleep(0.4)

    async def _handle_page(self, s: dict) -> None:
        href = s.get("href") or ""
        if "live.douyin.com" not in href:
            return  # 跳到登录/验证页就先不管

        for name, avatar in (s.get("avatars") or {}).items():
            if name and avatar and not is_junk_name(name):
                self._avatars[name] = avatar

        lines = s.get("lines") or []
        entries = s.get("entries")
        if entries is None:
            overlap = 0
            for length in range(min(len(self._previous_lines), len(lines)), 0, -1):
                if self._previous_lines[-length:] == lines[:length]:
                    overlap = length
                    break
            entries = [{"id": "", "text": line} for line in lines[overlap:]]
            self._previous_lines = lines[-200:]
        if not self._history_skipped and lines:
            self._history_skipped = True
            for entry in entries:
                if entry.get("id"):
                    self._remember(entry["id"])
            log.info("跳过挂接前已存在的 %d 条历史弹幕", len(lines))
            return

        for entry in entries:
            identifier, line = entry.get("id"), entry.get("text") or ""
            if identifier and identifier in self._seen:
                continue
            if identifier:
                self._remember(identifier)
            ev = parse_line(line)
            if not ev or ev["kind"] == "system":
                continue
            await self._dispatch(ev)

        likes = s.get("likes")
        if isinstance(likes, int):
            self._likes_changed(likes, lines)

    def _remember(self, line: str) -> None:
        self._seen.add(line)
        self._seen_order.append(line)
        if len(self._seen_order) > 800:
            old = self._seen_order.pop(0)
            self._seen.discard(old)

    async def _dispatch(self, ev: dict) -> None:
        kind = ev["kind"]
        raw_name = ev.get("name", "") or ""
        # DOM 通道拿到的昵称同样可能被打码，必须统一清洗，否则画面出现"华*****"
        name = self._display_name(raw_name) or "观众"
        self._roster[name] = time.time()
        # 头像表按「原始昵称」存的（DOM 采集时拿到的是页面上那个写法），
        # 这里必须用原始名查，用清洗后的名字查会一直取不到头像。
        avatar = self._avatars.get(raw_name, "") or self._avatars.get(name, "")

        if kind == "chat":
            text = normalize_text(ev.get("text", ""))
            if not text or self._channel_duplicate("dom", name, text):
                return
            # 头像按显示名再存一份，后续同类事件（送礼/进场）能直接命中
            if avatar:
                self._avatars[name] = avatar
            await self._emit(Event(kind="chat", user_id="", user_name=name,
                                   text=text, avatar=avatar))
        elif kind == "gift":
            gift = ev.get("gift", "礼物")
            cnt = int(ev.get("count", 1))
            if avatar:
                self._avatars[name] = avatar
            await self._emit(Event(kind="gift", user_name=name, gift_name=gift,
                                   gift_count=cnt, gift_value=_gift_value(gift) * cnt,
                                   text=f"送出了{gift}x{cnt}", avatar=avatar))
        elif kind == "enter":
            if not self._enter_once(name):
                return
            if avatar:
                self._avatars[name] = avatar
            await self._emit(Event(kind="enter", user_name=name, avatar=avatar))
        elif kind == "follow":
            if avatar:
                self._avatars[name] = avatar
            await self._emit(Event(kind="follow", user_name=name, avatar=avatar))

    def _enter_once(self, name: str) -> bool:
        """同一个人短时间内只算一次进场。

        协议帧和 DOM 两条通道会各报一次同一个进场，必须共用同一份去重表，
        否则直播间里每次有人进来都会播两条「来了」。
        """
        now = time.time()
        last = self._enter_at.get(name, 0.0)
        if now - last < 120:
            return False
        self._enter_at[name] = now
        if len(self._enter_at) > 3000:
            # 清理过期的，而不是整表清空——清空会让正在直播间的人重复进场
            self._enter_at = {k: v for k, v in self._enter_at.items() if now - v < 120}
        return True

    @staticmethod
    def _text_key(text: str) -> str:
        """去重用的归一化键：去掉 emoji、空白和标点。

        协议帧与 DOM 拿到的同一条弹幕常常差一点（一个带 [呲牙] 表情、一个不带），
        按原文比较会漏掉重复，所以只比较文字骨架。
        """
        s = normalize_text(text)
        return re.sub(r"[^\u4e00-\u9fffa-zA-Z0-9]", "", s)

    def _channel_duplicate(self, source: str, name: str, text: str) -> bool:
        now = time.monotonic()
        key = self._text_key(text) or text
        self._channel_events = [entry for entry in self._channel_events if now - entry[3] < 2]
        for index, (channel, actor, content, stamp) in enumerate(self._channel_events):
            if channel != source and actor == name and content == key:
                self._channel_events.pop(index)
                return True
        self._channel_events.append((source, name, key, now))
        self._channel_events = self._channel_events[-500:]
        return False

    def _message_duplicate(self, method: str, payload: bytes) -> bool:
        common = next((field.buf for field in pb_decode(payload)
                       if field.num == 1 and field.wire == 2), None)
        if not common:
            return False
        identifier = next((field.varint for field in pb_decode(common)
                           if field.num == 2 and field.varint), None)
        if not identifier:
            return False
        key = f"{method}:{identifier}"
        if key in self._message_ids:
            return True
        self._message_ids[key] = None
        if len(self._message_ids) > 2000:
            self._message_ids.pop(next(iter(self._message_ids)))
        return False

    # ---------------- 协议帧 ----------------
    def _on_cdp_event(self, msg: dict) -> None:
        if msg.get("method") != "Network.webSocketFrameReceived":
            return
        p = msg.get("params", {}).get("response", {})
        payload = p.get("payloadData")
        if not payload:
            return
        try:
            raw = base64.b64decode(payload) if p.get("opcode") == 2 else payload.encode("utf-8")
        except Exception:
            return
        self._handle_frame(raw)

    def _handle_frame(self, raw: bytes) -> None:
        """从协议帧里取弹幕/送礼/进场/点赞/关注。

        已用真实帧验证的方法名：
            WebcastChatMessage   #2 User  #3 正文
            WebcastMemberMessage 进场
            WebcastSocialMessage 关注
            WebcastLikeMessage   点赞
            WebcastGiftMessage   送礼
        """
        if not raw:
            return
        self._frame_count += 1
        buf = gunzip_any(raw)
        try:
            for method, payload in iter_envelopes(buf):
                if self._message_duplicate(method, payload):
                    continue
                if method == "WebcastChatMessage":
                    self._on_chat(payload)
                    continue

                # 房间人数：抖音真实在线观众数，不是网页连接数
                if method == "WebcastRoomUserSeqMessage":
                    self._on_room_seq(payload)
                    continue

                # 其余类型统一：取消息体第一个用户当动作发起者
                user = self._first_user(payload, field_number=5 if method == "WebcastLikeMessage" else 2)
                if not user and method != "WebcastLikeMessage":
                    continue
                user = user or {}
                actor, avatar = user.get("name") or "", user.get("avatar") or ""
                actor = self._display_name(actor) or "观众"
                self._roster[actor] = time.time()

                if method == "WebcastGiftMessage":
                    gift, cnt = self._gift_info(payload)
                    self._emit_async(Event(
                        kind="gift", user_id=user.get("id", ""), user_name=actor, gift_name=gift, gift_count=cnt,
                        gift_value=_gift_value(gift) * cnt,
                        text=f"送出了{gift}x{cnt}", avatar=avatar,
                    ))
                elif method == "WebcastMemberMessage":
                    if self._enter_once(actor):
                        self._emit_async(Event(kind="enter", user_name=actor, avatar=avatar))
                elif method == "WebcastSocialMessage":
                    self._emit_async(Event(kind="follow", user_name=actor, avatar=avatar))
                elif method == "WebcastLikeMessage":
                    self._ws_like_seen = True
                    self._last_ws_like = time.time()
                    count, total = self._like_values(payload)
                    self._emit_async(Event(kind="like", user_id=user.get("id", ""), user_name=actor,
                                           gift_count=count, avatar=avatar, like_count=count,
                                           like_total=total, like_source=f"douyin:{self.room}"))
        except Exception as exc:
            log.debug("帧解析异常：%s", exc)

    def _on_room_seq(self, payload: bytes) -> None:
        """房间人数：解析 WebcastRoomUserSeqMessage。

        实测字段：
            #3  varint  当前在线观众数（"在线"显示这个）
            #7  varint  累计观看人次（整场累计，远大于在线）
        两个都取出来，界面上区分开，避免观众以为"在线"是累计。
        """
        try:
            online = 0
            total = 0
            for f in pb_decode(payload):
                if f.varint is None:
                    continue
                if f.num == 3:
                    online = f.varint
                elif f.num == 7:
                    total = f.varint
            if online <= 0 and total <= 0:
                return
            self._room_online = online
            self._room_total = total
            log.debug("房间人数：在线 %d，累计 %d", online, total)
        except Exception:
            log.debug("人数帧解析失败", exc_info=True)

    def room_stats(self) -> dict:
        """给上层读的房间人数（0 表示还没收到人数帧）。"""
        return {"online": getattr(self, "_room_online", 0),
                "total_watch": getattr(self, "_room_total", 0)}

    def _on_chat(self, payload: bytes) -> None:
        text, user = chat_of(payload)
        text = normalize_text(text)
        if not text:
            return
        raw_name = (user or {}).get("name") or ""
        avatar = (user or {}).get("avatar") or ""
        name = self._display_name(raw_name)
        if not name:
            return
        if self._channel_duplicate("ws", name, text):
            return
        self._roster[name] = time.time()
        if avatar and is_real_avatar(avatar):
            self._avatars[name] = avatar
        else:
            avatar = self._avatars.get(name, "")
        self._emit_async(Event(kind="chat", user_id=(user or {}).get("id", ""), user_name=name, text=text, avatar=avatar,
                               is_admin=(user or {}).get("is_admin") is True))

    @staticmethod
    def _first_user(payload: bytes, field_number: int = 2) -> dict | None:
        """优先取消息对应的 User 字段，退化时再递归找。"""
        for f in pb_decode(payload):
            if f.num == field_number and f.wire == 2 and f.buf:
                u = extract_user(f.buf)
                if u:
                    return u
        users = collect_users(payload)
        return users[0] if users else None

    @staticmethod
    def _display_name(raw: str) -> str:
        """未登录时抖音返回打码昵称（如"金***"），转成可读的占位名。

        注意：不能直接把原文拼上去，否则画面会出现"观众金***"这种残星号。
        用「可见前缀 + 稳定短哈希」既好看又保证不同观众不互相覆盖。
        """
        s = (raw or "").strip()
        if not s:
            return ""
        if is_masked_nick(s):
            # 打码昵称的前缀可能带标点甚至本身就是"观众"（如"观众、***"），要洗掉
            prefix = re.sub(r"[^\u4e00-\u9fffa-zA-Z0-9]", "", s.split("*")[0])[:2]
            prefix = re.sub(r"^观众|^用户|^粉丝", "", prefix)
            tag = hashlib.md5(s.encode("utf-8")).hexdigest()[:4]
            return f"观众{prefix}{tag}"
        return s

    @staticmethod
    def _gift_info(payload: bytes) -> tuple[str, int]:
        """GiftMessage：#15 Gift 结构，里面 #2 是礼物名，另有重复次数。"""
        name, cnt = "礼物", 1
        fields = pb_decode(payload)
        for f in fields:
            if f.wire == 0 and f.num in (5, 6) and f.varint and 1 <= f.varint <= 9999:
                cnt = int(f.varint)
                break
        for f in fields:
            if f.wire == 2 and f.buf and f.num not in (1, 2):
                sub = pb_decode(f.buf)
                for s in sub:
                    if s.num == 2 and s.wire == 2 and s.buf:
                        cand = s.text()
                        if 1 <= len(cand) <= 20 and not cand.startswith("http"):
                            name = cand
                            break
                if name != "礼物":
                    break
        return name, max(1, cnt)

    @staticmethod
    def _like_count(payload: bytes) -> int:
        return DouyinCDPIngestor._like_values(payload)[0]

    @staticmethod
    def _like_values(payload: bytes) -> tuple[int, int | None]:
        fields = {field.num: field.varint for field in pb_decode(payload) if field.wire == 0}
        return max(0, int(fields.get(2, 1))), fields.get(3)

    def _emit_async(self, ev: Event) -> None:
        """CDP 事件在读取协程里触发，这里切回事件循环投递。"""
        if not self._emit:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._emit(ev))
        except Exception:
            pass

    # 兼容旧调用
    def _emit_threadsafe(self, ev: Event) -> None:
        self._emit_async(ev)

    def _likes_changed(self, total: int, lines: list[str]) -> None:
        """页面累计值只用于无协议点赞时的补采，不猜测点赞人。"""
        first = self._last_likes is None
        delta = max(0, total - (total if first else self._last_likes))
        self._last_likes = total
        if self._ws_like_seen or (not first and delta <= 0):
            return
        self._emit_async(Event(kind="like", user_name="直播间观众", gift_count=delta,
                               like_count=0 if first else delta, like_total=total,
                               like_source=f"douyin:{self.room}"))


# 常见礼物估值（钻石/音浪），用于打分排序；未知礼物给保守值
_GIFT_PRICE = {
    "小心心": 1, "玫瑰": 1, "棒棒糖": 5, "啤酒": 2, "粉丝团灯牌": 6, "入团卡": 10,
    "为ta点亮": 1, "抖音1号": 1888, "嘉年华": 3000, "火箭": 1000, "跑车": 500,
    "爱心": 1, "大啤酒": 9, "送你花花": 5,
}


def _gift_value(name: str) -> int:
    for k, v in _GIFT_PRICE.items():
        if k in (name or ""):
            return v
    return 5
