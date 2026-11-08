from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen, QRadialGradient


MESSAGES = {"tea": "这杯热茶，暖暖地敬给你", "video": "这份小惊喜，专门为你准备",
            "flowers": "把你送来的浪漫，认真捧在手心", "lollipop": "今天的甜，有你的一份",
            "sunglasses": "你送的帅气，我替你闪耀全场", "heart": "隔着屏幕，也想给你一个拥抱",
            "rocket": "带着你的心意，一起去看星星", "crown": "这束聚光，为你而亮",
            "sparkles": "谢谢你，让这里多了一点星光"}


def donor_card(painter, scene, state, user, bounds, color, age, subtitle="", tribute=None, compact=False):
    left, top, width, height = bounds
    tribute = tribute or user.get("tribute") or {}
    painter.save()
    panel = QPainterPath()
    panel.addRoundedRect(QRectF(*bounds), 20, 20)
    painter.setClipPath(panel, Qt.ClipOperation.IntersectClip)
    gradient = QLinearGradient(left, top, left + width, top + height)
    gradient.setColorAt(0, QColor(34, 52, 77, 226))
    gradient.setColorAt(1, QColor(15, 29, 49, 180))
    painter.fillPath(panel, gradient)
    shimmer = QLinearGradient(left + (age % 4 / 4) * (width + 160) - 160, top,
                              left + (age % 4 / 4) * (width + 160), top + height)
    shimmer.setColorAt(0, QColor(255, 255, 255, 0))
    shimmer.setColorAt(.5, QColor(255, 255, 255, 18))
    shimmer.setColorAt(1, QColor(255, 255, 255, 0))
    painter.fillPath(panel, shimmer)
    painter.setPen(QPen(QColor(color), 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(panel)
    radius = 28 if compact else 36
    center = QPointF(left + radius + 17, top + (height / 2 if compact else 62))
    halo = QRadialGradient(center, radius + 15)
    tint = QColor(color)
    tint.setAlpha(105)
    halo.setColorAt(.6, tint)
    halo.setColorAt(1, QColor(0, 0, 0, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(halo)
    painter.drawEllipse(center, radius + 15, radius + 15)
    painter.setPen(QPen(QColor(color), 2))
    painter.setBrush(QColor("#243c57"))
    painter.drawEllipse(center, radius + 3, radius + 3)
    image = state.get("avatar_images", {}).get(user.get("avatar", ""))
    if image is not None and not image.isNull():
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(center, radius, radius)
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        painter.drawImage(QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2), image)
        painter.restore()
    else:
        scene.text(painter, (center.x() - radius, center.y() - radius, radius * 2, radius * 2),
                   (user.get("name") or "观众")[:1], 25, color, True, Qt.AlignmentFlag.AlignCenter)
    for index in range(3):
        angle = age * .8 + index * math.tau / 3
        point = center + QPointF(math.cos(angle) * (radius + 7), math.sin(angle) * (radius + 7))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(point, 2.3, 2.3)
    text_left = center.x() + radius + 18
    text_width = left + width - text_left - 16
    if compact:
        scene.elided(painter, (text_left, top + 9, text_width, 29), user.get("name") or "观众", 23, color, True)
        scene.elided(painter, (text_left, top + 41, text_width, 24), subtitle, 15, "#d5e6ee")
    else:
        badge = "今日初次心意" if tribute.get("first") else "谢谢你再次陪伴" if tribute.get("daily_count") else "为你点亮这一刻"
        scene.elided(painter, (text_left, top + 21, text_width, 23), badge, 15, "#c0d6e6")
        scene.elided(painter, (text_left, top + 49, text_width, 36), user.get("name") or "观众", 26, color, True)
        scene.elided(painter, (left + 20, top + 108, width - 40, 27), subtitle, 20, "#fff0d7", True)
        count = tribute.get("daily_count", 0)
        detail = f"截至此刻 · 今日已收到你 {count:,} 份心意" if count else "每一份心意，都值得认真回应"
        scene.elided(painter, (left + 20, top + 143, width - 40, 24), detail, 14, "#b1c9dc")
    painter.restore()
