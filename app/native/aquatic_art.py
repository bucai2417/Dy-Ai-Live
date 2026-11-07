from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen, QPolygonF


def silhouette(painter, kind, preset):
    if preset.get("category") == "item":
        from .item_art import draw_item
        draw_item(painter, kind, preset)
        return True
    shape = preset.get("shape", "fish")
    if shape == "fish":
        return False
    body, accent = QColor(preset["color"]), QColor(preset["accent"])
    gradient = QLinearGradient(0, 28, 0, 136)
    gradient.setColorAt(0, body.lighter(125))
    gradient.setColorAt(.55, body)
    gradient.setColorAt(1, accent)

    def polygon(points, brush=accent):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(brush)
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in points]))

    def eye(horizontal, vertical, radius=5):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f4faf6"))
        painter.drawEllipse(QPointF(horizontal, vertical), radius + 2, radius + 2)
        painter.setBrush(QColor("#182c43"))
        painter.drawEllipse(QPointF(horizontal + 1, vertical), radius, radius)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(horizontal + 2, vertical - 2), 1.5, 1.5)

    painter.setPen(Qt.PenStyle.NoPen)
    if shape in ("whale", "shark", "swordfish"):
        polygon([(79, 85), (12, 26), (33, 79), (13, 133)])
        polygon([(126, 59), (162, 12 if shape != "whale" else 34), (194, 63)])
        outline = QPainterPath(QPointF(58, 80))
        outline.cubicTo(107, 47, 132, 30, 220, 39)
        outline.cubicTo(274, 42, 287, 81, 266, 104)
        outline.cubicTo(234, 141, 125, 119, 58, 80)
        painter.setBrush(gradient)
        painter.drawPath(outline)
        painter.save()
        painter.setClipPath(outline)
        painter.setBrush(QColor("#d9ebe9"))
        painter.drawEllipse(QRectF(90, 95, 200, 42))
        if kind == "orca":
            painter.drawEllipse(QRectF(209, 55, 28, 16))
        elif kind == "whale_shark":
            for row in range(3):
                for column in range(7):
                    painter.drawEllipse(QPointF(113 + column * 19, 52 + row * 16), 2.5, 2.5)
        if shape == "whale":
            painter.setPen(QPen(accent, 1.6))
            for offset in range(5):
                painter.drawArc(QRectF(130 + offset * 13, 78, 90, 50), 205 * 16, 110 * 16)
        else:
            painter.setPen(QPen(accent.darker(125), 2.5))
            for horizontal in range(204, 224, 6):
                painter.drawLine(QPointF(horizontal, 71), QPointF(horizontal - 3, 93))
        painter.restore()
        polygon([(176, 94), (140, 143), (214, 111)])
        if kind == "hammerhead":
            polygon([(242, 48), (275, 40), (287, 112), (258, 117)], gradient)
            eye(277, 105, 4)
        elif shape == "swordfish":
            polygon([(258, 69), (299, 65), (265, 80)], body)
        elif kind == "dolphin":
            polygon([(249, 65), (297, 87), (255, 93)], body)
        elif kind == "narwhal":
            polygon([(258, 79), (299, 62), (266, 87)], QColor("#f5e6bd"))
        eye(249, 71, 4)
        painter.setPen(QPen(accent.darker(145), 2))
        painter.drawArc(QRectF(232, 76, 36, 22), 195 * 16, 110 * 16)
    elif shape in ("lobster", "crab"):
        painter.setPen(QPen(accent, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for index in range(4):
            horizontal = 99 + index * 22
            for direction in (-1, 1):
                path = QPainterPath(QPointF(horizontal, 79))
                path.lineTo(horizontal - 14, 80 + direction * 28)
                path.lineTo(horizontal + 7, 80 + direction * 55)
                painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        if shape == "lobster":
            polygon([(65, 79), (19, 51), (30, 81), (20, 110)], body)
            for index in range(6):
                painter.setBrush(gradient)
                painter.drawEllipse(QRectF(48 + index * 23, 60 - index * 3, 48, 39 + index * 6))
        else:
            painter.drawEllipse(QRectF(89, 39, 100, 82))
        for direction in (-1, 1):
            vertical = 80 + direction * 47
            painter.setPen(QPen(accent, 11, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(174, 80 + direction * 12), QPointF(222, vertical))
            polygon([(216, vertical), (242, vertical - 20), (278, vertical - 16),
                     (250, vertical), (280, vertical + 15), (241, vertical + 21)], gradient)
        painter.setPen(QPen(accent, 2.5))
        painter.drawArc(QRectF(183, 9, 93, 65), 10 * 16, 150 * 16)
        painter.drawArc(QRectF(183, 86, 93, 65), 200 * 16, 150 * 16)
        eye(188, 65, 4)
        eye(188, 94, 4)
    elif shape in ("octopus", "squid", "jellyfish"):
        for index in range(8):
            start = 115 + index * 10
            path = QPainterPath(QPointF(start, 76))
            path.cubicTo(start + (index - 3) * 8, 105, start + math.sin(index) * 25, 151,
                         start + (index - 3) * 18, 133)
            painter.setPen(QPen(accent, 5 if shape == "jellyfish" else 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        if shape == "squid":
            polygon([(90, 58), (155, 8), (217, 59), (177, 84), (124, 84)], gradient)
        else:
            path = QPainterPath(QPointF(94, 84))
            path.cubicTo(80, -10, 228, -10, 216, 84)
            path.quadTo(155, 107, 94, 84)
            painter.drawPath(path)
        eye(140, 61)
        eye(175, 61)
        painter.setPen(QPen(body.lighter(140), 2))
        painter.drawArc(QRectF(112, 25, 48, 51), 65 * 16, 85 * 16)
    elif shape == "ray":
        polygon([(42, 18), (135, 56), (211, 64), (264, 81), (211, 97),
                 (137, 104), (42, 146), (91, 82)], gradient)
        painter.setPen(QPen(accent, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(110, 82), QPointF(9, 98))
        eye(218, 73, 3)
        painter.setPen(QPen(body.lighter(135), 2))
        painter.drawLine(QPointF(85, 43), QPointF(170, 78))
        painter.drawLine(QPointF(84, 120), QPointF(169, 88))
    elif shape == "turtle":
        for horizontal, vertical, rotation in ((99, 43, -28), (187, 43, 28), (99, 117, 28), (187, 117, -28)):
            painter.save()
            painter.translate(horizontal, vertical)
            painter.rotate(rotation)
            painter.setBrush(body)
            painter.drawEllipse(QRectF(-30, -14, 60, 28))
            painter.restore()
        polygon([(89, 80), (40, 90), (68, 74)], accent)
        painter.setBrush(body)
        painter.drawEllipse(QRectF(199, 57, 61, 44))
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(70, 28, 143, 105))
        painter.setPen(QPen(accent, 3))
        for horizontal, vertical in ((114, 60), (155, 60), (134, 92), (176, 92)):
            painter.drawPolygon(QPolygonF([QPointF(horizontal + 24 * math.cos(index * math.pi / 3),
                                                   vertical + 20 * math.sin(index * math.pi / 3)) for index in range(6)]))
        eye(241, 69, 4)
    elif shape == "eel":
        path = QPainterPath(QPointF(25, 104))
        path.cubicTo(85, 10, 119, 160, 165, 88)
        path.cubicTo(202, 29, 243, 46, 266, 70)
        painter.setPen(QPen(accent, 29, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(path)
        painter.setPen(QPen(body, 21, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(path)
        eye(257, 64, 4)
    elif shape == "seahorse":
        path = QPainterPath(QPointF(153, 55))
        path.cubicTo(117, 90, 209, 130, 148, 139)
        path.cubicTo(110, 144, 108, 104, 141, 112)
        painter.setPen(QPen(accent, 22, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(128, 18, 55, 53))
        polygon([(169, 39), (213, 51), (210, 65), (167, 59)], body)
        polygon([(138, 63), (105, 64), (129, 101)], accent)
        eye(163, 35, 4)
    elif shape == "puffer":
        for index in range(16):
            angle = index * math.pi / 8
            center = (149 + math.cos(angle) * 59, 80 + math.sin(angle) * 59)
            polygon([(center[0] - 6, center[1] - 6), (149 + math.cos(angle) * 73, 80 + math.sin(angle) * 73),
                     (center[0] + 6, center[1] + 6)], accent)
        polygon([(95, 80), (49, 49), (51, 109)], accent)
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(88, 20, 130, 121))
        painter.setBrush(accent)
        for index in range(12):
            painter.drawEllipse(QPointF(119 + index % 4 * 19, 44 + index // 4 * 27), 3, 3)
        eye(193, 67, 7)
    elif shape in ("angler", "lionfish", "flatfish", "flying", "catfish"):
        polygon([(97, 81), (32, 41), (43, 81), (32, 125)])
        if shape == "lionfish":
            for index in range(9):
                horizontal = 84 + index * 15
                polygon([(horizontal, 79), (horizontal - 15, 6 + index % 2 * 12), (horizontal + 21, 72)])
                polygon([(horizontal, 89), (horizontal - 9, 151 - index % 2 * 10), (horizontal + 22, 99)])
        elif shape == "flying":
            polygon([(187, 79), (67, 7), (71, 57), (121, 103)], body.lighter(135))
            polygon([(187, 89), (67, 155), (91, 110)], body.lighter(135))
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(76, 46 if shape != "angler" else 39, 184, 77 if shape != "angler" else 99))
        if shape == "angler":
            lure = QPainterPath(QPointF(204, 47))
            lure.cubicTo(184, -2, 251, -1, 253, 26)
            painter.setPen(QPen(accent, 4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(lure)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 239, 162, 55))
            painter.drawEllipse(QPointF(253, 29), 18, 18)
            painter.setBrush(QColor("#fff1a8"))
            painter.drawEllipse(QPointF(253, 29), 8, 8)
            painter.setBrush(accent.darker(170))
            painter.drawEllipse(QRectF(220, 92, 37, 28))
            for horizontal in range(225, 250, 8):
                polygon([(horizontal, 94), (horizontal + 4, 103), (horizontal + 7, 94)], QColor("#f8ecce"))
        elif shape in ("lionfish", "flatfish"):
            painter.setPen(QPen(accent, 4))
            for index in range(7):
                horizontal = 100 + index * 18
                if shape == "lionfish":
                    painter.drawLine(QPointF(horizontal, 54), QPointF(horizontal + 12, 115))
                else:
                    painter.drawEllipse(QPointF(horizontal, 71 + index % 2 * 27), 3, 3)
        elif shape == "catfish":
            painter.setPen(QPen(accent, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for direction in (-1, 1):
                whisker = QPainterPath(QPointF(252, 88))
                whisker.quadTo(296, 83 + direction * 35, 276, 83 + direction * 57)
                painter.drawPath(whisker)
        eye(234, 71, 5)
        if shape == "flatfish":
            eye(211, 60, 4)
    elif shape == "sawfish":
        polygon([(69, 80), (15, 36), (31, 81), (16, 124)])
        polygon([(98, 78), (116, 17), (174, 66)], gradient)
        polygon([(97, 85), (115, 144), (174, 100)], gradient)
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(65, 58, 147, 45))
        polygon([(194, 72), (291, 76), (291, 84), (194, 91)], body)
        for horizontal in range(215, 285, 10):
            polygon([(horizontal, 77), (horizontal + 2, 64), (horizontal + 6, 77)], accent)
            polygon([(horizontal, 84), (horizontal + 2, 97), (horizontal + 6, 84)], accent)
        eye(183, 69, 3)
    elif shape == "nautilus":
        painter.setPen(QPen(accent, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for index in range(7):
            tentacle = QPainterPath(QPointF(198, 99))
            tentacle.quadTo(248, 66 + index * 11, 269, 85 + index * 8)
            painter.drawPath(tentacle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QRectF(74, 19, 139, 122))
        spiral = QPainterPath(QPointF(142, 79))
        for index in range(1, 101):
            angle = index * .16
            radius = index * .52
            spiral.lineTo(142 + math.cos(angle) * radius, 79 + math.sin(angle) * radius)
        painter.setPen(QPen(accent, 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(spiral)
        eye(211, 96, 5)
    else:
        return False
    return True
