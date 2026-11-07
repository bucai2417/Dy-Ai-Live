from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen, QPolygonF, QRadialGradient


def draw_item(painter, kind, preset):
    body, accent = QColor(preset["color"]), QColor(preset["accent"])
    metal = QColor("#efd8a0")
    glass = QColor("#183f56")
    shine = QColor("#eafff8")
    gradient = QLinearGradient(80, 20, 215, 140)
    gradient.setColorAt(0, body.lighter(115))
    gradient.setColorAt(.55, body)
    gradient.setColorAt(1, accent)

    def brush(color=gradient, edge=accent, thickness=2):
        painter.setBrush(color)
        painter.setPen(QPen(edge, thickness))

    def ellipse(left, top, width, height, color=gradient):
        brush(color)
        painter.drawEllipse(QRectF(left, top, width, height))

    def box(left, top, width, height, color=gradient, radius=7):
        brush(color)
        painter.drawRoundedRect(QRectF(left, top, width, height), radius, radius)

    def path(points, color=gradient):
        brush(color)
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in points]))

    shape = preset["shape"]
    if shape == "filter":
        box(72, 44, 35, 19, accent)
        box(195, 91, 38, 19, accent)
        box(94, 25, 112, 112, QColor(87, 166, 186, 115), 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(body.red(), body.green(), body.blue(), 180))
        painter.drawRoundedRect(QRectF(100, 62, 100, 65), 12, 12)
        if kind == "eel_filter":
            eel = QPainterPath(QPointF(118, 110))
            eel.cubicTo(180, 123, 117, 66, 175, 74)
            painter.setPen(QPen(accent.darker(130), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(eel)
            ellipse(169, 67, 10, 9, shine)
            ellipse(173, 69, 3, 4, glass)
        else:
            for offset in range(4):
                box(119, 48 + offset * 18, 61, 10, body.lighter(125), 3)
        for horizontal, vertical, radius in ((185, 49, 4), (115, 82, 3), (181, 114, 3)):
            ellipse(horizontal, vertical, radius * 2, radius * 2, shine)
        box(91, 20, 118, 18, accent)
        box(91, 129, 118, 15, accent)
        box(110, 6, 78, 16, gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 140), 4))
        painter.drawLine(QPointF(105, 43), QPointF(105, 120))
    elif shape == "chest":
        box(75, 63, 150, 76, accent)
        box(79, 73, 142, 58, gradient)
        lid = QPainterPath(QPointF(75, 64))
        lid.cubicTo(75, 2, 225, 2, 225, 64)
        lid.closeSubpath()
        brush(gradient)
        painter.drawPath(lid)
        for horizontal in (100, 192):
            box(horizontal, 29, 9, 106, metal, 2)
        box(75, 63, 150, 9, metal, 2)
        box(140, 57, 22, 28, metal, 4)
        ellipse(148, 64, 7, 10, glass)
        for horizontal in (89, 214):
            ellipse(horizontal, 118, 5, 5, metal)
    elif shape == "boot":
        path([(103, 22), (174, 22), (169, 92), (216, 107), (226, 126), (213, 141), (91, 141), (87, 119)])
        box(98, 17, 82, 17, accent, 3)
        box(89, 133, 135, 12, accent, 4)
        painter.setPen(QPen(body.lighter(140), 5))
        painter.drawLine(QPointF(113, 43), QPointF(106, 117))
        ellipse(141, 51, 17, 22, metal)
    elif shape == "bottle":
        bottle = QPainterPath(QPointF(128, 32))
        bottle.lineTo(128, 51)
        bottle.cubicTo(83, 61, 98, 112, 100, 133)
        bottle.quadTo(150, 153, 200, 133)
        bottle.cubicTo(202, 112, 217, 61, 172, 51)
        bottle.lineTo(172, 32)
        bottle.closeSubpath()
        brush(gradient)
        painter.drawPath(bottle)
        box(124, 15, 52, 23, metal, 4)
        box(122, 74, 55, 49, QColor("#f4e4bd"), 4)
        painter.setPen(QPen(accent, 2))
        for vertical in (88, 97, 106):
            painter.drawLine(QPointF(132, vertical), QPointF(164, vertical))
        painter.setPen(QPen(shine, 4))
        painter.drawLine(QPointF(109, 76), QPointF(109, 119))
    elif shape == "helmet":
        box(82, 117, 139, 23, accent)
        ellipse(79, 17, 146, 119)
        ellipse(108, 43, 87, 81, metal)
        ellipse(118, 53, 67, 61, glass)
        box(61, 63, 29, 32, metal)
        box(215, 63, 27, 32, metal)
        painter.setPen(QPen(metal, 4))
        painter.drawLine(QPointF(126, 67), QPointF(177, 102))
        painter.drawLine(QPointF(126, 102), QPointF(177, 67))
        for index in range(8):
            angle = index * math.pi / 4
            ellipse(148 + math.cos(angle) * 39, 80 + math.sin(angle) * 37, 5, 5, accent)
    elif shape == "pearl":
        shell = QPainterPath(QPointF(151, 121))
        shell.cubicTo(45, 119, 49, 17, 150, 19)
        shell.cubicTo(250, 17, 252, 119, 151, 121)
        brush(gradient)
        painter.drawPath(shell)
        painter.setPen(QPen(accent, 2))
        for index in range(7):
            painter.drawLine(QPointF(151, 121), QPointF(78 + index * 24, 42 + abs(index - 3) * 9))
        ellipse(72, 104, 159, 37, body)
        glow = QRadialGradient(143, 87, 33)
        glow.setColorAt(0, QColor("#ffffff"))
        glow.setColorAt(.6, QColor("#eef8ed"))
        glow.setColorAt(1, QColor("#9db7ce"))
        ellipse(120, 75, 62, 58, glow)
    elif shape == "compass":
        ellipse(140, 3, 22, 25, metal)
        ellipse(87, 21, 129, 129, metal)
        ellipse(96, 30, 111, 111, glass)
        for index in range(12):
            angle = index * math.pi / 6
            painter.setPen(QPen(metal, 2))
            painter.drawLine(QPointF(151 + math.cos(angle) * 45, 85 + math.sin(angle) * 45),
                             QPointF(151 + math.cos(angle) * 50, 85 + math.sin(angle) * 50))
        path([(151, 44), (165, 86), (151, 79), (137, 86)], QColor("#ef9b89"))
        path([(151, 126), (165, 86), (151, 92), (137, 86)], shine)
        ellipse(146, 80, 10, 10, metal)
    elif shape == "duck":
        path([(97, 91), (63, 71), (75, 114), (116, 127)])
        ellipse(77, 72, 145, 70)
        ellipse(163, 24, 67, 70)
        path([(216, 59), (251, 67), (217, 78)], accent)
        ellipse(101, 88, 65, 31, body.lighter(120))
        ellipse(207, 44, 8, 10, glass)
    elif shape == "kettle":
        painter.setPen(QPen(accent, 13))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(110, 10, 95, 98), 0, 180 * 16)
        path([(111, 88), (71, 52), (58, 59), (91, 118)])
        ellipse(101, 52, 114, 89)
        box(112, 48, 92, 13, accent)
        ellipse(145, 35, 25, 16, metal)
        box(121, 129, 77, 12, accent)
    elif shape == "magnet":
        magnet = QPainterPath(QPointF(99, 28))
        magnet.lineTo(99, 90)
        magnet.cubicTo(99, 158, 204, 158, 204, 90)
        magnet.lineTo(204, 28)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, 35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawPath(magnet)
        painter.setPen(QPen(body, 29, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawPath(magnet)
        box(83, 22, 32, 31, shine, 3)
        box(188, 22, 32, 31, shine, 3)
    elif shape == "robot":
        painter.setPen(QPen(accent, 3))
        for direction in (-1, 1):
            for index in range(4):
                painter.drawLine(QPointF(151 + direction * 55, 117), QPointF(151 + direction * (76 + index * 6), 126 + index * 4))
        ellipse(69, 57, 163, 83, accent)
        ellipse(69, 39, 163, 83)
        ellipse(138, 29, 34, 23, metal)
        box(114, 61, 80, 28, glass, 12)
        for horizontal in (132, 166):
            ellipse(horizontal, 69, 8, 8, shine)
        box(100, 113, 104, 7, glass, 3)
    else:
        raise ValueError("未实现的奇物造型：" + shape)
