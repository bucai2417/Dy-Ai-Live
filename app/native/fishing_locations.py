from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF

from ..fishing_autonomy import SPOTS
from .diving_scene import smooth


def draw_location(painter, height, spot, night):
    surface = height * .52
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    if spot == "reeds":
        painter.setBrush(QColor("#223e42" if night else "#688f6a"))
        bank = QPainterPath(QPointF(690, surface + 12))
        bank.quadTo(805, surface - 64, 960, surface - 28)
        bank.lineTo(960, surface + 40)
        bank.closeSubpath()
        painter.drawPath(bank)
        for index in range(24):
            horizontal = 735 + index * 10
            stalk = 50 + index % 5 * 13
            painter.setPen(QPen(QColor("#607d54" if night else "#abc486"), 2))
            painter.drawLine(QPointF(horizontal, surface + 4), QPointF(horizontal - 8, surface - stalk))
            painter.setPen(QPen(QColor("#b6a17b" if night else "#e8d1a0"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(horizontal - 8, surface - stalk), QPointF(horizontal - 11, surface - stalk - 19))
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(7):
            horizontal = 375 + index * 82
            vertical = surface + 40 + index % 3 * 14
            painter.setBrush(QColor("#3c6154" if night else "#7cba85"))
            painter.drawPie(QRectF(horizontal, vertical, 38, 13), 20 * 16, 315 * 16)
            if index % 2:
                painter.setBrush(QColor("#dfbac9" if night else "#ffd6e3"))
                painter.drawEllipse(QPointF(horizontal + 17, vertical + 2), 6, 4)
    elif spot == "reef":
        for index in range(8):
            horizontal = 650 + index * 47
            top = surface - 15 - index % 3 * 18
            painter.setBrush(QColor("#354858" if night else "#6c8b96"))
            painter.drawPolygon(QPolygonF([QPointF(horizontal - 25, surface + 23), QPointF(horizontal - 14, top),
                                           QPointF(horizontal + 15, top - 9), QPointF(horizontal + 42, surface + 24)]))
        base = surface - 27
        painter.setBrush(QColor("#9eaec0" if night else "#e9ddd2"))
        painter.drawPolygon(QPolygonF([QPointF(806, base), QPointF(817, base - 143),
                                       QPointF(846, base - 143), QPointF(859, base)]))
        painter.setBrush(QColor("#9e6766" if night else "#c58177"))
        painter.drawRect(QRectF(816, base - 96, 33, 21))
        painter.setBrush(QColor("#ffe4aa"))
        painter.drawRoundedRect(QRectF(810, base - 155, 43, 24), 4, 4)
        painter.setBrush(QColor("#546c80"))
        painter.drawPolygon(QPolygonF([QPointF(805, base - 155), QPointF(832, base - 174), QPointF(859, base - 155)]))
        painter.setBrush(QColor(255, 224, 151, 36 if night else 12))
        painter.drawPolygon(QPolygonF([QPointF(832, base - 143), QPointF(450, base - 225), QPointF(450, base - 72)]))
    painter.restore()


def draw_relocation(painter, scene, state, game, height, now, show_actor):
    age = game["phase_elapsed"]
    moving = game.get("relocation") or {}
    target = SPOTS.get(moving.get("to"), SPOTS["cloud"])["name"]
    dock = height * .46
    character_height = min(390, height * .44)
    character_width = character_height * scene.avatar.width() / scene.avatar.height()
    if age < 2:
        horizontal = 177
        lift = smooth(age / 2)
        motion = "dive_put"
    elif age < 5:
        horizontal = 177 + 530 * smooth((age - 2) / 3)
        lift, motion = 1, "travel"
    elif age < 8:
        horizontal = -115 + 292 * smooth((age - 5) / 3)
        lift, motion = 1, "travel"
    else:
        horizontal = 177
        lift, motion = 1 - smooth((age - 8) / 2), "dive_put"
    bottom = dock + 12 + (1 - lift) * character_height * .22
    center_y = max(character_height / 2 + 26, bottom - character_height / 2)
    center_y -= abs(math.sin(age * 7)) * 4 if motion == "travel" else 0
    painter.save()
    painter.setPen(QPen(QColor("#8b765f"), 2))
    painter.setBrush(QColor("#b69e7e"))
    painter.drawPolygon(QPolygonF([QPointF(275, dock - 8), QPointF(960, dock - 8), QPointF(960, dock + 20), QPointF(325, dock + 20)]))
    for position in range(310, 960, 38):
        painter.drawLine(QPointF(position, dock - 7), QPointF(position + 25, dock + 19))
    anchors = None
    if show_actor:
        reaction = {"motion": motion, "started": now - age - 2, "duration": 86400,
                    "animation_id": moving.get("id", "") + motion}
        anchors = scene.draw_character(painter, state, QRectF(horizontal - character_width / 2, center_y - character_height / 2,
                                                               character_width, character_height), game["elapsed"], now, reaction)
    grip = QPointF(horizontal + 30, center_y + 25)
    if anchors and "hand_right" in anchors:
        inverse, valid = painter.worldTransform().inverted()
        if valid:
            grip = inverse.map(anchors["hand_right"])
    painter.setPen(QPen(QColor("#edce8f"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(grip, QPointF(grip.x() + 50, grip.y() - character_height * .50))
    bucket_x = horizontal - character_width * .22
    painter.setPen(QPen(QColor("#d9e6da"), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(bucket_x, bottom - 62, 30, 27), 0, 180 * 16)
    painter.setBrush(QColor("#6d9d9b"))
    painter.drawRoundedRect(QRectF(bucket_x - 4, bottom - 49, 38, 32), 5, 5)
    scene.elided(painter, (358, dock + 48, 550, 40), "前往 " + target, 27, "#ffe0a0", True)
    scene.elided(painter, (358, dock + 90, 550, 30), moving.get("reason", "换个地方，重新抛竿"), 17, "#d5ece3")
    if 4.4 < age < 5.6:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(199, 224, 222, round(225 * math.sin((age - 4.4) / 1.2 * math.pi))))
        painter.drawRect(QRectF(0, 0, 960, height))
    painter.restore()
