from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF


def draw_costume(painter, rect, costume):
    if costume == "original":
        return
    painter.save()
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width(), rect.height())
    if costume == "crown":
        painter.setPen(QPen(QColor("#fff0bc"), 0.006))
        painter.setBrush(QColor("#eabc59"))
        points = [(0.36, 0.145), (0.34, 0.065), (0.425, 0.105), (0.5, 0.025),
                  (0.575, 0.105), (0.66, 0.065), (0.64, 0.145)]
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in points]))
        painter.setBrush(QColor("#79e6dc"))
        painter.drawEllipse(QPointF(0.5, 0.105), 0.018, 0.012)
    elif costume == "cool":
        painter.setPen(QPen(QColor("#acdbf7"), 0.006))
        painter.setBrush(QColor(12, 19, 37, 235))
        for horizontal in (0.31, 0.53):
            painter.drawRoundedRect(QRectF(horizontal, 0.28, 0.19, 0.072), 0.015, 0.015)
        painter.drawLine(QPointF(0.5, 0.3), QPointF(0.53, 0.3))
        painter.setPen(QPen(QColor(238, 249, 255, 160), 0.008))
        for horizontal in (0.345, 0.565):
            painter.drawLine(QPointF(horizontal, 0.294), QPointF(horizontal + 0.035, 0.31))
    elif costume == "festival":
        painter.setPen(QPen(QColor("#ffe2ed"), 0.006))
        painter.setBrush(QColor("#e88fb7"))
        for direction in (-1, 1):
            ribbon = QPainterPath(QPointF(0.5, 0.455))
            ribbon.cubicTo(0.5 + direction * 0.15, 0.38, 0.5 + direction * 0.17, 0.5, 0.5, 0.455)
            painter.drawPath(ribbon)
        painter.setBrush(QColor("#ffe2ef"))
        painter.drawEllipse(QPointF(0.5, 0.455), 0.021, 0.015)
    painter.restore()
