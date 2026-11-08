from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QRadialGradient

from .diving_scene import draw_splash, smooth
from .gift_spotlight import donor_card


def draw_lantern(painter, dock, darkness, clock):
    if darkness <= .01:
        return
    painter.save()
    painter.setOpacity(darkness)
    glow = QRadialGradient(53, dock - 58, 115)
    glow.setColorAt(0, QColor(255, 207, 104, 125))
    glow.setColorAt(1, QColor(255, 207, 104, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(53, dock - 58), 115, 115)
    painter.setPen(QPen(QColor("#d6c09c"), 4))
    painter.drawLine(QPointF(28, dock), QPointF(28, dock - 111))
    painter.drawLine(QPointF(28, dock - 111), QPointF(53, dock - 111))
    painter.drawLine(QPointF(53, dock - 111), QPointF(53, dock - 81))
    painter.setBrush(QColor("#ffd78a"))
    painter.drawRoundedRect(QRectF(40, dock - 81, 26, 38), 7, 7)
    painter.setPen(QPen(QColor(255, 210, 134, 65), 2))
    for index in range(8):
        width = 12 + index * 4 + math.sin(clock * 1.3 + index) * 5
        painter.drawLine(QPointF(53 - width, dock + 55 + index * 9), QPointF(53 + width, dock + 55 + index * 9))
    painter.restore()


def draw_mesh(painter, center, radius, depth):
    painter.save()
    oval = QRectF(center.x() - radius, center.y() - depth, radius * 2, depth * 2)
    rim = QPainterPath()
    rim.addEllipse(oval)
    painter.setPen(QPen(QColor("#efd8a2"), 2.5))
    painter.setBrush(QColor(151, 218, 206, 32))
    painter.drawPath(rim)
    painter.setClipPath(rim, Qt.ClipOperation.IntersectClip)
    painter.setPen(QPen(QColor(210, 242, 221, 145), 1.2))
    for index in range(-12, 13):
        offset = index * radius / 6
        for direction in (-1, 1):
            painter.drawLine(QPointF(center.x() + offset - radius, center.y() - direction * depth),
                             QPointF(center.x() + offset + radius, center.y() + direction * depth))
    painter.restore()


def draw_fishing_action(painter, scene, state, game, height, now, show_actor, renderer):
    age = game["phase_elapsed"]
    feeding = (game.get("interruption") or {}).get("action") == "feed"
    result = game["phase"] == "net_caught"
    dock, surface = height * .46, height * .52
    character_height = min(420, height * .48)
    character_width = character_height * scene.avatar.width() / scene.avatar.height()
    rise = smooth(age / 1.7) if not result else 1
    center = QPointF(177 + 62 * rise, max(character_height / 2 + 28, dock - 28 - character_height * (.18 + .2 * rise)))
    motion = "fishing_feed" if feeding else "happy" if result else "net_windup" if age < 2 else "net_cast" if age < 4.5 else "net_haul"
    anchors = None
    if show_actor:
        reaction = {"motion": motion, "started": now - age - 2, "duration": 86400,
                    "animation_id": (game.get("net") or {}).get("id", game.get("round_id", "")) + motion}
        anchors = scene.draw_character(painter, state, QRectF(center.x() - character_width / 2, center.y() - character_height / 2,
                                                               character_width, character_height), game["elapsed"], now, reaction)
    grip = QPointF(center.x() + 35, center.y())
    if anchors and "hand_right" in anchors:
        inverse, valid = painter.worldTransform().inverted()
        if valid:
            grip = inverse.map(anchors["hand_right"])
    painter.setPen(QPen(QColor("#e4c685"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(78, dock - 12), QPointF(298, dock - 15))
    painter.setBrush(QColor("#768d8c"))
    painter.setPen(QPen(QColor("#d9d5bb"), 2))
    painter.drawRoundedRect(QRectF(94, dock - 46, 35, 34), 6, 6)
    if feeding:
        for index in range(16):
            travel = max(0, min(1, (age - .8 - index * .09) / 1.3))
            if not 0 < travel < 1:
                continue
            destination = 470 + index % 5 * 24
            horizontal = grip.x() + (destination - grip.x()) * travel
            vertical = grip.y() + (surface - grip.y()) * travel - math.sin(travel * math.pi) * 85
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffd79c"))
            painter.drawEllipse(QPointF(horizontal, vertical), 3.5, 3.5)
        draw_splash(painter, 515, surface, age - 2.2, .35)
        return
    if result:
        draw_net_result(painter, scene, state, game, height, renderer)
        return
    if age < 2:
        net_center = QPointF(grip.x() + 20, grip.y() + 24)
        radius, depth = 28, 44
    elif age < 4.5:
        travel = (age - 2) / 2.5
        net_center = QPointF(grip.x() + (635 - grip.x()) * travel,
                            grip.y() + (surface - grip.y()) * travel - math.sin(travel * math.pi) * 140)
        radius, depth = 28 + 142 * smooth(travel), 44 + 10 * travel
    elif age < 6.5:
        sinking = smooth((age - 4.5) / 2)
        net_center = QPointF(635, surface + sinking * min(100, height * .12))
        radius, depth = 170 - sinking * 25, 54 + sinking * 22
    else:
        haul = smooth((age - 6.5) / 3.5)
        net_center = QPointF(635 - haul * 160, surface + min(100, height * .12) * (1 - haul) - haul * 55)
        radius, depth = 145 - haul * 65, 76 + haul * 8
    rope = QPainterPath(grip)
    rope.quadTo((grip.x() + net_center.x()) / 2, (grip.y() + net_center.y()) / 2 + 35, net_center.x(), net_center.y() - depth)
    painter.setPen(QPen(QColor("#ead6aa"), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(rope)
    if age >= 6.5:
        for index, fish in enumerate((game.get("net") or {}).get("fish", [])):
            renderer._fish(painter, fish["kind"], net_center.x() + math.sin(index * 2.1) * radius * .45,
                           net_center.y() + (index % 3 - 1) * 17, 62, index % 2 == 0)
    draw_mesh(painter, net_center, radius, depth)
    draw_splash(painter, 635, surface, age - 4.5, 1.1, "fan")
    draw_splash(painter, 540, surface, age - 8.0, .5)
    haul = game.get("net") or {}
    donor_card(painter, scene, state, haul.get("user") or {}, (350, 82, 560, 78), "#ffe0a0", age,
               f"{haul.get('gift', '礼物')} × {haul.get('count', 1)} · 专门为你撒一网好运", compact=True)


def draw_net_result(painter, scene, state, game, height, renderer):
    haul = game.get("net") or {}
    catches = haul.get("fish") or []
    age = game["phase_elapsed"]
    top = max(82, min(height - 318, height * .22))
    painter.save()
    painter.setPen(QPen(QColor(255, 220, 151, 100), 1.5))
    painter.setBrush(QColor(9, 30, 47, 178))
    painter.drawRoundedRect(QRectF(346, top, 568, 308), 22, 22)
    for index in range(34):
        progress = (age * .24 + index / 34) % 1
        horizontal = 354 + (index * 79) % 545
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(("#ffe0a0", "#87ecdc", "#ccb6ff")[index % 3]))
        painter.drawEllipse(QPointF(horizontal, top - 5 + progress * 265), 2.5, 4)
    donor_card(painter, scene, state, haul.get("user") or {}, (358, top + 10, 544, 78), "#ffe0a0", age,
               "恭喜满载！这一网好运全部属于你", compact=True)
    slot = 520 / max(1, len(catches))
    for index, fish in enumerate(catches):
        horizontal = 370 + index * slot
        width = min(148, slot - 8)
        painter.drawImage(QRectF(horizontal + (slot - width) / 2, top + 102, width, 72), renderer.fish_images[fish["kind"]])
        scene.elided(painter, (horizontal, top + 180, slot - 4, 24), fish["name"], 16, "#edf6e5", True)
    scene.outlined(painter, (366, top + 213, 528, 31), f"满载 {len(catches)} 条 · +{haul.get('total', 0):,} 图鉴分", 24, "#9af0d8", True, "center")
    scene.outlined(painter, (366, top + 249, 528, 24), "专属鱼获已记入你的今日榜单", 16, "#c4eee2", align="center")
    hint = "未提供价值，按礼物数量估算 · 玩法积分" if haul.get("estimated") else "礼物价值换算 · 玩法积分，不可兑换"
    scene.outlined(painter, (366, top + 278, 528, 22), hint, 13, "#b4cad7", align="center")
    painter.restore()
