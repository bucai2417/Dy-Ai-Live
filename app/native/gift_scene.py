from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen, QPolygonF, QRadialGradient

from .gift_spotlight import MESSAGES, donor_card


COLORS = {"tea": ("#f5cc8a", "#a3e3d2"), "video": ("#a3edbb", "#f5cf8c"),
          "flowers": ("#ffafca", "#c7a3ef"), "lollipop": ("#ffa3cf", "#a6e6ff"),
          "sunglasses": ("#88dfff", "#ccbbff"), "heart": ("#ff9bb6", "#ffe0b3"),
          "rocket": ("#afc4ff", "#8cf7f0"), "crown": ("#ffdb91", "#fff1c6"),
          "sparkles": ("#c2b0ff", "#8fe8df")}


def gift_view(state, now):
    show = state.get("gift_show") or {}
    extra = 0 if state.get("paused") or show.get("paused") or show.get("holding") else max(0, now - state.get("gift_show_at", now))
    return {**show, "elapsed": show.get("elapsed", 0) + extra}


def star(painter, center, radius, color):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    points = [QPointF(center.x() + math.cos(index * math.pi / 5 - math.pi / 2) * radius * (1 if index % 2 == 0 else .42),
                     center.y() + math.sin(index * math.pi / 5 - math.pi / 2) * radius * (1 if index % 2 == 0 else .42))
              for index in range(10)]
    painter.drawPolygon(QPolygonF(points))


def heart_path(size):
    path = QPainterPath(QPointF(0, size * .5))
    path.cubicTo(-size * 1.1, -size * .1, -size * .55, -size, 0, -size * .55)
    path.cubicTo(size * .55, -size, size * 1.1, -size * .1, 0, size * .5)
    return path


class GiftRenderer:
    def draw(self, painter, scene, state, bounds, now):
        view = gift_view(state, now)
        show = view.get("active")
        if not show:
            return
        age = min(show["duration"], view["elapsed"])
        effect = show["effect"]
        primary, secondary = COLORS.get(effect, COLORS["sparkles"])
        left, top, width, height = bounds
        height *= 960 / width
        painter.save()
        painter.translate(left, top)
        painter.scale(width / 960, width / 960)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, 960, height), 28, 28)
        painter.setClipPath(clip)
        painter.fillRect(QRectF(0, 0, 960, height), QColor(6, 15, 34, 170))
        fade = min(1, age / .6, max(0, (show["duration"] - age) / .65))
        painter.setOpacity(fade)
        glow = QRadialGradient(330, height * .48, min(450, height * .8))
        tint = QColor(primary)
        tint.setAlpha(85)
        glow.setColorAt(0, tint)
        glow.setColorAt(1, QColor(10, 20, 40, 0))
        painter.fillRect(QRectF(0, 0, 960, height), glow)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(primary), 1.6))
        painter.drawEllipse(QPointF(330, height - 61), 215 + math.sin(age * 2) * 5, 23)
        for index in range(28):
            phase = (age * (.06 + index % 3 * .015) + index * .173) % 1
            horizontal = 42 + (index * 127) % 870 + math.sin(age + index) * 18
            vertical = height - phase * height
            painter.setOpacity(fade * math.sin(phase * math.pi) * .7)
            star(painter, QPointF(horizontal, vertical), 3 + index % 5, primary if index % 2 else secondary)
        painter.setOpacity(fade)
        scene.text(painter, (42, 22, 876, 29), "礼物专属时刻", 18, secondary, align=Qt.AlignmentFlag.AlignCenter)
        scene.text(painter, (42, 55, 876, 43), show["label"], 33, primary, True, Qt.AlignmentFlag.AlignCenter)
        actor_height = min(720, max(260, height - 125))
        actor_width = actor_height * scene.avatar.width() / scene.avatar.height()
        bob = math.sin(age * 2) * 4
        if effect == "rocket":
            bob -= math.sin(min(1, max(0, (age - 2) / 7)) * math.pi) * 45
        actor = QRectF(324 - actor_width / 2, height - 60 - actor_height + bob, actor_width, actor_height)
        actor_state = {**state, "celebration_until": 0}
        if effect == "sunglasses":
            actor_state["costume"] = "cool" if age > 3.1 else "original"
        elif effect == "crown":
            actor_state["costume"] = "crown" if age > 3.1 else state.get("costume", "original")
        reaction = {"motion": "gift_" + effect, "started": now - age, "duration": show["duration"], "animation_id": show["id"]}
        anchors = scene.draw_character(painter, actor_state, actor, age, now, reaction)
        inverse, valid = painter.worldTransform().inverted()
        grip = inverse.map(anchors["hand_right"]) if anchors and valid else QPointF(actor.center().x() + actor.width() * .18, actor.top() + actor.height() * .62)
        if effect in ("flowers", "heart") and anchors and valid:
            other = inverse.map(anchors["hand_left"])
            grip = (grip + other) / 2
        painter.save()
        painter.translate(grip)
        painter.scale(actor_height / 1407, actor_height / 1407)
        painter.setOpacity(fade * min(1, max(0, (age - .5) / .6), max(0, (show["duration"] - age) / 1.2)))
        self._prop(painter, effect, age)
        painter.restore()
        video = state.get("video_image")
        if effect == "video" and video is not None and not video.isNull():
            video_height = min(actor_height, 450)
            video_width = min(440, video_height * video.width() / video.height())
            video_height = video_width * video.height() / video.width()
            painter.fillRect(QRectF(80, 105, 440, height - 150), QColor(9, 23, 39, 235))
            painter.drawImage(QRectF(324 - video_width / 2, height - 65 - video_height, video_width, video_height), video)
        card_top = max(112, min(height - 264, height * (.23 if scene.portrait else .30)))
        donor_card(painter, scene, state, show["user"], (548, card_top, 368, 178), primary, age,
                   f"{show['gift']}  × {show['count']}", show.get("tribute"))
        phase = "这一刻，你是主角" if age < 2 else MESSAGES.get(effect, MESSAGES["sparkles"])
        scene.elided(painter, (550, card_top + 186, 366, 28), phase, 18, secondary, True)
        if age >= show["duration"] - 2.5:
            scene.elided(painter, (550, card_top + 217, 366, 24), "心意收到啦 · 期待和你继续玩", 16, "#e6f4ff")
        elif view.get("pending"):
            scene.elided(painter, (550, card_top + 217, 366, 24), f"还有 {view['pending']} 份心意，依次呈现", 15, "#aac1d7")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 30))
        painter.drawRoundedRect(QRectF(550, card_top + 250, 356, 4), 2, 2)
        painter.setBrush(QColor(primary))
        painter.drawRoundedRect(QRectF(550, card_top + 250, 356 * min(1, age / show["duration"]), 4), 2, 2)
        painter.restore()


    def _prop(self, painter, effect, age):
        if effect in ("tea", "video"):
            self._tea(painter, age)
        elif effect == "flowers":
            self._flowers(painter, age)
        elif effect == "lollipop":
            self._lollipop(painter, age)
        elif effect == "heart":
            painter.translate(0, -90)
            painter.setPen(QPen(QColor("#ffdde6"), 5))
            painter.setBrush(QColor("#ef76a5"))
            painter.drawPath(heart_path(150 + math.sin(age * 5) * 7))
        elif effect == "rocket":
            self._rocket(painter, age)
        elif effect == "sunglasses":
            if age < 3.2:
                painter.setPen(QPen(QColor("#acdbf7"), 6))
                painter.setBrush(QColor("#151f38"))
                for horizontal in (-70, 12):
                    painter.drawRoundedRect(QRectF(horizontal, -42, 67, 40), 10, 10)
                painter.drawLine(QPointF(-3, -25), QPointF(12, -25))
            else:
                star(painter, QPointF(0, -20), 40, "#d1f4ff")
        else:
            star(painter, QPointF(0, -60), 100, "#ffdf9f" if effect == "crown" else "#c2b0ff")
            star(painter, QPointF(-12, -72), 44, "#fff8e7")

    def _tea(self, painter, age):
        painter.rotate(-14 * max(0, min(1, (age - 4) / 1.2)) if age < 7.4 else 0)
        painter.setPen(QPen(QColor("#f3d7ab"), 8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(34, -55, 40, 42))
        gradient = QLinearGradient(-45, -60, 48, 8)
        gradient.setColorAt(0, QColor("#fff4da"))
        gradient.setColorAt(1, QColor("#bfe2db"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#ebdec5"), 3))
        painter.drawRoundedRect(QRectF(-48, -68, 96, 78), 18, 18)
        painter.setBrush(QColor("#906442"))
        painter.drawEllipse(QRectF(-45, -75, 90, 20))
        painter.setPen(QPen(QColor(255, 243, 222, 160), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(3):
            offset = math.sin(age * 2.8 + index) * 9
            steam = QPainterPath(QPointF(-26 + index * 26, -88))
            steam.cubicTo(-37 + index * 26 + offset, -115, -10 + index * 26 - offset, -129, -25 + index * 26, -155)
            painter.drawPath(steam)

    def _flowers(self, painter, age):
        painter.setPen(QPen(QColor("#7ba97f"), 7))
        for horizontal in (-80, -40, 0, 40, 80):
            painter.drawLine(QPointF(0, 20), QPointF(horizontal, -145 - abs(horizontal) * .2))
        painter.setPen(QPen(QColor("#fce6e8"), 4))
        painter.setBrush(QColor("#d5c5eb"))
        painter.drawPolygon(QPolygonF([QPointF(-130, -135), QPointF(0, 55), QPointF(130, -135), QPointF(0, -98)]))
        for index, (horizontal, vertical) in enumerate(((-78, -160), (79, -162), (-40, -203), (38, -208), (0, -151), (0, -246))):
            painter.save()
            painter.translate(horizontal, vertical)
            painter.rotate(math.sin(age * 2 + index) * 5)
            painter.setPen(QPen(QColor("#ffe5ee"), 2))
            painter.setBrush(QColor(("#ef89aa", "#edb5d1", "#f6dcc8")[index % 3]))
            for petal in range(6):
                angle = petal * math.pi / 3
                painter.drawEllipse(QPointF(math.cos(angle) * 22, math.sin(angle) * 22), 27, 24)
            painter.setBrush(QColor("#ffe2a1"))
            painter.drawEllipse(QPointF(), 17, 17)
            painter.restore()
        painter.setPen(QPen(QColor("#fff0e5"), 5))
        painter.setBrush(QColor("#d7769f"))
        painter.drawPolygon(QPolygonF([QPointF(0, 6), QPointF(-50, -13), QPointF(-45, 34), QPointF(0, 6),
                                       QPointF(50, -13), QPointF(45, 34)]))

    def _lollipop(self, painter, age):
        painter.setPen(QPen(QColor("#f9ecdb"), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(0, 28), QPointF(0, -165))
        painter.translate(0, -250)
        painter.rotate(math.sin(age * 2) * 12)
        painter.setPen(QPen(QColor("#ffe6f1"), 6))
        painter.setBrush(QColor("#f69bc6"))
        painter.drawEllipse(QPointF(), 140, 140)
        spiral = QPainterPath(QPointF())
        for index in range(1, 121):
            angle = index / 120 * math.pi * 6
            radius = index / 120 * 128
            spiral.lineTo(math.cos(angle) * radius, math.sin(angle) * radius)
        painter.setPen(QPen(QColor("#fff1d4"), 19, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(spiral)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(-112, -112, 224, 224), 45 * 16, 70 * 16)

    def _rocket(self, painter, age):
        painter.translate(0, -90)
        painter.rotate(-15)
        painter.setPen(QPen(QColor("#d9eaff"), 4))
        painter.setBrush(QColor("#9faff1"))
        painter.drawPolygon(QPolygonF([QPointF(-40, 50), QPointF(-82, 105), QPointF(-25, 89), QPointF(25, 89), QPointF(82, 105), QPointF(40, 50)]))
        painter.setBrush(QColor("#edf5ff"))
        body = QPainterPath(QPointF(0, -150))
        body.cubicTo(-80, -75, -45, 60, -30, 85)
        body.lineTo(30, 85)
        body.cubicTo(45, 60, 80, -75, 0, -150)
        painter.drawPath(body)
        painter.setBrush(QColor("#83daea"))
        painter.drawEllipse(QPointF(0, -28), 26, 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffd08a"))
        painter.drawPolygon(QPolygonF([QPointF(-22, 87), QPointF(0, 150 + math.sin(age * 18) * 20), QPointF(22, 87)]))
