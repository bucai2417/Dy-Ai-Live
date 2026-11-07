from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter


class TickerRenderer:
    def __init__(self):
        self.key = None
        self.strip = None
        self.distance = 0.0
        self.last_clock = None

    def draw(self, painter, config, width, height, clock):
        text = " · ".join(part.strip() for part in str(config.get("text", "")).splitlines() if part.strip())
        if not config.get("enabled", False) or not text:
            self.last_clock = None
            return False
        speed = float(config.get("speed", 55))
        if text != self.key:
            font = QFont("Microsoft YaHei UI")
            font.setPixelSize(15)
            metrics = QFontMetricsF(font)
            self.strip = QImage(max(1, int(metrics.horizontalAdvance(text)) + 4), 26, QImage.Format.Format_ARGB32_Premultiplied)
            self.strip.fill(0)
            ink = QPainter(self.strip)
            ink.setFont(font)
            ink.setPen(QColor("#c8e8e4"))
            ink.drawText(QPointF(0, (26 - metrics.height()) / 2 + metrics.ascent()), text)
            ink.end()
            self.key, self.distance, self.last_clock = text, 0, clock
        if self.last_clock is not None:
            self.distance += max(0, clock - self.last_clock) * speed
        self.last_clock = clock
        left, top, available = 32, height - 32, width - 64
        painter.save()
        painter.fillRect(QRectF(left, top, available, 26), QColor(8, 28, 42, 220))
        painter.setClipRect(QRectF(left, top, available, 26))
        cycle = self.strip.width() + 90
        self.distance %= cycle
        horizontal = left - self.distance
        while horizontal < left + available:
            painter.drawImage(QPointF(horizontal, top), self.strip)
            horizontal += cycle
        painter.restore()
        return True
