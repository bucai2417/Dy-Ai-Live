from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient

from ..fishing_catalog import FISH, FISH_KINDS, ITEM_KINDS
from .aquatic_art import silhouette
from .diving_scene import smooth


def fishing_view(state, now):
    game = state.get("fishing") or {}
    extra = 0 if state.get("paused") or game.get("paused") else max(0, now - state.get("fishing_at", now))
    return {**game, "elapsed": game.get("elapsed", 0) + extra,
            "phase_elapsed": game.get("phase_elapsed", 0) + extra,
            "pull_age": game.get("pull_age", 100) + extra,
            "gift_age": game.get("gift_age", 100) + extra,
            "activity_age": game.get("activity_age", 100) + extra,
            "cheer_remaining": max(0, game.get("cheer_remaining", 0) - extra),
            "day_phase": (game.get("day_phase", .5) + extra * game.get("day_speed", 0)) % 1,
            "remaining": max(0, game.get("remaining", 0) - extra)}


def fish_image(kind):
    preset = FISH[kind]
    image = QImage(300, 160, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if silhouette(painter, kind, preset):
        painter.end()
        return image
    painter.setPen(Qt.PenStyle.NoPen)
    body_color, accent = QColor(preset["color"]), QColor(preset["accent"])
    painter.setBrush(accent)
    painter.drawPolygon(QPolygonF([QPointF(88, 80), QPointF(22, 29), QPointF(35, 80), QPointF(22, 131)]))
    painter.setPen(QPen(body_color, 2))
    for offset in (-30, -15, 0, 15, 30):
        painter.drawLine(QPointF(79, 80), QPointF(33, 80 + offset))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(accent)
    painter.drawPolygon(QPolygonF([QPointF(117, 49), QPointF(158, 14), QPointF(200, 51)]))
    painter.drawPolygon(QPolygonF([QPointF(125, 110), QPointF(158, 146), QPointF(178, 109)]))
    body = QPainterPath()
    body.moveTo(71, 80)
    body.cubicTo(125, 6, 233, 32, 274, 78)
    body.cubicTo(237, 129, 119, 147, 71, 80)
    gradient = QLinearGradient(0, 35, 0, 125)
    gradient.setColorAt(0, body_color.lighter(115))
    gradient.setColorAt(.6, body_color)
    gradient.setColorAt(1, accent)
    painter.setBrush(gradient)
    painter.drawPath(body)
    painter.save()
    painter.setClipPath(body)
    if kind == "koi":
        painter.setBrush(accent)
        for horizontal, vertical, radius in ((120, 57, 20), (172, 89, 26), (219, 50, 17)):
            painter.drawEllipse(QPointF(horizontal, vertical), radius, radius * .65)
    elif kind in ("perch", "tuna", "clown", "salmon"):
        painter.setPen(QPen(accent, 8 if kind == "perch" else 4))
        for horizontal in range(100, 214, 23):
            painter.drawLine(QPointF(horizontal, 37), QPointF(horizontal + 12, 100))
    else:
        painter.setPen(QPen(QColor(255, 255, 240, 100), 1.3))
        for row in range(3):
            for column in range(8):
                painter.drawArc(QRectF(98 + column * 16 + row % 2 * 8, 55 + row * 17, 15, 17), -90 * 16, 180 * 16)
    painter.restore()
    painter.setPen(QPen(accent.darker(130), 2))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(209, 53, 21, 57), -80 * 16, 160 * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(body_color.lighter(130))
    painter.drawEllipse(QPointF(242, 68), 10, 10)
    painter.setBrush(QColor("#122a3a"))
    painter.drawEllipse(QPointF(245, 68), 5, 6)
    painter.setBrush(QColor("#ffffff"))
    painter.drawEllipse(QPointF(247, 66), 1.8, 1.8)
    painter.setBrush(accent)
    painter.drawPolygon(QPolygonF([QPointF(205, 89), QPointF(181, 117), QPointF(179, 90)]))
    painter.end()
    return image


class FishingRenderer:
    def __init__(self):
        self.fish_images = {kind: fish_image(kind) for kind in FISH}
        self.backgrounds = {}

    def _background(self, height, night=False):
        key = (math.ceil(height), night)
        if key in self.backgrounds:
            return self.backgrounds[key]
        image = QImage(960, math.ceil(height), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sky = QLinearGradient(0, 0, 0, height)
        sky.setColorAt(0, QColor("#081329" if night else "#1d4e65"))
        sky.setColorAt(.48, QColor("#344c70" if night else "#a3c6bc"))
        sky.setColorAt(1, QColor("#092e43"))
        painter.fillRect(image.rect(), sky)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f6dfad"))
        painter.drawEllipse(QPointF(760, height * .20), 35, 35)
        if night:
            painter.setBrush(QColor("#14233d"))
            painter.drawEllipse(QPointF(774, height * .20 - 9), 30, 30)
            for index in range(55):
                painter.setBrush(QColor(199, 224, 251, 90 + index % 5 * 30))
                painter.drawEllipse(QPointF((index * 137 + 29) % 950, 25 + (index * 47) % max(30, int(height * .28))), 1 + index % 2, 1 + index % 2)
        for layer, color in ((0, "#5d8f95"), (1, "#367681"), (2, "#235d6e")):
            points = [QPointF(0, height * .55)]
            for index in range(15):
                points.append(QPointF(index * 72, height * (.32 + layer * .06) - math.sin(index * 1.3 + layer) * 35))
            points.append(QPointF(960, height * .55))
            painter.setBrush(QColor(color).darker(210) if night else QColor(color))
            painter.drawPolygon(QPolygonF(points))
        water_top = height * .52
        water = QLinearGradient(0, water_top, 0, height)
        water.setColorAt(0, QColor("#264766" if night else "#3e9eaa"))
        water.setColorAt(.28, QColor("#19334d" if night else "#226a83"))
        water.setColorAt(1, QColor("#101f37" if night else "#10344e"))
        painter.fillRect(QRectF(0, water_top, 960, height - water_top), water)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(198, 238, 214, 18))
        for horizontal in (340, 550, 770):
            painter.drawPolygon(QPolygonF([QPointF(horizontal, water_top), QPointF(horizontal + 60, water_top),
                                           QPointF(horizontal + 150, height), QPointF(horizontal - 80, height)]))
        painter.setPen(QPen(QColor("#397679"), 5))
        for horizontal in (375, 415, 850, 900, 930):
            path = QPainterPath(QPointF(horizontal, height - 55))
            path.cubicTo(horizontal - 28, height - 100, horizontal + 30, height - 110, horizontal, height - 145)
            painter.drawPath(path)
        painter.end()
        if len(self.backgrounds) >= 4:
            self.backgrounds.clear()
        self.backgrounds[key] = image
        return image

    def draw(self, painter, scene, state, bounds, now, show_actor=True):
        game = fishing_view(state, now)
        left, top, width, height = bounds
        height = height * 960 / width
        clock = game["elapsed"]
        phase = game.get("phase", "waiting")
        painter.save()
        painter.translate(left, top)
        painter.scale(width / 960, width / 960)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, 960, height), 28, 28)
        painter.setClipPath(clip)
        painter.drawImage(QPointF(), self._background(height))
        day_phase = game.get("day_phase", .5)
        daylight = smooth((.5 - math.cos(day_phase * math.tau) * .5 - .20) / .60)
        if daylight < 1:
            painter.save()
            painter.setOpacity(1 - daylight)
            painter.drawImage(QPointF(), self._background(height, True))
            painter.restore()
        surface, bottom = height * .52, height - 100
        painter.setPen(QPen(QColor(191, 241, 223, 85), 1.5))
        for index in range(15):
            horizontal = (index * 83 + clock * 12) % 1010 - 40
            vertical = surface + 5 + index % 4 * 10
            painter.drawLine(QPointF(horizontal, vertical), QPointF(horizontal + 30 + index % 3 * 16, vertical))
        fish = game.get("fish") or {}
        for index, swimmer in enumerate(game.get("school") or []):
            if index == fish.get("slot") and phase != "waiting":
                continue
            preset = FISH[swimmer["kind"]]
            span = max(60, bottom - surface - 40)
            travel = (swimmer["phase"] + clock * swimmer["speed"]) % 2
            forward = travel < 1
            fraction = travel if forward else 2 - travel
            horizontal = 370 + fraction * 510
            vertical = surface + 35 + swimmer["depth"] * span + math.sin(clock * 1.8 + index) * 6
            if phase == "diving" and game["phase_elapsed"] >= 5.8:
                flight = smooth((game["phase_elapsed"] - 5.8) / 1.7)
                direction = -1 if horizontal < 580 else 1
                horizontal += direction * flight * 1150
                forward = direction > 0
            if (game.get("interruption") or {}).get("action") == "feed":
                approach = smooth(game["phase_elapsed"] / 4.5) * .65
                horizontal += (520 + index % 4 * 32 - horizontal) * approach
                vertical += (surface + 45 + index % 3 * 18 - vertical) * approach
            self._fish(painter, swimmer["kind"], horizontal, vertical, 75 * preset["size"], forward, .78)
        painter.setPen(QPen(QColor(190, 243, 238, 65), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(16):
            phase_bubble = (clock * .12 + index * .173) % 1
            painter.drawEllipse(QPointF(365 + index * 37, height - 80 - phase_bubble * (height - surface - 90)), 2 + index % 3, 2 + index % 3)
        dock_top = height * .46
        painter.setPen(Qt.PenStyle.NoPen)
        for horizontal in (48, 265):
            painter.setBrush(QColor("#314b54"))
            painter.drawRoundedRect(QRectF(horizontal, dock_top + 22, 28, height - dock_top), 5, 5)
            painter.setBrush(QColor("#65817b"))
            painter.drawRect(QRectF(horizontal, dock_top + 22, 7, height - dock_top))
        painter.setBrush(QColor("#517278"))
        painter.drawPolygon(QPolygonF([QPointF(0, dock_top + 15), QPointF(310, dock_top + 15),
                                       QPointF(335, dock_top + 42), QPointF(0, dock_top + 42)]))
        painter.setBrush(QColor("#c8b390"))
        painter.drawPolygon(QPolygonF([QPointF(0, dock_top - 10), QPointF(280, dock_top - 10),
                                       QPointF(335, dock_top + 15), QPointF(0, dock_top + 15)]))
        painter.setPen(QPen(QColor("#92896d"), 2))
        for horizontal in range(0, 300, 34):
            painter.drawLine(QPointF(horizontal, dock_top - 10), QPointF(horizontal + 35, dock_top + 15))
        painter.setPen(QPen(QColor("#e9d9b7"), 8))
        painter.drawLine(QPointF(108, dock_top - 30), QPointF(230, dock_top - 30))
        painter.setPen(QPen(QColor("#365b62"), 5))
        for horizontal in (125, 217):
            painter.drawLine(QPointF(horizontal, dock_top - 29), QPointF(horizontal + 5, dock_top + 8))
        from .fishing_actions_scene import draw_fishing_action, draw_lantern
        draw_lantern(painter, dock_top, 1 - daylight, clock)
        if phase in ("net_cast", "net_caught") or (game.get("interruption") or {}).get("action") == "feed":
            draw_fishing_action(painter, scene, state, game, height, now, show_actor, self)
        elif phase == "diving":
            from .diving_scene import draw_dive
            draw_dive(painter, scene, state, game, height, now, show_actor)
        elif phase == "interacting":
            self._interaction(painter, scene, state, game, height, now, show_actor)
        else:
            self._angler(painter, scene, state, game, height, now, show_actor, dock_top, clock, phase, surface, bottom, fish)
        scene.outlined(painter, (583, 19, 350, 26), "云汐钓台 · 常驻" if game.get("dedicated") else "云汐钓台 · 协力", 22, "#f3dfb5", True, "right")
        light_label = "夜钓" if daylight < .15 else "白昼" if daylight > .85 else "晨曦" if day_phase < .5 else "暮色"
        scene.outlined(painter, (533, 47, 400, 23), f"{light_label} · {len(FISH_KINDS)} 种水族 / {len(ITEM_KINDS)} 件奇物", 15, "#c7e2df", align="right")
        if game.get("dedicated"):
            self._species(painter, scene, state, game, height)
        self._status(painter, scene, game, height)
        if phase in ("caught", "escaped"):
            self._result(painter, scene, game, height)
        painter.restore()

    def _species(self, painter, scene, state, game, height):
        board = (state.get("stats") or {}).get("fishingRanking") or {}
        animals, items = board.get("species") or [], board.get("items") or []
        species = animals + items
        count = len(species)
        page_size = 2
        pages = max(1, math.ceil(count / page_size))
        page = int(state.get("animation_time", game["elapsed"]) / 6) % pages
        top = height - 94
        scene.outlined(painter, (26, top, 398, 24), f"今日水族 {len(animals)}/{len(FISH_KINDS)} · 奇物 {len(items)}/{len(ITEM_KINDS)} · {board.get('encounters', 0)} 次", 15, "#d6e9df", True)
        if not species:
            scene.outlined(painter, (434, top, 450, 24), "等第一份鱼获 · 含脱钩，同日保留", 15, "#b8d8d9")
        for index, entry in enumerate(species[page * page_size:(page + 1) * page_size]):
            horizontal = 434 + index * 223
            image = self.fish_images.get(entry["kind"])
            if image:
                painter.drawImage(QRectF(horizontal, top + 1, 42, 23), image)
            scene.outlined(painter, (horizontal + 44, top, 175, 24), f"{entry['name']} ×{entry['count']}", 15, "#d2e9e6")
        if pages > 1:
            scene.outlined(painter, (895, top, 39, 24), f"{page + 1}/{pages}", 12, "#acc9d1", align="right")

    def _interaction(self, painter, scene, state, game, height, now, show_actor):
        from .actions import pose
        from .effects import draw_particles
        dock = height * .46
        painter.setPen(QPen(QColor("#ddbd83"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(80, dock - 12), QPointF(295, dock - 14))
        if not show_actor:
            return
        frozen_now = state.get("paused_at", now) if state.get("paused") else now
        reaction = state.get("reaction", {}) if frozen_now < state.get("reaction_until", 0) else {}
        movement = pose(reaction, frozen_now)
        character_height = min(360, height * .50)
        character_width = character_height * scene.avatar.width() / scene.avatar.height()
        painter.save()
        painter.translate(192 + movement["x"], dock - character_height * .36 + movement["y"])
        painter.rotate(movement["tilt"])
        painter.scale(movement["scale_x"], movement["scale_y"])
        scene.draw_character(painter, state, QRectF(-character_width / 2, -character_height / 2,
                                                    character_width, character_height), game["elapsed"], frozen_now, reaction, movement)
        painter.restore()
        draw_particles(painter, reaction, (35, dock - character_height, 300, character_height), frozen_now)

    def _angler(self, painter, scene, state, game, height, now, show_actor, dock_top, clock, phase, surface, bottom, fish):
        character_height = min(420, height * .48)
        character_width = character_height * scene.avatar.width() / scene.avatar.height()
        actor = QRectF(177 - character_width / 2, dock_top - 28 - character_height * .68, character_width, character_height)
        frozen_now = state.get("paused_at", now) if state.get("paused") else now
        pull = max(0, 1 - game["pull_age"] / .65)
        activity = game.get("activity") or {}
        motion = "fishing_pull" if pull > 0 else "fishing"
        if game.get("activity_age", 100) < 2.5 and activity.get("kind") in ("loosen", "cheer"):
            motion = "fishing_" + activity["kind"]
        reaction = {"motion": motion, "duration": 86400,
                    "started": frozen_now - 2 - clock, "animation_id": game.get("round_id", "") + str(pull > 0)}
        anchors = scene.draw_character(painter, state, actor, clock, frozen_now, reaction) if show_actor else None
        grip = QPointF(233, dock_top - 103)
        if anchors and "hand_right" in anchors:
            inverse, valid = painter.worldTransform().inverted()
            if valid:
                grip = inverse.map(anchors["hand_right"])
        tip = QPointF(592 - pull * 25, height * .16 - pull * 15)
        rod = QPainterPath(grip)
        rod.quadTo((grip.x() + tip.x()) * .5, tip.y() - 50 + (24 if phase == "hooked" else 0), tip.x(), tip.y())
        painter.setPen(QPen(QColor("#e4c685"), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(rod)
        painter.setPen(QPen(QColor("#243c48"), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(grip, QPointF(grip.x() - 18, grip.y() + 14))
        painter.setPen(QPen(QColor("#e4c685"), 3))
        painter.drawEllipse(QPointF(grip.x() + 7, grip.y() + 15), 10, 10)
        progress = game.get("progress", 0)
        hook_x = 614 + math.sin(clock * (6 if phase == "hooked" else 1.3)) * (10 if phase == "hooked" else 3)
        hook_y = surface + (bottom - surface) * .65 * (1 - progress) + 18
        if phase == "landing":
            hook_y = surface + 18 - min(1, game["phase_elapsed"] / 1.6) * height * .24
        if phase == "caught":
            hook_y = surface - height * .24
        painter.setPen(QPen(QColor("#d8f4e8"), 1.7))
        if motion == "fishing_loosen":
            slack = QPainterPath(tip)
            slack.cubicTo(tip.x() + 90, tip.y() + 80, hook_x - 100, hook_y - 50, hook_x, hook_y)
            painter.drawPath(slack)
        else:
            painter.drawLine(tip, QPointF(hook_x, hook_y))
        if phase in ("hooked", "landing") and fish:
            fish_width = 90 * FISH[fish["kind"]]["size"]
            self._fish(painter, fish["kind"], hook_x - fish_width * .4, hook_y + 2, fish_width, True)
        elif phase != "caught":
            painter.drawArc(QRectF(hook_x - 7, hook_y - 3, 14, 17), 175 * 16, 225 * 16)
        if phase in ("waiting", "hooked"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffc978" if phase == "hooked" else "#f2f5d9"))
            bob = math.sin(clock * (10 if phase == "hooked" else 2)) * (6 if phase == "hooked" else 2)
            painter.drawRoundedRect(QRectF(hook_x - 5, surface - 10 + bob, 10, 25), 5, 5)
            painter.setBrush(QColor("#e97760"))
            painter.drawEllipse(QPointF(hook_x, surface - 8 + bob), 5, 6)
            painter.setPen(QPen(QColor(252, 225, 153, 150), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = 15 + (clock * 22) % 35
            painter.drawEllipse(QPointF(hook_x, surface + 9), radius, radius * .22)
        if game.get("gift_age", 100) < 3:
            age = game["gift_age"]
            painter.save()
            painter.setOpacity(max(0, 1 - age / 3))
            painter.setPen(QPen(QColor("#ffe0a0"), 4))
            for index in range(5):
                radius = 20 + age * 65 + index * 16
                painter.drawEllipse(QPointF(hook_x, surface), radius, radius * .32)
            donor = (game.get("gift_boost") or {}).get("name", "鱼友")
            scene.elided(painter, (350, surface - 72, 570, 40), donor + " · 礼物强力拉钩！", 26, "#ffe0a0", True)
            painter.restore()

    def _fish(self, painter, kind, horizontal, vertical, width, forward, opacity=1):
        painter.save()
        painter.translate(horizontal, vertical)
        painter.scale(1 if forward else -1, 1)
        painter.setOpacity(opacity)
        painter.drawImage(QRectF(-width / 2, -width * .26, width, width * .53), self.fish_images[kind])
        painter.restore()

    def _status(self, painter, scene, game, height):
        phase = game.get("phase")
        item = (game.get("fish") or {}).get("category") == "item"
        title = {"waiting": "静候鱼讯 · 咬钩后点赞 / 拉钩收线", "hooked": "钩住奇物！发拉 / 拉钩或点赞打捞" if item else "咬钩了！发拉 / 拉钩或点赞收线",
                 "landing": "稳住！宝贝正在出水" if item else "稳住！鱼正在出水", "caught": "打捞成功！" if item else "捕获成功！",
                 "escaped": "这次差一点，再接再厉", "diving": "花式跳水 · 鱼群避让", "interacting": "放竿互动 · 表演后继续本轮"}.get(phase, "协力钓鱼")
        if phase in ("net_cast", "net_caught"):
            age = game["phase_elapsed"]
            title = "专属鱼获 · 礼赠心意满载而归" if phase == "net_caught" else next(text for end, text in ((2, "放下鱼竿 · 提网蓄力"), (4.5, "挥臂撒网 · 网花入水"), (6.5, "沉网捕鱼 · 静候惊喜"), (float("inf"), "双手收网 · 鱼获出水")) if age < end)
        elif (game.get("interruption") or {}).get("action") == "feed":
            title = "投饵引鱼 · 鱼群聚拢，咬钩等待缩短"
        elif game.get("activity_age", 100) < 3 and (game.get("activity") or {}).get("kind") == "loosen":
            title = "鱼线松了！进度 -" + format(game["activity"].get("amount", 0), ".0%") + " · 快拉回来！"
        elif game.get("cheer_remaining", 0) > 0 and phase == "hooked":
            title = f"加油护线 {math.ceil(game['cheer_remaining'])} 秒 · 脱钩风险减半"
        if phase == "diving":
            dive = game.get("dive") or {}
            style = {"tuck": "抱团翻腾", "pike": "屈体前翻", "twist": "转体入水", "cannonball": "炮弹炸水"}.get(dive.get("style"), "花式跳水")
            title = f"{style} · 游 {dive.get('laps', 3)} 圈再回台"
        scene.outlined(painter, (26, height - 62, 750, 28), title, 21, "#ffdb95" if phase == "hooked" else "#e4f3ef", True)
        if phase == "hooked":
            scene.outlined(painter, (785, height - 62, 149, 28), f"剩余 {math.ceil(game['remaining'])} 秒", 17, "#c3ece1", align="right")
            scene.outlined(painter, (30, height - 35, 80, 25), f"{game['progress']:.1%}", 15, "#83e0c5")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(9, 40, 53, 120))
            painter.drawRoundedRect(QRectF(120, height - 21, 440, 5), 2.5, 2.5)
            painter.setBrush(QColor("#83e0c5"))
            painter.drawRoundedRect(QRectF(120, height - 21, 440 * game["progress"], 5), 2.5, 2.5)
            winner = (game.get("winner") or {}).get("name")
            scene.outlined(painter, (580, height - 33, 354, 25), "领先 · " + winner if winner else f"{game['likes_per_pull']} 赞 = 拉一次", 15, "#c7e2df", align="right")
        else:
            hint = "常驻直播 · 自动续竿" if game.get("dedicated") else "发不钓了返回"
            if phase in ("diving", "interacting"):
                hint = f"约 {math.ceil(game['remaining'])} 秒回钓台"
            elif phase in ("net_cast", "net_caught"):
                hint = f"完成后继续原鱼竿 · 等待撒网 {game.get('net_pending', 0)} 位鱼友"
            elif game.get("gift_pending"):
                hint = f"已存 {game['gift_pending']} 件礼物助力，咬钩自动结算"
            else:
                hint = "发投饵 / 跳水 · 咬钩后发拉 / 加油" + (" · 松可捣蛋" if game.get("loosen_enabled", True) else "")
            scene.outlined(painter, (26, height - 31, 908, 24), hint, 15, "#b8d8d9")

    def _result(self, painter, scene, game, height):
        caught = game["phase"] == "caught"
        fish = game.get("fish") or {}
        item = fish.get("category") == "item"
        card_height = 340
        top = (height - card_height) / 2 - 15
        painter.setPen(QPen(QColor(176, 225, 222, 85), 1))
        painter.setBrush(QColor(8, 33, 48, 165))
        painter.drawRoundedRect(QRectF(220, top, 520, card_height), 24, 24)
        if caught:
            for index in range(26):
                angle = index * 2.399
                radius = 110 + (game["phase_elapsed"] * 60 + index * 17) % 210
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(("#ffd685", "#83dac6", "#d3b6ed")[index % 3]))
                painter.drawEllipse(QPointF(480 + math.cos(angle) * radius, top + 152 + math.sin(angle) * radius * .48), 3, 4)
            winner = (game.get("winner") or {}).get("name")
            scene.outlined(painter, (240, top + 12, 480, 38), "恭喜 " + winner if winner else "匿名合力收获成功", 27, "#ffe0a0", True, "center")
            scene.outlined(painter, (240, top + 51, 480, 24), "本轮贡献冠军 · 大家协力收获" if winner else "未收到用户身份 · 发拉钩可记录名字", 15, "#c7e2df", align="center")
            painter.drawImage(QRectF(364, top + 78, 232, 124), self.fish_images[fish["kind"]])
            scene.outlined(painter, (240, top + 207, 480, 33), fish["name"] + " · " + fish["rarity"], 25, "#f3e9d2", True, "center")
            scene.outlined(painter, (240, top + 242, 480, 27), f"{fish['weight']:.2f} kg  /  图鉴价值 {fish['value']:,} 分", 19, "#a7f1dc", align="center")
            if item:
                scene.outlined(painter, (240, top + 271, 480, 25), fish.get("description", "趣味收藏物品"), 16, "#d7c5ee", align="center")
        else:
            scene.outlined(painter, (240, top + 72, 480, 48), "哎呀，宝贝滑回水里了" if item else "哎呀，鱼儿溜走了", 28, "#d0e5ef", True, "center")
            scene.outlined(painter, (240, top + 137, 480, 36), game.get("reason", ""), 20, "#c0dbe1", align="center")
            scene.outlined(painter, (240, top + 196, 480, 36), "下一次，换我们赢！", 23, "#e8d5b4", align="center")
        next_action = "自动续竿" if game.get("dedicated") else "返回原场景"
        footer = f"{math.ceil(game['remaining'])} 秒后{next_action}" if game["remaining"] > 0 else "展示中 · 稍后" + next_action
        scene.outlined(painter, (240, top + 305, 480, 23), footer, 15, "#a8c3cf", align="center")
