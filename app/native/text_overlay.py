from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen


def text_font(size, bold=False):
    result = QFont("Microsoft YaHei UI")
    result.setPixelSize(size)
    result.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return result


@lru_cache(maxsize=128)
def subtitle_pages(text, width, size):
    metrics = QFontMetricsF(text_font(size, True))
    rows, current = [], ""
    for character in text:
        if character == "\n":
            rows.append(current + character)
            current = ""
        elif current and metrics.horizontalAdvance(current + character) > width - 8:
            rows.append(current)
            current = character
        else:
            current += character
    if current or not rows:
        rows.append(current)
    return tuple(tuple(rows[index:index + 2]) for index in range(0, len(rows), 2))


@lru_cache(maxsize=192)
def outlined_image(text, width, size, color, bold, align, elide):
    font = text_font(size, bold)
    metrics = QFontMetricsF(font)
    if elide:
        text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 8)
    image = QImage(max(1, width), math.ceil(metrics.height()) + 8, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        advance = metrics.horizontalAdvance(text)
        horizontal = (width - advance) / 2 if align == "center" else width - advance - 4 if align == "right" else 4
        path = QPainterPath()
        path.addText(QPointF(horizontal, 4 + metrics.ascent()), font, text)
        painter.setPen(QPen(QColor(5, 24, 37, 220), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(color))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
    finally:
        painter.end()
    return image


def draw_outlined(painter, rect, text, size=18, color="#e7f4ff", bold=False, align="left", elide=True):
    left, top, width, height = rect
    image = outlined_image(str(text), round(width), size, color, bold, align, elide)
    painter.drawImage(QPointF(left, top + (height - image.height()) / 2), image)


def draw_subtitle(painter, rect, text, elapsed, duration, size=20, background=False):
    left, top, width, height = rect
    pages = subtitle_pages(text, round(width), size)
    page = min(len(pages) - 1, int(max(0, elapsed) / max(.1, duration) * len(pages)))
    rows = pages[page]
    line_height = math.ceil(QFontMetricsF(text_font(size, True)).height()) + 4
    top += (height - len(rows) * line_height) / 2
    if background:
        metrics = QFontMetricsF(text_font(size, True))
        backing_width = min(width, max(metrics.horizontalAdvance(row.rstrip("\n")) for row in rows) + 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(6, 24, 39, 88))
        painter.drawRoundedRect(QRectF(left + (width - backing_width) / 2, top - 4, backing_width, len(rows) * line_height + 8), 9, 9)
    for index, row in enumerate(rows):
        draw_outlined(painter, (left, top + index * line_height, width, line_height), row.rstrip("\n"),
                      size, "#f5f8ed", True, "center", elide=False)
    if len(pages) > 1:
        draw_outlined(painter, (left, top + len(rows) * line_height, width, 15), f"{page + 1} / {len(pages)}", 10, "#b9d9d9", align="center")
