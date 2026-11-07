from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen


def smooth(value):
    value = max(0, min(1, value))
    return value * value * (3 - 2 * value)


def dive_position(age, height, character_height, style, laps):
    dock, surface = height * .46, height * .52
    seated, standing = dock - 28 - character_height * .18, dock - character_height * .40
    water_depth = max(24, min(95, (height - surface - 105) * .42))
    water_center = surface + water_depth
    if age < 2:
        return 177, seated + (standing - seated) * smooth(age / 2), 0, "dive_put"
    if age < 4:
        progress = smooth((age - 2) / 2)
        return 177 + 128 * progress, standing - abs(math.sin(progress * math.pi * 4)) * 8, 0, "travel"
    if age < 6:
        progress = (age - 4) / 2
        rotation = {"tuck": 720, "pike": 360, "twist": -540, "cannonball": 360}[style]
        return (305 + 275 * progress, standing + (water_center - standing) * progress
                - math.sin(progress * math.pi) * min(180, height * .13), rotation * smooth(progress), "dive_" + style)
    if age < 7:
        return 580, water_center, 0, "dive_tuck"
    if age < 18:
        angle = (age - 7) / 11 * math.tau * laps
        depth = min(65, water_depth * .65)
        horizontal, vertical = 580 + math.sin(angle) * 220, water_center + (1 - math.cos(angle)) * depth / 2
        rotation = math.degrees(math.atan2(math.sin(angle) * depth / 2, math.cos(angle) * 220)) + 90
        rotation *= smooth((age - 7) / .6) * smooth((18 - age) / .6)
        return horizontal, vertical, rotation, "swim"
    if age < 21:
        progress = (age - 18) / 3
        return (580 - 403 * smooth(progress), water_center + (standing - water_center) * progress
                - math.sin(progress * math.pi) * min(210, height * .15), -360 * smooth(progress), "dive_pike")
    return 177, standing + (seated - standing) * smooth((age - 21) / 3), 0, "dive_put"


def draw_splash(painter, horizontal, surface, age, strength, mode="crown"):
    if not 0 <= age < 2.4:
        return
    progress = age / 2.4
    painter.save()
    painter.setOpacity(1 - progress)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index in range(3):
        radius = (32 + progress * 145 + index * 18) * strength
        painter.setPen(QPen(QColor("#c1f8ec"), 3 - index * .5))
        painter.drawEllipse(QPointF(horizontal, surface + index * 4), radius, radius * .19)
    painter.setPen(Qt.PenStyle.NoPen)
    crest = math.sin(progress * math.pi)
    painter.setBrush(QColor(161, 240, 238, 105))
    for index in range(7):
        spread = (index - 3) * (22 if mode == "fan" else 10 if mode == "jet" else 16) * strength
        lift = (145 if mode == "jet" else 65 if mode == "fan" else 95) * strength * crest
        lift *= 1 - abs(index - 3) * .12
        petal = QPainterPath(QPointF(horizontal + spread * .25, surface))
        petal.quadTo(horizontal + spread * .5, surface - lift, horizontal + spread, surface - lift * .9)
        petal.quadTo(horizontal + spread + 10, surface - lift * .35, horizontal + spread * .35 + 13, surface)
        painter.drawPath(petal)
    for index in range(30):
        spread = (index / 29 - .5) * 230 * strength
        lift = (45 + index % 7 * 16) * strength
        vertical = surface - math.sin(progress * math.pi) * lift
        painter.setBrush(QColor("#d9fcf3" if index % 2 else "#8ee5e9"))
        painter.drawEllipse(QPointF(horizontal + spread * progress, vertical), 3 + index % 4, 6 + index % 7)
    painter.restore()


def draw_dive(painter, scene, state, game, height, now, show_actor):
    age, settings = game["phase_elapsed"], game.get("dive") or {}
    style, laps = settings.get("style", "tuck"), settings.get("laps", 3)
    dock, surface = height * .46, height * .52
    character_height = min(420, height * .48)
    scale = 1 - .36 * smooth((age - 5) / 2) * smooth((21 - age) / 3)
    horizontal, vertical, rotation, motion = dive_position(age, height, character_height, style, laps)
    character_width = character_height * scene.avatar.width() / scene.avatar.height()
    if show_actor:
        painter.save()
        painter.translate(horizontal, vertical)
        painter.rotate(rotation)
        painter.scale(scale, scale)
        painter.setOpacity(.86 if 6 <= age < 18 else 1)
        reaction = {"motion": motion, "duration": 86400, "started": now - age - 2,
                    "animation_id": game.get("round_id", "") + ":dive:" + motion}
        anchors = scene.draw_character(painter, state, QRectF(-character_width / 2, -character_height / 2,
                                                               character_width, character_height), age, now, reaction)
        painter.restore()
        grip = QPointF(232, dock - 100)
        if anchors and "hand_right" in anchors:
            inverse, valid = painter.worldTransform().inverted()
            if valid:
                grip = inverse.map(anchors["hand_right"])
        parked = QPointF(90, dock - 12)
        resting = smooth(age / 2) if age < 2 else smooth((24 - age) / 3) if age > 21 else 1
        rod_grip = grip * (1 - resting) + parked * resting
        tip = QPointF(592, height * .16) * (1 - resting) + QPointF(298, dock - 14) * resting
        painter.setPen(QPen(QColor("#ddbd83"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(rod_grip, tip)
        painter.setPen(QPen(QColor("#314959"), 8))
        painter.drawLine(rod_grip, rod_grip + QPointF(-18, 4))
        painter.setPen(QPen(QColor("#ddbd83"), 3))
        painter.drawEllipse(rod_grip + QPointF(10, 10), 8, 8)
    if 7 <= age < 18:
        painter.save()
        painter.setClipRect(QRectF(0, surface, 960, height - surface), Qt.ClipOperation.IntersectClip)
        painter.setPen(QPen(QColor(177, 244, 236, 95), 2))
        for index in range(12):
            drift = (age * 1.7 + index * .19) % 1
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(horizontal - math.cos(math.radians(rotation - 90)) * (30 + drift * 90),
                                         vertical - drift * 55 + index % 3 * 9), 2 + drift * 5, 2 + drift * 5)
        painter.restore()
    strength = settings.get("splash", 1)
    draw_splash(painter, 580, surface, age - 5.8, strength, settings.get("splash_kind", "crown"))
    draw_splash(painter, 580, surface, age - 18, strength * .65)
