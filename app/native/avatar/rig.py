from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPen, QTransform

from ..costumes import draw_costume

from ...paths import RESOURCE_ROOT

MODEL_PATH = RESOURCE_ROOT / "assets/avatars/deepy/model.json"
REQUIRED_CLOTHES = {"clothes", "skirt", "sleeve_left", "sleeve_right"}


def pair(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("骨架坐标必须包含两个数字")
    if any(isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number)
           or abs(number) > 4096 for number in value):
        raise ValueError("骨架坐标超出范围")
    return tuple(value)


def joint_pose(reaction, now, elapsed):
    angles = {"head": math.sin(elapsed * 1.3) * 1.8,
              "hair_left": math.sin(elapsed * 1.6) * 3, "hair_right": math.sin(elapsed * 1.6 + 1) * 3,
              "tail": math.sin(elapsed * 1.7) * 3,
              "fin_left": math.sin(elapsed * 2.2) * 5, "fin_right": -math.sin(elapsed * 2.2) * 5}
    duration = reaction.get("duration", 0)
    age = now - reaction.get("started", now)
    if duration <= 0 or not 0 <= age < duration:
        return angles
    strength = min(1.0, age / .35, (duration - age) / .5)
    strength = strength * strength * (3 - 2 * strength)
    if strength == 0:
        return angles
    motion = reaction.get("motion")
    target = {}
    if motion and motion.startswith("gift_"):
        from ..gift_poses import gift_pose
        target = gift_pose(motion[5:], age, duration)
    elif motion == "dive_put":
        target = {"upper_right": -25, "forearm_right": -65, "upper_left": 25,
                  "forearm_left": -35, "head": 12, "tail": -24}
    elif motion in ("dive_tuck", "dive_cannonball"):
        target = {"torso": 18, "head": 20, "upper_left": -42, "upper_right": 42,
                  "forearm_left": -95, "forearm_right": 95, "tail": -70, "fin_left": 30, "fin_right": -30}
    elif motion == "dive_pike":
        target = {"torso": 10, "head": -8, "upper_left": 162, "upper_right": -162,
                  "forearm_left": 12, "forearm_right": -12, "tail": -32}
    elif motion == "dive_twist":
        target = {"upper_left": 115, "upper_right": -70, "forearm_left": 30,
                  "forearm_right": -70, "torso": 12, "head": -10, "tail": 26}
    elif motion == "wave":
        target = {"upper_right": -112, "forearm_right": -52, "hand_right": math.sin(age * 12) * 26}
    elif motion in ("stretch", "float", "surprise"):
        height = 125 if motion == "stretch" else 65
        target = {"upper_left": height, "upper_right": -height,
                  "forearm_left": 20, "forearm_right": -20, "head": -4}
    elif motion == "heart":
        target = {"upper_left": -48, "upper_right": 48, "forearm_left": -92,
                  "forearm_right": 92, "hand_left": -20, "hand_right": 20, "head": -5}
    elif motion == "shy":
        target = {"upper_left": -10, "upper_right": 10, "forearm_left": -160,
                  "forearm_right": 160, "head": 9}
    elif motion in ("dance", "happy", "tail_sway"):
        swing = math.sin(age * 5)
        target = {"upper_left": 40 + swing * 25, "upper_right": -40 + swing * 25,
                  "forearm_left": -25, "forearm_right": 25, "head": swing * 7,
                  "tail": swing * 11, "fin_left": swing * 14, "fin_right": -swing * 14}
    elif motion == "bow":
        target = {"torso": 8, "head": 14, "upper_left": -12, "upper_right": 12}
    elif motion in ("nod", "shake", "peek", "sleepy"):
        target = {"head": math.sin(age * 7) * 8 if motion in ("nod", "shake") else 13,
                  "upper_left": 12, "upper_right": -12}
    elif motion == "handstand":
        target = {"upper_left": 150, "upper_right": -150, "forearm_left": 10, "forearm_right": -10,
                  "hair_left": 22, "hair_right": -22, "tail": math.sin(age * 3) * 8}
    elif motion in ("swim", "travel"):
        stroke = math.sin(age * 4.2)
        target = {"upper_left": 65 + stroke * 28, "upper_right": -65 + stroke * 28,
                  "forearm_left": -30 - stroke * 18, "forearm_right": 30 - stroke * 18,
                  "hand_left": stroke * 12, "hand_right": -stroke * 12,
                  "head": math.sin(age * 2.1) * 5, "torso": stroke * 3,
                  "tail": math.sin(age * 4.2 - .8) * 19,
                  "fin_left": math.sin(age * 4.2 - 1.3) * 22, "fin_right": -math.sin(age * 4.2 - 1.3) * 22}
    elif motion == "run":
        stride = math.sin(age * 9)
        target = {"upper_left": stride * 42, "upper_right": -stride * 42,
                  "forearm_left": -70, "forearm_right": 70, "tail": stride * 24,
                  "fin_left": -stride * 30, "fin_right": stride * 30, "torso": stride * 3}
    elif motion == "drive":
        target = {"upper_left": 32, "upper_right": -32, "forearm_left": -80, "forearm_right": 80,
                  "head": math.sin(age * 1.5) * 5, "tail": 12}
    elif motion in ("fishing", "fishing_pull"):
        pulling = motion == "fishing_pull"
        target = {"upper_left": 24, "forearm_left": -82, "upper_right": -48 if pulling else -28,
                  "forearm_right": -58 if pulling else -82, "hand_right": -15,
                  "head": -7 if pulling else 8, "torso": -4 if pulling else 4,
                  "tail": -48, "fin_left": 25, "fin_right": -18}
    elif motion == "sleep":
        target = {"upper_left": 28, "upper_right": -18, "forearm_left": -95, "forearm_right": 55,
                  "head": -12, "tail": 16, "fin_left": 14, "fin_right": -12}
    elif motion == "tea":
        target = {"upper_right": -38, "forearm_right": 115 + math.sin(age * 1.5) * 8, "head": 7}
    elif motion == "lookout":
        target = {"upper_right": -110, "forearm_right": 80, "head": -8}
    elif motion == "bicycle":
        target = {"upper_left": 25, "upper_right": -25, "forearm_left": -90, "forearm_right": 90}
    elif motion == "web_swing":
        target = {"upper_right": -155, "forearm_right": -20, "upper_left": 45, "tail": 14}
    elif motion == "rocket":
        target = {"upper_left": 55, "upper_right": -55, "head": -5}
    for bone, value in target.items():
        angles[bone] = angles.get(bone, 0) * (1 - strength) + value * strength
    overlay = reaction.get("overlay") or {}
    if overlay.get("motion") in ("wave", "heart", "shy", "happy", "nod", "shake", "bow", "surprise", "stretch", "dance", "handstand"):
        secondary = joint_pose(overlay, now, elapsed)
        for bone in ("upper_left", "upper_right", "forearm_left", "forearm_right", "hand_left", "hand_right", "head"):
            angles[bone] = angles.get(bone, 0) * .25 + secondary.get(bone, 0) * .75
    return angles


class LayeredAvatar:
    def __init__(self, model_path=MODEL_PATH):
        model_path = Path(model_path)
        self.directory = model_path.parent.resolve()
        data = json.loads(model_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("不支持的骨架模型版本")
        self.width, self.height = pair(data["canvas"])
        if self.width <= 0 or self.height <= 0:
            raise ValueError("模型画布尺寸无效")
        self.bones = {}
        bones = data["bones"]
        if not isinstance(bones, list) or not 1 <= len(bones) <= 80:
            raise ValueError("骨架数量无效")
        for bone in bones:
            name = bone["name"]
            if not isinstance(name, str) or not name or name in self.bones:
                raise ValueError("重复或无效的骨骼名称")
            self.bones[name] = (bone["parent"], pair(bone["position"]))
        self.order = []
        visiting = set()

        def visit(name):
            if name in visiting or name not in self.bones:
                raise ValueError("骨架存在循环或缺失父节点")
            if name in self.order:
                return
            visiting.add(name)
            parent = self.bones[name][0]
            if parent is not None:
                visit(parent)
            visiting.remove(name)
            self.order.append(name)

        for name in self.bones:
            visit(name)
        if "head" not in self.bones:
            raise ValueError("模型缺少头部关节")
        self.origins = {name: transform.map(QPointF()) for name, transform in self.transforms({}).items()}
        self.images = {}
        base_slots = data["slots"]
        if not isinstance(base_slots, list) or len(base_slots) > 100:
            raise ValueError("图层数量无效")
        self.base = [self._slot(slot["name"], slot) for slot in base_slots]
        if len({slot[0] for slot in self.base}) != len(self.base):
            raise ValueError("图层名称重复")
        self.skins = {}
        if not isinstance(data["skins"], dict) or not 1 <= len(data["skins"]) <= 20:
            raise ValueError("服装列表无效")
        for name, skin in data["skins"].items():
            if not isinstance(skin, dict) or not isinstance(skin.get("slots"), dict):
                raise ValueError("服装槽位必须是对象")
            if set(skin["slots"]) != REQUIRED_CLOTHES:
                raise ValueError("服装必须完整包含上衣、裙子和双袖")
            self.skins[name] = sorted(self.base + [self._slot(key, slot) for key, slot in skin["slots"].items()],
                                      key=lambda slot: slot[4])
        if "sailor" not in self.skins:
            raise ValueError("缺少默认水手服")
        self.last_angles = None
        self.last_action = None
        self.blend_from = {}
        self.blend_started = 0.0
        self.last_elapsed = 0.0

    def animated_angles(self, reaction, now, elapsed):
        target = joint_pose(reaction, now, elapsed)
        action = (reaction.get("motion"), reaction.get("animation_id", reaction.get("started")))
        if self.last_angles is None or elapsed < self.last_elapsed:
            self.blend_from = target.copy()
            self.blend_started = elapsed - .22
        elif action != self.last_action:
            self.blend_from = self.last_angles.copy()
            self.blend_started = elapsed
        progress = min(1, max(0, (elapsed - self.blend_started) / .22))
        progress = progress * progress * (3 - 2 * progress)
        self.last_angles = {name: self.blend_from.get(name, 0) * (1 - progress) + target.get(name, 0) * progress
                            for name in self.bones}
        self.last_action, self.last_elapsed = action, elapsed
        return self.last_angles.copy()

    def _slot(self, name, slot):
        bone = slot["bone"]
        if bone not in self.bones:
            raise ValueError("图层引用了未知骨骼")
        target = (self.directory / slot["file"]).resolve()
        if not target.is_relative_to(self.directory):
            raise ValueError("图层路径超出模型目录")
        if target not in self.images:
            image = QImage(str(target))
            if image.isNull() or image.width() > 4096 or image.height() > 4096:
                raise ValueError("图层不存在或尺寸无效：" + slot["file"])
            self.images[target] = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        order = slot["order"]
        if not isinstance(order, (int, float)) or not math.isfinite(order):
            raise ValueError("图层顺序无效")
        return name, bone, self.images[target], pair(slot["offset"]), order

    def transforms(self, angles):
        transforms = {}
        for name in self.order:
            parent, position = self.bones[name]
            transform = QTransform(transforms[parent]) if parent is not None else QTransform()
            transform.translate(*position)
            transform.rotate(angles.get(name, 0))
            transforms[name] = transform
        return transforms

    def draw(self, painter, rect, *, reaction, now, elapsed, skin, accessory, patches, metadata,
             mouth=0, blink=0, blush=0, debug=False, cropped=False):
        slots = self.skins.get(skin, self.skins["sailor"])
        transforms = self.transforms(self.animated_angles(reaction, now, elapsed))
        painter.save()
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / self.width, rect.height() / self.height)
        base_transform = painter.worldTransform()
        opacity = painter.opacity()
        front = (reaction.get("motion") in ("wave", "heart", "shy", "stretch", "web_swing", "fishing", "fishing_pull")
                 or str(reaction.get("motion", "")).startswith(("gift_", "dive_")))
        deferred = []
        for slot in slots:
            if cropped and slot[1] in ("tail", "fin_left", "fin_right"):
                continue
            if front and slot[1].startswith(("upper_", "forearm_", "hand_")):
                deferred.append(slot)
                continue
            self._draw_slot(painter, slot, transforms, base_transform)
            if slot[0] == "head":
                origin = self.origins["head"]
                painter.setWorldTransform(base_transform)
                painter.setWorldTransform(transforms["head"], combine=True)
                for key, alpha in (("eyes", blink), ("mouth", 1 - mouth)):
                    bounds = metadata[key]["rect"]
                    painter.setOpacity(opacity * max(0, min(1, alpha)))
                    painter.drawImage(QPointF(bounds[0] * self.width - origin.x(),
                                              bounds[1] * self.height - origin.y()), patches[key])
                painter.translate(-origin.x(), -origin.y())
                painter.setOpacity(opacity * blush * .35)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#f287b0"))
                for cheek in (.36, .65):
                    painter.drawEllipse(QRectF(cheek * self.width, .355 * self.height,
                                               .07 * self.width, .024 * self.height))
                painter.setOpacity(opacity)
                draw_costume(painter, QRectF(0, 0, self.width, self.height), accessory)
        for slot in deferred:
            self._draw_slot(painter, slot, transforms, base_transform)
        painter.setWorldTransform(base_transform)
        if debug:
            painter.setPen(QPen(QColor("#59ffdf"), 4))
            painter.setBrush(QColor("#122a45"))
            for name in self.order:
                position = transforms[name].map(QPointF())
                parent = self.bones[name][0]
                if parent:
                    painter.drawLine(transforms[parent].map(QPointF()), position)
                painter.drawEllipse(position, 7, 7)
        anchors = {name: base_transform.map(transform.map(QPointF())) for name, transform in transforms.items()}
        painter.restore()
        return anchors

    @staticmethod
    def _draw_slot(painter, slot, transforms, base_transform):
        name, bone, image, offset, order = slot
        painter.setWorldTransform(base_transform)
        painter.setWorldTransform(transforms[bone], combine=True)
        painter.drawImage(QPointF(*offset), image)
