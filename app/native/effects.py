from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen


def draw_particles(painter, reaction, rect, now):
    effect = reaction.get("effect")
    age = now - reaction.get("started", now)
    duration = reaction.get("duration", 0)
    if effect not in ("hearts", "sparkle", "confetti", "bubbles") or not 0 <= age < duration:
        return
    left, top, width, height = rect
    painter.save()
    painter.setClipRect(QRectF(left, top, width, height))
    count = 32 if effect == "confetti" else 18
    for index in range(count):
        progress = (age - (index % 6) * 0.08) / max(0.1, duration - 0.5)
        if not 0 <= progress <= 1:
            continue
        opacity = min(1, progress * 8, (1 - progress) * 4) * 0.85
        horizontal = left + ((index * 0.6180339) % 1) * width
        horizontal += math.sin(progress * 7 + index) * 20
        if effect == "confetti":
            vertical = top - 12 + progress * (height + 24)
        else:
            vertical = top + height * (0.9 - progress * 0.85) + (index % 3) * 12
        size = 5 + index % 5 * 1.5
        painter.save()
        painter.translate(horizontal, vertical)
        painter.setOpacity(opacity)
        color = QColor(("#83e6db", "#9abfff", "#ffd992", "#f8a3c8")[index % 4])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if effect == "hearts":
            painter.setBrush(QColor("#f8a3c8"))
            heart = QPainterPath(QPointF(0, size))
            heart.cubicTo(-size * 2, -size * 0.2, -size, -size * 1.5, 0, -size * 0.5)
            heart.cubicTo(size, -size * 1.5, size * 2, -size * 0.2, 0, size)
            painter.drawPath(heart)
        elif effect == "sparkle":
            painter.rotate(progress * 100 + index * 31)
            star = QPainterPath()
            star.moveTo(0, -size)
            star.lineTo(size * 0.25, -size * 0.25)
            star.lineTo(size, 0)
            star.lineTo(size * 0.25, size * 0.25)
            star.lineTo(0, size)
            star.lineTo(-size * 0.25, size * 0.25)
            star.lineTo(-size, 0)
            star.lineTo(-size * 0.25, -size * 0.25)
            star.closeSubpath()
            painter.drawPath(star)
        elif effect == "confetti":
            painter.rotate(index * 25 + progress * 260)
            painter.drawRoundedRect(QRectF(-size / 2, -size, size, size * 1.5), 2, 2)
        else:
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(QColor(120, 210, 255, 20))
            painter.drawEllipse(QPointF(0, 0), size, size)
        painter.restore()
    painter.restore()
