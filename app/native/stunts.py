from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from .effects import draw_particles
from .actions import pose



def stunt_age(state, now):
    active = (state.get("like_goal") or {}).get("active")
    if not active:
        return None
    age = float(active.get("elapsed", 0))
    if not state.get("paused") and not (state.get("like_goal") or {}).get("paused"):
        age += max(0, now - state.get("like_goal_at", now))
    if age >= active["duration"]:
        return None
    return active, max(0, age)


def _pen(painter, color, width):
    painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _line(painter, start, end):
    painter.drawLine(QPointF(*start), QPointF(*end))


def _actor(painter, scene, state, center, top, height, tilt=0, cropped=False, now=0):
    source = scene.stunt_rider if cropped else scene.avatar
    width = height * source.width() / source.height()
    painter.save()
    painter.translate(center, top + height * 0.55)
    painter.rotate(tilt)
    active = (state.get("like_goal") or {}).get("active") or {}
    if active.get("motion") == "swim" and now < state.get("reaction_until", 0):
        movement = pose(state["reaction"], now)
        painter.rotate(movement["tilt"])
        painter.scale(movement["scale_x"], movement["scale_y"])
    if (state["config"].get("stage") or {}).get("flip"):
        painter.scale(-1, 1)
    rect = QRectF(-width / 2, -height * 0.55, width, height)
    full_height = height / 0.79 if cropped else height
    full_rect = QRectF(rect.x(), rect.y(), width, full_height)
    active = (state.get("like_goal") or {}).get("active") or {}
    age = stunt_age(state, now)
    reaction = {"motion": active.get("motion"), "started": now - age[1], "duration": active.get("duration", 1),
                "animation_id": "stunt-" + str(active.get("id", active.get("motion")))} if age else {}
    if now < state.get("reaction_until", 0):
        reaction["overlay"] = state.get("reaction") or {}
    painter.setOpacity(painter.opacity() * float((state["config"].get("stage") or {}).get("opacity", 1)))
    anchors = scene.draw_character(painter, state, full_rect, state.get("animation_time", now), now, reaction, cropped=cropped)
    painter.restore()
    inverse, valid = painter.worldTransform().inverted()
    return {name: inverse.map(point) for name, point in anchors.items()} if anchors and valid else {}


def _bicycle(painter, scene, state, bounds, age, now):
    left, top, width, height = bounds
    center = left + width / 2 + math.sin(age * 0.85 - 0.5) * (width / 2 - 170)
    ground = top + height - 20
    wheel_y = ground - 47 + math.sin(age * 9) * 2
    rear, front = (center - 91, wheel_y), (center + 91, wheel_y)
    _pen(painter, "#54788b", 2)
    for index in range(9):
        start = left + (index * 100 - age * 150) % width
        _line(painter, (start, ground + 5), (min(left + width, start + 45), ground + 5))
    for wheel in (rear, front):
        _pen(painter, "#061526", 12)
        painter.drawEllipse(QPointF(*wheel), 46, 46)
        _pen(painter, "#91e9e3", 4)
        painter.drawEllipse(QPointF(*wheel), 43, 43)
        _pen(painter, "#638fa9", 1.6)
        for index in range(8):
            angle = age * 8 + index * math.pi / 4
            _line(painter, wheel, (wheel[0] + math.cos(angle) * 41, wheel[1] + math.sin(angle) * 41))
    crank = (center - 5, wheel_y)
    saddle = (center - 38, wheel_y - 76)
    stem = (center + 57, wheel_y - 77)
    _pen(painter, "#54e1d1", 8)
    for start, end in ((rear, saddle), (saddle, crank), (crank, rear), (crank, stem), (stem, saddle), (stem, front)):
        _line(painter, start, end)
    _pen(painter, "#edf9fc", 5)
    _line(painter, stem, (stem[0] - 7, stem[1] - 26))
    _line(painter, (stem[0] - 7, stem[1] - 26), (stem[0] + 17, stem[1] - 26))
    _pen(painter, "#f9cf88", 9)
    _line(painter, (saddle[0] - 15, saddle[1] - 5), (saddle[0] + 14, saddle[1] - 5))
    anchors = _actor(painter, scene, state, center - 34, saddle[1] - 220, 228, -5 + math.sin(age * 5) * 2, True, now)
    _pen(painter, "#edf9fc", 5)
    for name in ("hand_left", "hand_right"):
        if name in anchors:
            hand = anchors[name]
            _line(painter, (hand.x(), hand.y()), (stem[0] - 6, stem[1] - 25))
    angle = age * 7
    pedal = (crank[0] + math.cos(angle) * 23, crank[1] + math.sin(angle) * 23)
    _pen(painter, "#5d9ce5", 13)
    _line(painter, (center - 39, saddle[1] + 3), pedal)
    _pen(painter, "#e7f5ff", 4)
    _line(painter, crank, pedal)
    _pen(painter, "#f9cf88", 6)
    _line(painter, (pedal[0] - 9, pedal[1]), (pedal[0] + 9, pedal[1]))
    scene.text(painter, (center - 65, ground + 6, 160, 20), "鱼尾驱动 · 快乐加速", 12, "#a4d9e5")


def _web_swing(painter, scene, state, bounds, age, now):
    left, top, width, height = bounds
    anchor = (left + width / 2, top + 16)
    length = min(145, height * 0.34)
    angle = math.sin(max(0, age - 0.8) * 2.2) * 0.83
    center = anchor[0] + math.sin(angle) * length
    grip = (center, anchor[1] + math.cos(angle) * length)
    actor_height = min(225, height * 0.54)
    actor_center = center - 55
    actor_top = grip[1] + 5
    anchors = _actor(painter, scene, state, actor_center, actor_top, actor_height,
                     -math.degrees(angle) * 0.4, now=now)
    if "hand_right" in anchors:
        hand = anchors["hand_right"]
        grip = (hand.x(), hand.y())
    else:
        _pen(painter, "#4e91d5", 12)
        _line(painter, (actor_center + 47, actor_top + 117), (center + 18, grip[1] + 65))
        _line(painter, (center + 18, grip[1] + 65), grip)
    arrival = min(1, age / 0.65)
    target = (grip[0] + (anchor[0] - grip[0]) * arrival, grip[1] + (anchor[1] - grip[1]) * arrival)
    _pen(painter, "#b7eeff", 2.5)
    for offset in (-3, 0, 3):
        thread = QPainterPath(QPointF(grip[0] + offset, grip[1]))
        thread.quadTo(QPointF((grip[0] + target[0]) / 2 + offset * 2,
                              (grip[1] + target[1]) / 2), QPointF(*target))
        painter.drawPath(thread)
    if arrival >= 1:
        _pen(painter, "#b7eeff", 1.4)
        for index in range(8):
            ray = index * math.pi / 4
            _line(painter, anchor, (anchor[0] + math.cos(ray) * 18, anchor[1] + math.sin(ray) * 10))
        painter.drawEllipse(QPointF(*anchor), 11, 6)
    _pen(painter, "#76a6d0", 1.5)
    for index in range(3):
        start = actor_center - math.copysign(80 + index * 8, angle or 1)
        _line(painter, (start, grip[1] + 70 + index * 14),
              (start - math.copysign(25, angle or 1), grip[1] + 82 + index * 14))


def _rocket(painter, scene, state, bounds, age, now):
    left, top, width, height = bounds
    launch = min(1, max(0, (age - 1) / 2))
    center = left + width / 2 + math.sin(age * 2.7) * 70 * launch
    bottom = top + height - 55 - launch * min(115, max(0, height - 310)) + math.sin(age * 7) * 4
    painter.setPen(Qt.PenStyle.NoPen)
    for index in range(12):
        trail_age = (age * 1.7 + index / 12) % 1
        painter.setBrush(QColor(126, 192, 230, int(100 * (1 - trail_age))))
        painter.drawEllipse(QPointF(center + math.sin(index * 5.1) * trail_age * 70, bottom + trail_age * 90),
                            8 + trail_age * 16, 6 + trail_age * 11)
    flame = QPainterPath(QPointF(center - 18, bottom))
    flame.quadTo(QPointF(center - 35, bottom + 26), QPointF(center, bottom + 52 + math.sin(age * 30) * 9))
    flame.quadTo(QPointF(center + 35, bottom + 26), QPointF(center + 18, bottom))
    painter.setBrush(QColor("#ffb260"))
    painter.drawPath(flame)
    painter.setBrush(QColor("#fff3aa"))
    painter.drawEllipse(QRectF(center - 10, bottom - 2, 20, 35 + math.sin(age * 24) * 5))
    _actor(painter, scene, state, center, bottom - 245, 237, math.sin(age * 2.7) * 8, now=now)
    shell = QPainterPath(QPointF(center, bottom - 106))
    shell.cubicTo(QPointF(center - 40, bottom - 90), QPointF(center - 50, bottom - 32), QPointF(center - 25, bottom))
    shell.lineTo(QPointF(center + 25, bottom))
    shell.cubicTo(QPointF(center + 50, bottom - 32), QPointF(center + 40, bottom - 90), QPointF(center, bottom - 106))
    _pen(painter, "#9af4e7", 3)
    painter.setBrush(QColor("#315d8e"))
    painter.drawPath(shell)
    painter.setBrush(QColor("#b4f6f3"))
    painter.drawEllipse(QPointF(center, bottom - 52), 17, 17)
    _pen(painter, "#ffd891", 6)
    _line(painter, (center - 32, bottom - 34), (center - 57, bottom + 5))
    _line(painter, (center + 32, bottom - 34), (center + 57, bottom + 5))


def swim_path(bounds, age, duration=16):
    left, top, width, height = bounds
    phase = max(0, min(1, age / duration)) * math.tau * 2
    envelope = min(1, max(0, age / 1.1), max(0, (duration - age) / 1.1))
    envelope = envelope * envelope * (3 - 2 * envelope)
    size = min(225, height * .61) * (.86 + .12 * math.cos(phase))
    horizontal = max(0, width / 2 - size * .57 - 12)
    vertical = max(0, height / 2 - size * .57 - 10)
    center = left + width / 2 + math.sin(phase) * horizontal * envelope
    middle = top + height / 2 + math.sin(phase * 2) * vertical * envelope
    tilt = (math.tanh(math.cos(phase) * 2.4) * 67 + math.sin(phase * 2) * 7) * envelope
    return center, middle, size, tilt


def _swim(painter, scene, state, bounds, age, now):
    active = (state.get("like_goal") or {}).get("active") or {}
    duration = active.get("duration", 16)
    center, middle, size, tilt = swim_path(bounds, age, duration)
    effects = (state["config"].get("stage") or {}).get("native_effects", True)
    if effects:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(30, 0, -1):
            trail_age = max(0, age - index * .065)
            horizontal, vertical, _, _ = swim_path(bounds, trail_age, duration)
            alpha = int(80 * (1 - index / 31))
            painter.setPen(QPen(QColor(104, 240, 230, alpha), 2 + (1 - index / 31) * 8))
            painter.drawPoint(QPointF(horizontal, vertical + size * .22))
        for index in range(16):
            lag = ((age * .55 + index * .173) % 1) * 1.6
            horizontal, vertical, _, _ = swim_path(bounds, max(0, age - lag), duration)
            horizontal += math.sin(index * 7.3 + age) * (12 + lag * 16)
            vertical += size * .24 - lag * 35
            radius = 3 + (index % 4) * 1.8 + lag
            _pen(painter, QColor(157, 243, 255, int(150 * (1 - lag / 1.7))), 1.1)
            painter.drawEllipse(QPointF(horizontal, vertical), radius, radius)
            painter.setPen(QPen(QColor(242, 255, 255, 160), 1.5))
            painter.drawPoint(QPointF(horizontal - radius * .25, vertical - radius * .4))
    _actor(painter, scene, state, center, middle - size * .55, size, tilt, now=now)
    if effects and now < state.get("reaction_until", 0):
        draw_particles(painter, state["reaction"], (center - size * .55, middle - size * .55, size * 1.1, size), now)


def draw_stunt(painter, scene, state, bounds, active, age, now):
    if state.get("paused"):
        now = state.get("paused_at", now)
    painter.save()
    painter.setClipRect(QRectF(*bounds))
    opacity = min(1, age / 0.25, (active["duration"] - age) / 0.35)
    painter.setOpacity(max(0, opacity))
    draw = {"swim": _swim, "bicycle": _bicycle, "web_swing": _web_swing, "rocket": _rocket}.get(active["motion"])
    if draw:
        draw(painter, scene, state, bounds, age, now)
    painter.restore()
