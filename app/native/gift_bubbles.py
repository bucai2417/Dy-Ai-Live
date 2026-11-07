from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QRadialGradient


@dataclass
class Bubble:
    key: str
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    color: str
    sender: dict


class GiftBubbles:
    def __init__(self, bounds):
        self.bounds = bounds
        self.bubbles: dict[str, Bubble] = {}
        self.last_time = None
        self.sprites = {}

    def update(self, senders, config, now, paused=False):
        limit = max(0, min(10, int(config.get("ball_max", 10))))
        active = senders[:limit] if limit and config.get("show_ballpit", True) else []
        keys = {sender["key"] for sender in active}
        self.bubbles = {key: bubble for key, bubble in self.bubbles.items() if key in keys}
        left, top, width, height = self.bounds
        radius = min(44 * max(0.6, min(1.8, float(config.get("ball_size", 1)))),
                     (width / 4 - 8) / 2, (height / 3 - 8) / 2)
        for sender in active:
            key = sender["key"]
            if key in self.bubbles:
                self.bubbles[key].sender = sender
                self.bubbles[key].radius = radius
                continue
            seed = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
            rng = random.Random(seed)
            candidates = [(left + width * ((column + 0.5) / 4), top + height * ((row + 0.5) / 3))
                          for row in range(3) for column in range(4)]
            rng.shuffle(candidates)
            horizontal, vertical = max(candidates, key=lambda point: min(
                (math.hypot(point[0] - other.x, point[1] - other.y) for other in self.bubbles.values()),
                default=width))
            angle = rng.uniform(0, math.tau)
            speed = rng.uniform(45, 70)
            color = ("#8ae7df", "#f3ce89", "#b9b0ff", "#f5acd5", "#8dcfff")[seed % 5]
            self.bubbles[key] = Bubble(key, horizontal, vertical, math.cos(angle) * speed,
                                       math.sin(angle) * speed, radius, color, sender)
        delta = 0 if self.last_time is None or paused else max(0, min(0.1, now - self.last_time))
        self.last_time = now
        speed = max(0.1, min(3, float(config.get("ball_speed", 1))))
        steps = max(1, math.ceil(delta * speed * 120))
        for step in range(steps):
            self._step(delta * speed / steps, bool(config.get("ball_bounce", True)))

    def _walls(self, bubble, bounce):
        left, top, width, height = self.bounds
        low_x, high_x = left + bubble.radius, left + width - bubble.radius
        low_y, high_y = top + bubble.radius, top + height - bubble.radius
        if bounce:
            if bubble.x < low_x or bubble.x > high_x:
                bubble.vx = abs(bubble.vx) if bubble.x < low_x else -abs(bubble.vx)
            if bubble.y < low_y or bubble.y > high_y:
                bubble.vy = abs(bubble.vy) if bubble.y < low_y else -abs(bubble.vy)
            bubble.x = max(low_x, min(high_x, bubble.x))
            bubble.y = max(low_y, min(high_y, bubble.y))
        else:
            if bubble.x < low_x:
                bubble.x = high_x
            elif bubble.x > high_x:
                bubble.x = low_x
            if bubble.y < low_y:
                bubble.y = high_y
            elif bubble.y > high_y:
                bubble.y = low_y

    def _step(self, delta, bounce):
        bubbles = list(self.bubbles.values())
        for bubble in bubbles:
            bubble.x += bubble.vx * delta
            bubble.y += bubble.vy * delta
            self._walls(bubble, bounce)
        for iteration in range(3):
            for index, first in enumerate(bubbles):
                for second in bubbles[index + 1:]:
                    dx, dy = second.x - first.x, second.y - first.y
                    distance = math.hypot(dx, dy)
                    minimum = first.radius + second.radius + 2
                    if distance >= minimum:
                        continue
                    normal_x, normal_y = (dx / distance, dy / distance) if distance > 0.001 else (1, 0)
                    overlap = (minimum - distance) / 2
                    first.x -= normal_x * overlap
                    first.y -= normal_y * overlap
                    second.x += normal_x * overlap
                    second.y += normal_y * overlap
                    relative = (second.vx - first.vx) * normal_x + (second.vy - first.vy) * normal_y
                    if relative < 0:
                        first.vx += relative * normal_x
                        first.vy += relative * normal_y
                        second.vx -= relative * normal_x
                        second.vy -= relative * normal_y
            for bubble in bubbles:
                self._walls(bubble, bounce)

    def draw(self, painter, images, now):
        self.sprites = {key: value for key, value in self.sprites.items() if key in self.bubbles}
        for bubble in self.bubbles.values():
            image = images.get(bubble.sender.get("avatar"))
            signature = (bubble.radius, bubble.sender.get("name"), bubble.sender.get("count"), bubble.sender.get("rank"),
                         image.cacheKey() if image is not None else 0)
            cached = self.sprites.get(bubble.key)
            padding = math.ceil(bubble.radius + 8)
            if cached is None or cached[0] != signature:
                sprite = QImage(padding * 2, padding * 2, QImage.Format.Format_ARGB32_Premultiplied)
                sprite.fill(Qt.GlobalColor.transparent)
                canvas = QPainter(sprite)
                canvas.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
                canvas.translate(padding - bubble.x, padding - bubble.y)
                self._paint_bubbles(canvas, [bubble], images)
                canvas.end()
                cached = (signature, sprite)
                self.sprites[bubble.key] = cached
            painter.drawImage(QPointF(bubble.x - padding, bubble.y - padding), cached[1])
            age = now - bubble.sender.get("at", 0)
            if 0 <= age < 1.2:
                painter.save()
                color = QColor(bubble.color)
                color.setAlpha(int(150 * (1 - age / 1.2)))
                painter.setPen(QPen(color, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(bubble.x, bubble.y), bubble.radius + age * 8, bubble.radius + age * 8)
                painter.restore()

    def _paint_bubbles(self, painter, bubbles, images):
        for bubble in bubbles:
            painter.save()
            painter.translate(bubble.x, bubble.y)
            radius = bubble.radius
            color = QColor(bubble.color)
            gradient = QRadialGradient(-radius * 0.25, -radius * 0.3, radius * 1.5)
            gradient.setColorAt(0, QColor(38, 78, 102, 235))
            gradient.setColorAt(1, QColor(8, 22, 43, 235))
            painter.setBrush(QColor(0, 8, 22, 60))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(0, 4), radius + 2, radius + 2)
            painter.setBrush(gradient)
            painter.setPen(QPen(color, 1.6))
            painter.drawEllipse(QPointF(0, 0), radius, radius)
            avatar_radius = radius * 0.48
            avatar_rect = QRectF(-avatar_radius, -radius * 0.76, avatar_radius * 2, avatar_radius * 2)
            clip = QPainterPath()
            clip.addEllipse(avatar_rect)
            painter.save()
            painter.setClipPath(clip)
            image = images.get(bubble.sender.get("avatar"))
            if image is not None and not image.isNull():
                painter.drawImage(avatar_rect, image)
            else:
                painter.fillRect(avatar_rect, QColor(bubble.color))
                fallback_font = QFont("Microsoft YaHei UI")
                fallback_font.setPixelSize(max(16, int(radius * 0.48)))
                fallback_font.setBold(True)
                painter.setFont(fallback_font)
                painter.setPen(QColor("#18304a"))
                painter.drawText(avatar_rect, Qt.AlignmentFlag.AlignCenter,
                                 (bubble.sender.get("name") or "鱼")[:1])
            painter.restore()
            label_font = QFont("Microsoft YaHei UI")
            label_font.setPixelSize(max(11, int(radius * 0.3)))
            label_font.setBold(True)
            painter.setFont(label_font)
            painter.setPen(QColor("#f4f8ff"))
            label = QFontMetricsF(label_font).elidedText(bubble.sender.get("name") or "观众",
                                                        Qt.TextElideMode.ElideRight, radius * 1.7)
            painter.drawText(QRectF(-radius * 0.86, radius * 0.25, radius * 1.72, radius * 0.45),
                             Qt.AlignmentFlag.AlignCenter, label)
            label_font.setPixelSize(max(9, int(radius * 0.22)))
            label_font.setBold(False)
            painter.setFont(label_font)
            painter.setPen(QColor(bubble.color))
            painter.drawText(QRectF(-radius * 0.65, radius * 0.64, radius * 1.3, radius * 0.23),
                             Qt.AlignmentFlag.AlignCenter, f"#{bubble.sender.get('rank', 1)} · ×{bubble.sender.get('count', 1)}")
            painter.restore()
