from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath, QPen


def game_view(state, now):
    game = state.get("game") or {}
    extra = 0 if state.get("paused") or game.get("paused") else max(0, now - state.get("game_at", now))
    return {**game, "elapsed": game.get("elapsed", 0) + extra,
            "remaining": max(0, game.get("remaining", 0) - extra)}


def draw_strokes(painter, strokes, canvas, progress):
    painter.save()
    painter.setClipRect(canvas)
    painter.translate(canvas.left(), canvas.top())
    painter.scale(canvas.width() / 100, canvas.height() / 100)
    for index, stroke in enumerate(strokes):
        portion = min(1, max(0, progress * len(strokes) - index))
        if portion <= 0:
            break
        points = [QPointF(*point) for point in stroke["points"]]
        painter.setPen(QPen(QColor(stroke["color"]), 1.15, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(stroke["fill"]) if stroke["fill"] != "none" and portion == 1 else Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if stroke["shape"] == "line":
            path.moveTo(points[0])
            segments = portion * (len(points) - 1)
            for point_index in range(1, len(points)):
                fraction = min(1, max(0, segments - point_index + 1))
                if fraction <= 0:
                    break
                path.lineTo(points[point_index - 1] + (points[point_index] - points[point_index - 1]) * fraction)
        else:
            rect = QRectF(points[0], points[1]).normalized()
            if stroke["shape"] == "ellipse":
                path.arcMoveTo(rect, 90)
                path.arcTo(rect, 90, -360 * portion)
                if portion == 1:
                    path.closeSubpath()
            else:
                corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft(), rect.topLeft()]
                path.moveTo(corners[0])
                for side in range(4):
                    fraction = min(1, max(0, portion * 4 - side))
                    if fraction > 0:
                        path.lineTo(corners[side] + (corners[side + 1] - corners[side]) * fraction)
                if portion == 1:
                    path.closeSubpath()
        painter.drawPath(path)
    painter.restore()


class GameRenderer:
    def draw(self, painter, scene, state, bounds, now):
        game = game_view(state, now)
        if not game.get("active"):
            return
        left, top, width, height = bounds
        height *= 960 / width
        portrait = height > 720
        age = game["elapsed"]
        question = game.get("question") or {}
        phase = game.get("phase", "preparing")
        painter.save()
        painter.translate(left, top)
        painter.scale(width / 960, width / 960)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, 960, height), 28, 28)
        painter.setClipPath(clip)
        gradient = QLinearGradient(0, 0, 960, height)
        gradient.setColorAt(0, QColor("#113b49"))
        gradient.setColorAt(1, QColor("#15253d"))
        painter.fillRect(QRectF(0, 0, 960, height), gradient)
        painter.setPen(QPen(QColor(129, 217, 205, 18), 1))
        for column in range(20, 960, 36):
            for row in range(90, int(height), 36):
                painter.drawPoint(QPointF(column, row))
        scene.text(painter, (30, 18, 520, 46), game.get("label", "弹幕小游戏"), 32, "#f4f4de", True)
        scene.text(painter, (560, 25, 205, 33), f'第 {game.get("round", 1)} / {game.get("total", 5)} 回合', 20, "#b0dbd2")
        timer = "已暂停" if game.get("paused") or state.get("paused") else str(math.ceil(game["remaining"])) + " 秒"
        if phase == "result" and game["remaining"] <= 0:
            timer = "结算中"
        scene.text(painter, (772, 18, 158, 46), timer, 28, "#efc983", True, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        paper = QRectF(30, 88, 900 if portrait else 592, height - (420 if portrait else 204))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 35))
        painter.drawRoundedRect(paper.translated(0, 7), 22, 22)
        painter.setBrush(QColor("#f6f0df"))
        painter.drawRoundedRect(paper, 22, 22)
        center = Qt.AlignmentFlag.AlignCenter
        if phase == "preparing":
            scene.text(painter, (paper.x() + 20, paper.center().y() - 48, paper.width() - 40, 46), "AI 正在构思" + "·" * (int(age * 2) % 3 + 1), 32, "#24364b", True, center)
            scene.text(painter, (paper.x() + 20, paper.center().y() + 8, paper.width() - 40, 36), "每一题都现场生成，请稍等片刻", 20, "#6c807e", align=center)
        elif game.get("kind") == "idiom" and question:
            word = question.get("word", "")
            tile = min(140, (paper.width() - 78) / 4)
            start = paper.center().x() - tile * 2
            vertical = paper.center().y() - tile / 2 - 8
            scene.text(painter, (paper.x() + 15, vertical - 52, paper.width() - 30, 35), "接住最后一个字，让成语继续旅行", 20, "#607b76", align=center)
            for index, character in enumerate(word[:4]):
                rect = QRectF(start + index * tile + 4, vertical, tile - 8, tile - 8)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#d8e7d8" if index == 3 else "#fffaf0"))
                painter.drawRoundedRect(rect, 16, 16)
                scene.text(painter, rect.getRect(), character, int(tile * .57), "#287c70" if index == 3 else "#24364b", True, center)
            scene.elided(painter, (paper.x() + 26, vertical + tile + 10, paper.width() - 52, 34), question.get("meaning", ""), 19, "#607b76")
        elif question.get("strokes"):
            size = min(paper.width() - 48, paper.height() - 55)
            canvas = QRectF(paper.center().x() - size / 2, paper.y() + 10, size, size)
            progress = 1 if phase == "result" else min(1, age / max(1, game.get("draw_seconds", 12)))
            draw_strokes(painter, question["strokes"], canvas, progress)
            clue = "答案：" + question["answer"] if "answer" in question else question.get("hint") or f'{question.get("letters", 2)} 个字 · 看轮廓，猜猜是什么？'
            scene.elided(painter, (paper.x() + 24, paper.bottom() - 42, paper.width() - 48, 32), clue, 21, "#287c70", True)
        else:
            scene.text(painter, (paper.x() + 20, paper.center().y() - 22, paper.width() - 40, 44), "这一局先休息一下", 28, "#24364b", True, center)
        feedback = game.get("feedback", "")
        scene.elided(painter, (30, paper.bottom() + 15, paper.width(), 34), feedback, 21, "#f5d79a" if game.get("winner") else "#bce1db", True)
        actor_height = 155 if portrait else 200
        actor_width = actor_height * scene.avatar.width() / scene.avatar.height()
        actor_x = 154 if portrait else 790
        actor_y = height - 272 if portrait else 90
        actor = QRectF(actor_x - actor_width / 2, actor_y, actor_width, actor_height)
        scene.draw_character(painter, {**state, "reaction": {}, "celebration_until": 0}, actor, age, now,
                             reaction={"motion": "happy" if game.get("winner") else "peek", "started": now - age})
        score_x = 310 if portrait else 654
        score_y = height - 266 if portrait else 297
        score_width = 590 if portrait else 270
        scene.text(painter, (score_x, score_y, score_width, 30), "本局小榜单", 19, "#8dcbbd", True)
        scores = game.get("scores") or []
        for index, row in enumerate(scores[:3]):
            scene.elided(painter, (score_x, score_y + 36 + index * 32, score_width - 65, 28), f'{index + 1}  {row["name"]}', 19, "#eaf1df")
            scene.text(painter, (score_x + score_width - 60, score_y + 36 + index * 32, 60, 28), str(row["score"]), 20, "#f5d79a", True, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if not scores:
            scene.text(painter, (score_x, score_y + 36, score_width, 32), "等一个聪明的你", 20, "#c8d9d4")
        if game.get("winner"):
            for index in range(22):
                horizontal = 35 + index * 41
                vertical = 75 + ((age * 45 + index * 31) % max(100, paper.height()))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#efc983" if index % 2 else "#89d8bd"))
                painter.drawEllipse(QPointF(horizontal, vertical), 3, 5)
        painter.restore()
