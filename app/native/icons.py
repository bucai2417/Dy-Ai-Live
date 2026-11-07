from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen


def draw_icon(painter, kind, left, top, size=20, color="#9bded7"):
    painter.save()
    painter.translate(left, top)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    if kind == "viewers":
        path.moveTo(2, 12)
        path.cubicTo(7, 3, 17, 3, 22, 12)
        path.cubicTo(17, 21, 7, 21, 2, 12)
        painter.drawPath(path)
        painter.drawEllipse(QPointF(12, 12), 3, 3)
    elif kind == "chat":
        painter.drawRoundedRect(QRectF(3, 3, 18, 14), 4, 4)
        path.moveTo(7, 17)
        path.lineTo(7, 21)
        path.lineTo(12, 17)
        painter.drawPath(path)
        painter.drawLine(QPointF(7, 8), QPointF(17, 8))
        painter.drawLine(QPointF(7, 12), QPointF(13, 12))
    elif kind == "reply":
        path.moveTo(9, 6)
        path.lineTo(3, 12)
        path.lineTo(9, 18)
        path.moveTo(3, 12)
        path.lineTo(15, 12)
        path.quadTo(21, 12, 21, 20)
        painter.drawPath(path)
    else:
        painter.drawRoundedRect(QRectF(5, 5, 14, 14), 3, 3)
        painter.drawRect(QRectF(9, 9, 6, 6))
        for offset in (8, 16):
            painter.drawLine(QPointF(offset, 2), QPointF(offset, 5))
            painter.drawLine(QPointF(offset, 19), QPointF(offset, 22))
            painter.drawLine(QPointF(2, offset), QPointF(5, offset))
            painter.drawLine(QPointF(19, offset), QPointF(22, offset))
    painter.restore()
