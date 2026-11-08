from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen, QRadialGradient

from .state import FrameStore, StageState
from .actions import pose
from .effects import draw_particles
from .music_view import music_view
from .gift_bubbles import GiftBubbles
from .costumes import draw_costume
from .stunts import draw_stunt, stunt_age
from .display import validate_size
from .layout import stage_layout
from .avatar import LayeredAvatar
from .world_scene import WorldRenderer
from .fishing_scene import FishingRenderer
from .gift_scene import GiftRenderer
from .game_scene import GameRenderer
from .ticker import TickerRenderer
from .icons import draw_icon
from .text_overlay import draw_outlined, draw_subtitle

from ..paths import RESOURCE_ROOT

ART_DIR = RESOURCE_ROOT / "web" / "assets" / "whale" / "art"


def font(size: int, bold: bool = False) -> QFont:
    result = QFont("Microsoft YaHei UI")
    result.setPixelSize(size)
    result.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return result


class Scene:
    outlined = staticmethod(draw_outlined)

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.portrait = height > width
        self.design_width = 720 if self.portrait else 1280
        self.design_height = 1280 if self.portrait else 720
        self.layout = stage_layout(self.portrait)
        self.avatar = QImage(str(ART_DIR / "deepy.png"))
        if self.avatar.isNull():
            raise RuntimeError("人物素材加载失败：deepy.png")
        self.stunt_rider = self.avatar.copy(0, 0, self.avatar.width(), int(self.avatar.height() * 0.79))
        self.patches = json.loads((ART_DIR / "deepy.patches.json").read_text(encoding="utf-8"))
        self.patch_images = {key: QImage(str(ART_DIR / value["file"])) for key, value in self.patches.items()}
        self.rig = None
        self.avatar_error = ""
        try:
            self.rig = LayeredAvatar()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.avatar_error = "骨架素材加载失败，已使用原始立绘"
            logging.getLogger(__name__).warning("%s：%s", self.avatar_error, error)
        self.background = self._background()
        self.gifts = GiftBubbles(self.layout.bubbles)
        self.world = WorldRenderer()
        self.fishing = FishingRenderer()
        self.gift_show = GiftRenderer()
        self.games = GameRenderer()
        self.ticker = TickerRenderer()
        self.hud_key = None
        self.hud_image = None

    def resize(self, width: int, height: int):
        self.width, self.height = validate_size(width, height)
        self.portrait = height > width
        self.design_width = 720 if self.portrait else 1280
        self.design_height = 1280 if self.portrait else 720
        self.background = self._background()

    def _layout(self, state):
        settings = state["config"].get("stage") or {}
        self.layout = stage_layout(self.portrait, settings.get("show_tokens", True),
                                   settings.get("show_datapods", True))
        if self.gifts.bounds != self.layout.bubbles:
            old_left, old_top, old_width, old_height = self.gifts.bounds
            left, top, width, height = self.layout.bubbles
            for bubble in self.gifts.bubbles.values():
                bubble.x = left + (bubble.x - old_left) / old_width * width
                bubble.y = top + (bubble.y - old_top) / old_height * height
            self.gifts.bounds = self.layout.bubbles

    def _background(self) -> QImage:
        image = QImage(self.width, self.height, QImage.Format.Format_RGB32)
        painter = QPainter(image)
        painter.scale(self.width / self.design_width, self.height / self.design_height)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.design_width, self.design_height)
        gradient.setColorAt(0, QColor("#08182d"))
        gradient.setColorAt(0.55, QColor("#102c47"))
        gradient.setColorAt(1, QColor("#080f24"))
        painter.fillRect(QRectF(0, 0, self.design_width, self.design_height), gradient)
        glow = QRadialGradient(self.design_width * 0.52, self.design_height * 0.38, self.design_width * 0.6)
        glow.setColorAt(0, QColor(40, 147, 194, 85))
        glow.setColorAt(1, QColor(15, 31, 55, 0))
        painter.fillRect(QRectF(0, 0, self.design_width, self.design_height), glow)
        painter.setPen(QPen(QColor(120, 196, 220, 13), 1))
        for offset in range(-400, 1400, 80):
            painter.drawLine(QPointF(offset, 0), QPointF(offset + 400, self.design_height))
        painter.end()
        return image

    def text(self, painter, rect, text, size=18, color="#e7f4ff", bold=False, align=None):
        painter.setFont(font(size, bold))
        painter.setPen(QColor(color))
        flags = align if align is not None else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(QRectF(*rect), flags, str(text))

    def panel(self, painter, rect, radius=22):
        painter.setPen(QPen(QColor(142, 201, 233, 37), 1))
        painter.setBrush(QColor(9, 24, 44, 198))
        painter.drawRoundedRect(QRectF(*rect), radius, radius)

    def elided(self, painter, rect, text, size=16, color="#e7f4ff", bold=False):
        painter.setFont(font(size, bold))
        value = QFontMetricsF(painter.font()).elidedText(str(text), Qt.TextElideMode.ElideRight, rect[2])
        self.text(painter, rect, value, size, color, bold)

    def render(self, state: dict, elapsed: float, now: float) -> QImage:
        self._layout(state)
        image = self.background.copy()
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self.width / self.design_width, self.height / self.design_height)
        try:
            self._ambient(painter, state.get("animation_time", elapsed))
            painter.save()
            if not self.portrait:
                painter.setClipRect(QRectF(*self.layout.stage))
            stunt = stunt_age(state, now)
            gift = bool((state.get("gift_show") or {}).get("active"))
            game = bool((state.get("game") or {}).get("active"))
            if gift or game:
                stunt = None
            fishing = (state.get("fishing") or {}).get("active", False)
            interacting = (state.get("fishing") or {}).get("phase") == "interacting"
            if interacting and ((state.get("fishing") or {}).get("interruption") or {}).get("world"):
                fishing = False
            if game and not gift:
                self.games.draw(painter, self, state, self.layout.effects, now)
            elif fishing:
                if not interacting:
                    stunt = None
                self.fishing.draw(painter, self, state, self.layout.effects, now, show_actor=not gift and stunt is None)
            elif (state["config"].get("world") or {}).get("enabled", False):
                self.world.draw(painter, self, state, self.layout.effects, now, show_actor=stunt is None and not gift)
            elif stunt is None and not gift:
                self._avatar(painter, state, elapsed, now)
            if stunt is not None:
                draw_stunt(painter, self, state, self.layout.effects, *stunt, now)
            if gift:
                self.gift_show.draw(painter, self, state, self.layout.effects, now)
            if not game and (not gift or state["gift_show"]["active"].get("effect") != "video"):
                self._video(painter, state)
            painter.restore()
            painter.drawImage(QRectF(0, 0, self.design_width, self.design_height), self._hud(state))
            self._footer(painter, state, now)
            self.gifts.update(state.get("gift_senders", []), state["config"].get("stage") or {}, now, state["paused"])
            painter.save()
            painter.setOpacity(.82)
            self.gifts.draw(painter, state.get("avatar_images", {}), now)
            painter.restore()
            if state["paused"]:
                self._break_screen(painter)
        finally:
            painter.end()
        return image

    def _hud(self, state):
        goal = state.get("like_goal") or {}
        active = goal.get("active") or {}
        goal_key = tuple(goal.get(key) for key in ("enabled", "total", "step", "remaining", "target", "pending"))
        key = (self.width, self.height, state["config"], state["paused"], state["connected"],
               state["stats"], state["room"], state["danmu"], state["events"], goal_key,
                active.get("label"), active.get("milestones"), (state.get("fishing") or {}).get("active", False))
        if key != self.hud_key:
            image = QImage(self.width, self.height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(self.width / self.design_width, self.height / self.design_height)
            try:
                self._header(painter, state)
                self._cards(painter, state)
                self._like_meter(painter, state)
                self._messages(painter, state)
            finally:
                painter.end()
            self.hud_key, self.hud_image = copy.deepcopy(key), image
        return self.hud_image

    def _ambient(self, painter, elapsed):
        painter.setPen(QPen(QColor(165, 225, 255, 38), 1))
        for index in range(16):
            horizontal = (index * 97.3 + math.sin(elapsed * 0.25 + index) * 16) % self.design_width
            vertical = self.design_height - (index * 129 + elapsed * (7 + index % 4)) % self.design_height
            radius = 2 + index % 5
            painter.setBrush(QColor(140, 213, 243, 10 + index % 4 * 5))
            painter.drawEllipse(QPointF(horizontal, vertical), radius, radius)

    def _avatar(self, painter, state, elapsed, now):
        elapsed = state.get("animation_time", elapsed)
        now = state.get("paused_at", now) if state["paused"] else now
        settings = state["config"].get("stage") or {}
        token_space = self.portrait and settings.get("show_tokens", True)
        target_height = self.layout.avatar_height * max(0.1, min(3, float(settings.get("scale", 0.64)))) / 0.64
        target_width = target_height * self.avatar.width() / self.avatar.height()
        center = self.layout.stage[0] + self.layout.stage[2] / 2
        center += float(settings.get("offset_x", 0)) * self.design_width * 0.35
        top = self.layout.avatar_top + float(settings.get("offset_y", -0.11) + 0.11) * self.design_height * 0.3
        bob = math.sin(elapsed * 1.35) * 4
        reacting = now < state["reaction_until"]
        reaction = state["reaction"] if reacting else {}
        movement = pose(reaction, now)
        music = music_view(state, now)
        groove = music["playing"] and settings.get("native_music_motion", True) and not reacting
        beat = math.sin(music["position"] * 3.6) if groove else 0
        tilt = math.sin(elapsed * 1.1) * 0.7 + movement["tilt"] + beat * 1.8
        painter.save()
        pivot = 0.5 if reaction.get("motion") in ("handstand", "spin") else 0.7
        painter.translate(center + movement["x"] + beat * 4, top + target_height * pivot + bob + movement["y"])
        painter.rotate(tilt)
        painter.scale((-1 if settings.get("flip") else 1) * movement["scale_x"],
                      movement["scale_y"] * (1 + math.sin(elapsed * 1.5) * 0.003))
        painter.setOpacity(float(settings.get("opacity", 1)))
        rect = QRectF(-target_width / 2, -target_height * pivot, target_width, target_height)
        self.draw_character(painter, state, rect, elapsed, now, reaction, movement)
        painter.restore()
        if self.avatar_error and settings.get("avatar_mode", "layered") == "layered":
            self.text(painter, (center - 170, top + target_height, 340, 25), self.avatar_error, 12, "#ffd196")
        if settings.get("native_effects", True):
            draw_particles(painter, reaction, (max(24, center - target_width * 0.62), max(210 if self.portrait else 110, top),
                                               min(self.design_width - 48, target_width * 1.24), target_height), now)
        if reacting:
            reaction_top = max(286 if token_space else 218 if self.portrait else 110, top + 24)
            self.panel(painter, (center - 104, reaction_top, 208, 42), 21)
            label = reaction.get("label") or "听到你啦"
            self.text(painter, (center - 100, reaction_top, 200, 42), label, 17, "#fbe2a2", align=Qt.AlignmentFlag.AlignCenter)

    def draw_character(self, painter, state, rect, elapsed, now, reaction=None, movement=None, cropped=False):
        settings = state["config"].get("stage") or {}
        reaction = reaction or {}
        movement = movement or pose(reaction, now)
        target_width, target_height = rect.width(), rect.height()
        mouth = state["mouth"] if now - state["mouth_at"] < 0.3 and not state["paused"] else 0
        mouth = max(mouth, movement["mouth"])
        blink = max(movement["eyes"], max(0, 1 - abs((elapsed % 4.7) - 4.4) / 0.09))
        costume = "crown" if now < state.get("celebration_until", 0) else state.get("costume") or settings.get("costume", "original")
        if self.rig is not None and settings.get("avatar_mode", "layered") == "layered":
            return self.rig.draw(painter, rect, reaction=reaction, now=now, elapsed=elapsed,
                                 skin=state.get("outfit") or settings.get("outfit", "sailor"), accessory=costume,
                                 patches=self.patch_images, metadata=self.patches, mouth=mouth, blink=blink,
                                 blush=movement["blush"], debug=settings.get("show_bones", False), cropped=cropped)
        painter.save()
        opacity = painter.opacity()
        if cropped:
            painter.setClipRect(QRectF(rect.x(), rect.y(), rect.width(), rect.height() * .79), Qt.ClipOperation.IntersectClip)
        painter.drawImage(rect, self.avatar)
        mouth = state["mouth"] if now - state["mouth_at"] < 0.3 and not state["paused"] else 0
        mouth = max(mouth, movement["mouth"])
        blink = max(0, 1 - abs((elapsed % 4.7) - 4.4) / 0.09)
        blink = max(blink, movement["eyes"])
        for key, alpha in (("eyes", blink), ("mouth", 1 - mouth)):
            meta = self.patches[key]["rect"]
            painter.setOpacity(opacity * alpha)
            painter.drawImage(QRectF(rect.x() + meta[0] * target_width, rect.y() + meta[1] * target_height,
                                     meta[2] * target_width, meta[3] * target_height), self.patch_images[key])
        if movement["blush"]:
            painter.setOpacity(opacity * movement["blush"] * 0.35)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#f287b0"))
            for cheek in (0.36, 0.65):
                painter.drawEllipse(QRectF(rect.x() + cheek * target_width, rect.y() + 0.355 * target_height,
                                          target_width * 0.07, target_height * 0.024))
        painter.setOpacity(opacity)
        draw_costume(painter, rect, costume)
        painter.restore()

    def _video(self, painter, state):
        image = state.get("video_image")
        if image is None or image.isNull() or state["paused"]:
            return
        scale = float((state["config"].get("stage") or {}).get("video_scale", 1.0))
        left, top, available_width, available_height = self.layout.video
        height = min(available_height, 470 * scale, available_width * image.height() / image.width())
        width = height * image.width() / image.height()
        center = left + available_width / 2
        painter.drawImage(QRectF(center - width / 2, top, width, height), image)
        label = "奶蛙小剧场" + (f" · 待播 {state.get('video_pending')}" if state.get("video_pending") else "")
        self.panel(painter, (center - 122, top + height + 8, 244, 34), 17)
        self.text(painter, (center - 118, top + height + 8, 236, 34), label, 15, "#bcecdc",
                  align=Qt.AlignmentFlag.AlignCenter)

    def _header(self, painter, state):
        width = self.design_width
        self.elided(painter, (24, 17, width - 235, 35), state["config"].get("name") or "大肥鱼", 26, bold=True)
        self.text(painter, (25, 51, 275, 18), "DEEP BLUE  /  深蓝陪伴电台", 11, "#86aecb")
        badge = "休息中" if state["paused"] else "LIVE  直播中" if state["connected"] else "正在重连"
        self.panel(painter, (width - 174, 22, 150, 34), 17)
        painter.setBrush(QColor("#f4c37d" if state["paused"] else "#63e6b9" if state["connected"] else "#d6a37a"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(width - 156, 39), 4, 4)
        self.text(painter, (width - 143, 22, 113, 34), badge, 14)

    def _cards(self, painter, state):
        show_tokens = (state["config"].get("stage") or {}).get("show_tokens", True)
        if (state["config"].get("stage") or {}).get("show_datapods", True):
            values = [("viewers", state["room"].get("online", "—")), ("chat", state["danmu"]),
                      ("reply", state["stats"].get("replies", 0))]
            for index, (icon, value) in enumerate(values):
                left, top, size, height = self.layout.stats[index]
                self.panel(painter, (left, top, size, height), 12)
                draw_icon(painter, icon, left + 9, top + 7)
                text = self._compact_number(value) if isinstance(value, (int, float)) else str(value)
                self.elided(painter, (left + 35, top + 5, size - 42, 24), text, 14, bold=True)
        if show_tokens:
            self._tokens(painter, state)
        if (state.get("fishing") or {}).get("active"):
            self._fishing_ranking(painter, state)
        elif (state["config"].get("stage") or {}).get("show_ranking", True):
            ranking = state["stats"].get("ranking") or []
            left, top, width, height = self.layout.ranking
            self.panel(painter, self.layout.ranking, 18)
            self.text(painter, (left + 18, top + 10, width - 36, 24), "星光守护榜", 15, "#f3d7a5", True)
            if not ranking:
                self.elided(painter, (left + 16, top + 43, width - 32, 24), "每一份陪伴都值得被看见", 12, "#86a2bb")
            for index, item in enumerate(ranking[:3]):
                label = f"{index + 1:02d}   {item.get('name', '观众')}"
                painter.setFont(font(14))
                label = QFontMetricsF(painter.font()).elidedText(label, Qt.TextElideMode.ElideRight, width - 36)
                self.text(painter, (left + 18, top + 42 + index * 27, width - 36, 25), label, 13)

    def _fishing_ranking(self, painter, state):
        board = state["stats"].get("fishingRanking") or {}
        left, top, width, height = self.layout.ranking
        self.panel(painter, self.layout.ranking, 18)
        self.text(painter, (left + 14, top + 8, width - 28, 22), "今日钓鱼榜 · 总价值", 14, "#f3d7a5", True)
        ranking = board.get("ranking") or []
        capacity = 3 if self.portrait else 5
        if not ranking:
            self.elided(painter, (left + 14, top + 45, width - 28, 23), "本轮贡献最多，钓获即可上榜", 12, "#a3c5ce")
        for index, record in enumerate(ranking[:capacity]):
            row_top = top + 36 + index * 23
            color = "#ffdc99" if index == 0 else "#c4e1e8"
            self.text(painter, (left + 14, row_top, 22, 22), f"{index + 1:02d}", 12, color, True)
            self.elided(painter, (left + 42, row_top, width - 138, 22), record["name"], 12, color)
            self.text(painter, (left + width - 93, row_top, 79, 22), self._compact_number(record["value"]) + " 分", 12, color,
                      align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        community = board.get("community") or {}
        footnote = (f"全场协力 {community['count']} 条 · {self._compact_number(community['value'])} 分"
                    if community.get("count") else "累计鱼获分 · 同日重启保留")
        self.elided(painter, (left + 14, top + height - 22, width - 28, 17), footnote, 10, "#7fa5b4")

    def _tokens(self, painter, state):
        stats = state["stats"]
        label = "今日 Token" + (" · 估算" if stats.get("estimated") else "")
        total = self._compact_number(stats.get("tokens", 0))
        cost = float(stats.get("cost", 0) or 0)
        amount = f"¥{cost:,.4f}" if cost < 10000 else f"¥{self._compact_number(cost)}"
        left, top, width, height = self.layout.tokens
        self.panel(painter, self.layout.tokens, 12)
        draw_icon(painter, "tokens", left + 9, top + 7)
        self.elided(painter, (left + 35, top + 5, 190, 24), label + " " + total, 12, "#c9e2e9")
        self.elided(painter, (left + 234, top + 5, width - 244, 24), "估费 " + amount, 12, "#7ee0d0", True)

    @staticmethod
    def _compact_number(value):
        value = max(0, float(value or 0))
        if value >= 100000000:
            return f"{value / 100000000:.2f}亿"
        if value >= 10000:
            return f"{value / 10000:.2f}万"
        return f"{value:,.0f}"

    def _like_meter(self, painter, state):
        config = state["config"].get("likeGoal") or {}
        if not config.get("show_progress", True):
            return
        goal = state.get("like_goal") or {}
        enabled = goal.get("enabled", config.get("enabled", True))
        if not enabled and not goal.get("active"):
            return
        left, top, meter_width, height = self.layout.like_meter
        total = int(goal.get("total", 0))
        step = max(1, int(goal.get("step", config.get("step", 100))))
        remaining = int(goal.get("remaining", step))
        active = goal.get("active")
        label = (f"{active['label']} · 还差 {remaining:,} 赞" if active
                 else f"点赞整活  {total:,} / {goal.get('target', step):,}")
        if active and active.get("milestones", 1) > 1:
            label += f" · 连达 {active['milestones']} 档"
        if goal.get("pending"):
            label += f" · 待播 {goal['pending']}"
        self.elided(painter, (left, top, meter_width, 18), label, 11, "#a4e4e3", True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#243f56"))
        painter.drawRoundedRect(QRectF(left, top + 22, meter_width, 4), 2, 2)
        progress = max(0, min(1, 1 - remaining / step))
        if progress:
            painter.setBrush(QColor("#7ee0d0"))
            painter.drawRoundedRect(QRectF(left, top + 22, meter_width * progress, 4), 2, 2)

    def _messages(self, painter, state):
        if not (state["config"].get("stage") or {}).get("show_danmu", True):
            return
        left, top, width, height = self.layout.messages
        self.panel(painter, (left, top, width, height))
        self.text(painter, (left + 18, top + 12, width - 36, 24), "鱼塘聊天室", 15, bold=True)
        row_height = 26
        capacity = max(1, int((height - 48) // row_height))
        events = state["events"][-capacity:]
        if not events:
            self.elided(painter, (left + 18, top + 49, width - 36, 28), "发条弹幕，和我打个招呼吧", 13, "#86a2bb")
        for index, event in enumerate(events):
            kind = event.get("kind")
            content = event.get("text") or {"enter": "来到直播间", "follow": "关注了主播", "like": "点了赞"}.get(kind, "")
            if kind == "gift":
                content = f"送出 {event.get('giftName') or '礼物'} ×{event.get('giftCount') or 1}"
            line = f"{event.get('userName', '观众')}  {content}"
            painter.setFont(font(14))
            line = QFontMetricsF(painter.font()).elidedText(line, Qt.TextElideMode.ElideRight, width - 36)
            self.text(painter, (left + 18, top + 40 + index * row_height, width - 36, 26), line, 14,
                      "#f3d7a5" if kind == "gift" else "#c7ddeb")

    def _footer(self, painter, state, now):
        width, height = self.design_width, self.design_height
        show_player = (state["config"].get("music") or {}).get("show_player", True)
        if show_player:
            self._music_player(painter, state, now)
        if now < state["subtitle_until"] and (state["config"].get("stage") or {}).get("show_subtitle", True):
            rect = self.layout.subtitle
            game = {} if (state.get("game") or {}).get("active") else state.get("fishing") or {}
            if game.get("active"):
                left, top, scene_width, scene_height = self.layout.effects
                rect = (left + 20, top + scene_height - 110 * scene_width / 960 - 82, scene_width - 40, 82)
            if not game.get("active") or game.get("phase") not in ("caught", "escaped"):
                started = state.get("subtitle_at", state["subtitle_until"] - 8)
                settings = state["config"].get("stage") or {}
                draw_subtitle(painter, rect, state["subtitle"], now - started, state["subtitle_until"] - started,
                              int(settings.get("subtitle_size", 20)), settings.get("subtitle_backing", False))
        ticker = self.ticker.draw(painter, state["config"].get("ticker") or {}, width, height, state.get("animation_time", 0))
        if not ticker and not (self.portrait and show_player):
            self.text(painter, (32, height - 31, width - 64, 20), "很高兴，今天也能和你见面。", 12, "#83a8c2",
                      align=Qt.AlignmentFlag.AlignCenter)

    def _music_player(self, painter, state, now):
        view = music_view(state, now)
        left, top, width, height = self.layout.music
        self.panel(painter, (left, top, width, height), 22)
        accent = "#7ee0d0" if view["playing"] else "#92abc2"
        self.text(painter, (left + 16, top + 8, width - 110, 22), "音乐电台 · " + view["status"], 12, accent, True)
        self.text(painter, (left + width - 85, top + 8, 67, 22), f"待播 {view['queue_count']}", 11, "#8ba8bf",
                  align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        painter.save()
        painter.translate(left + 43, top + 61)
        painter.scale(.82, .82)
        painter.rotate(view["position"] * 30)
        gradient = QRadialGradient(0, 0, 32)
        gradient.setColorAt(0, QColor("#34677a"))
        gradient.setColorAt(0.45, QColor("#172c42"))
        gradient.setColorAt(1, QColor("#37576b"))
        painter.setPen(QPen(QColor("#4c7788"), 1))
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(0, 0), 31, 31)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for radius in (19, 25):
            painter.drawEllipse(QPointF(0, 0), radius, radius)
        painter.setBrush(QColor(accent))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), 10, 10)
        painter.setBrush(QColor("#102436"))
        painter.drawEllipse(QPointF(0, 0), 3, 3)
        painter.setPen(QPen(QColor(180, 231, 236, 120), 2))
        painter.drawLine(QPointF(12, -15), QPointF(19, -22))
        painter.restore()
        self.elided(painter, (left + 82, top + 35, width - 98, 26), view["title"],
                    19 if self.portrait else 16, bold=True)
        details = (view["artist"] + " · " if view["artist"] else "") + view["requester"]
        self.elided(painter, (left + 82, top + 64, width - 98, 20), details, 11, "#9db7cd")
        progress_top = top + (94 if self.portrait else 100)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#24394e"))
        painter.drawRoundedRect(QRectF(left + 18, progress_top, width - 36, 4), 2, 2)
        if view["progress"] is not None and view["progress"] > 0:
            painter.setBrush(QColor(accent))
            painter.drawRoundedRect(QRectF(left + 18, progress_top, (width - 36) * view["progress"], 4), 2, 2)
        time_top = top + (107 if self.portrait else 111)
        self.text(painter, (left + 18, time_top, 116, 18), view["time"], 10, "#91adc3")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent))
        for index in range(5):
            bar_height = 3 + (0.5 + 0.5 * math.sin(view["position"] * 7 + index * 1.8)) * 11 if view["playing"] else 3
            painter.drawRoundedRect(QRectF(left + width - 49 + index * 6, time_top + 15 - bar_height, 3, bar_height), 1, 1)
        if self.portrait:
            next_text = view["next_title"] + (" · " + view["next_artist"] if view["next_artist"] else "")
            self.text(painter, (left + 146, top + 106, 52, 22), "下一首", 11, "#7ee0d0", True)
            self.elided(painter, (left + 204, top + 106, width - 270, 22), next_text, 13)
        else:
            self.text(painter, (left + 18, top + 137, 52, 20), "下一首", 11, "#7ee0d0", True)
            self.elided(painter, (left + 78, top + 137, width - 94, 20), view["next_title"], 13, bold=True)
            self.elided(painter, (left + 18, top + 159, width - 36, 17), view["next_artist"] or "发弹幕点歌，加入歌单", 10, "#8ba8bf")

    def _break_screen(self, painter):
        painter.fillRect(QRectF(0, 106, self.design_width, self.design_height - 106), QColor(6, 15, 32, 210))
        center = self.design_height * 0.49
        self.text(painter, (32, center - 70, self.design_width - 64, 35), "BE RIGHT BACK", 15, "#75d8cd",
                  align=Qt.AlignmentFlag.AlignCenter)
        self.text(painter, (32, center - 18, self.design_width - 64, 70), "稍作休息 · 马上回来", 34, bold=True,
                  align=Qt.AlignmentFlag.AlignCenter)
        self.text(painter, (32, center + 60, self.design_width - 64, 45), "谢谢你的陪伴，鱼塘暂时安静一下", 17, "#9ab7cc",
                  align=Qt.AlignmentFlag.AlignCenter)


class RenderWorker(threading.Thread):
    def __init__(self, state: StageState, frames: FrameStore, width=720, height=1280, fps=30):
        super().__init__(name="native-render", daemon=True)
        self.state, self.frames = state, frames
        self._size = self._requested_size = validate_size(width, height)
        self.size_lock = threading.Lock()
        self.target_fps = fps
        self.stop_event = threading.Event()

    @property
    def size(self):
        with self.size_lock:
            return self._size

    @property
    def width(self):
        return self.size[0]

    @property
    def height(self):
        return self.size[1]

    @property
    def requested_size(self):
        with self.size_lock:
            return self._requested_size

    def request_size(self, width: int, height: int):
        size = validate_size(width, height)
        with self.size_lock:
            self._requested_size = size

    def run(self):
        try:
            scene = Scene(*self.size)
            started = time.perf_counter()
            sample_start, count, fps = started, 0, 0.0
            deadline = started
            while not self.stop_event.is_set():
                before = time.perf_counter()
                size = self.requested_size
                if size != (scene.width, scene.height):
                    scene.resize(*size)
                image = scene.render(self.state.snapshot(), before - started, time.monotonic())
                after = time.perf_counter()
                count += 1
                if after - sample_start >= 1:
                    fps, count, sample_start = count / (after - sample_start), 0, after
                self.frames.publish(image, fps, (after - before) * 1000)
                with self.size_lock:
                    self._size = size
                deadline = max(deadline + 1 / self.target_fps, after)
                self.stop_event.wait(max(0, deadline - time.perf_counter()))
        except Exception as error:
            self.frames.error = str(error)
        finally:
            self.frames.close()

    def stop(self):
        self.stop_event.set()
        self.join(timeout=5)
