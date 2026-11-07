from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient

from ..world import ACTIVITIES, SCENES, WEATHERS, WorldDirector, view
from ..world_navigation import LANDMARKS
from .actions import pose
from .effects import draw_particles
from .music_view import music_view


@dataclass(frozen=True)
class Face:
    vertices: tuple
    color: str
    light: float = 1.0
    double_sided: bool = False


class Mesh:
    def __init__(self):
        self.faces = []

    def face(self, vertices, color, light=1, double_sided=False):
        self.faces.append(Face(tuple(vertices), color, light, double_sided))

    def box(self, center, size, color):
        horizontal, vertical, depth = center
        width, height, length = (value / 2 for value in size)
        corners = [(horizontal + side * width, vertical + up * height, depth + back * length)
                   for side, up, back in ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                                         (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))]
        for indices, light in (((0, 1, 2, 3), .67), ((4, 7, 6, 5), .91), ((0, 3, 7, 4), .76),
                               ((1, 5, 6, 2), .83), ((3, 2, 6, 7), 1.08)):
            self.face([corners[index] for index in indices], color, light)

    def cylinder(self, center, radius, height, color, upper=None, sides=12):
        horizontal, vertical, depth = center
        upper = radius if upper is None else upper
        bottom, top = [], []
        for index in range(sides):
            angle = index / sides * math.tau
            bottom.append((horizontal + math.cos(angle) * radius, vertical, depth + math.sin(angle) * radius))
            top.append((horizontal + math.cos(angle) * upper, vertical + height, depth + math.sin(angle) * upper))
        self.face(top, color, 1.08)
        for index in range(sides):
            following = (index + 1) % sides
            self.face((bottom[index], bottom[following], top[following], top[index]), color,
                      .78 + math.sin(index / sides * math.tau) * .12)

    def plant(self, horizontal, depth, scale=1):
        self.cylinder((horizontal, 0, depth), .24 * scale, .48 * scale, "#e2b38b", .31 * scale)
        self.cylinder((horizontal, .48 * scale, depth), .27 * scale, .025, "#384d48")
        for index in range(9):
            angle = index * 2.399
            spread = (.4 + index % 3 * .13) * scale
            tip = (horizontal + math.cos(angle) * spread, (.85 + index % 4 * .22) * scale,
                   depth + math.sin(angle) * spread)
            base = (horizontal, .44 * scale, depth)
            side = .15 * scale
            self.face((base, (tip[0] - side, tip[1] - .2, tip[2]), tip,
                       (tip[0] + side, tip[1] - .2, tip[2])), ("#51ad9d", "#438c83", "#79c5aa")[index % 3], double_sided=True)

    def ring(self, center, radius, width, color, sides=64, stretch=1):
        horizontal, vertical, depth = center
        for index in range(sides):
            corners = []
            for angle, size in ((index, radius), (index + 1, radius), (index + 1, radius - width), (index, radius - width)):
                phase = angle / sides * math.tau
                corners.append((horizontal + math.cos(phase) * size, vertical, depth + math.sin(phase) * size * stretch))
            self.face(corners, color, double_sided=True)


def build_room(weather):
    mesh = Mesh()
    night = weather == "night"
    mesh.box((0, -.18, 0), (8.2, .34, 6), "#233c51")
    for index in range(16):
        mesh.box((-3.75 + index * .5, .005, 0), (.485, .06, 5.8), ("#b5ad96", "#c3baa2", "#bdb29a")[index % 3])
    wall = "#68828f" if night else "#a1b9bc"
    mesh.box((0, .53, -2.83), (8, 1.05, .18), wall)
    mesh.box((0, 3.38, -2.83), (8, .44, .18), wall)
    for side in (-1, 1):
        mesh.box((side * 3.36, 2.1, -2.83), (1.28, 2.2, .18), wall)
        mesh.box((side * 2.74, 2.1, -2.67), (.12, 2.25, .16), "#e9d7b5")
        mesh.box((side * 3.97, .5, 0), (.12, 1, 5.6), wall)
        for curtain in range(4):
            mesh.box((side * (2.9 + curtain * .13), 2.1, -2.45), (.12, 2.16, .12),
                     ("#6d999d", "#80aeb0")[curtain % 2])
    for vertical in (1.02, 3.2):
        mesh.box((0, vertical, -2.63), (5.65, .11, .25), "#e9d7b5")
    for horizontal in (-.94, .94):
        mesh.box((horizontal, 2.1, -2.76), (.055, 2.13, .08), "#d9dbcf")
    mesh.box((0, 1, -2.5), (5.8, .12, .55), "#d7ccb2")
    mesh.box((0, .055, .3), (3.4, .025, 2.7), "#4a888e")
    mesh.box((0, .071, .3), (3.18, .008, 2.48), "#609c9e")
    mesh.box((0, .078, .3), (2.94, .008, 2.24), "#558e94")
    sofa(mesh, -2.45, -1.92)
    mesh.box((2.95, .59, -1.92), (1.4, 1.16, .86), "#8c7162")
    mesh.box((2.95, 1.21, -1.92), (1.5, .1, .98), "#d8ba94")
    for horizontal in (2.61, 3.29):
        mesh.box((horizontal, .64, -1.474), (.59, .79, .035), "#a48970")
        mesh.box((horizontal, .75, -1.443), (.1, .04, .03), "#e1c394")
    for index in range(5):
        mesh.box((2.65 + index * .12, 1.46, -1.95), (.085, .43 + index % 2 * .08, .3),
                 ("#76aab1", "#e0b793", "#405e79")[index % 3])
    mesh.cylinder((3.27, 1.27, -1.9), .16, .3, "#e8dac6", .11)
    mesh.plant(3.55, .4, .95)
    mesh.plant(-3.4, 1.7, .8)
    mesh.cylinder((2.82, 0, 1.78), .56, .12, "#3d5965")
    mesh.cylinder((2.82, .12, 1.78), .12, .56, "#a48a70")
    mesh.cylinder((2.82, .68, 1.78), .64, .1, "#d5bc97")
    mesh.cylinder((2.72, .78, 1.75), .13, .17, "#e0e4d8", .15)
    mesh.box((3.0, .8, 1.93), (.32, .05, .25), "#648d9e")
    mesh.cylinder((-3.25, 0, -1.1), .3, .08, "#4a626d")
    mesh.cylinder((-3.25, .08, -1.1), .035, 2.35, "#d1b68b", sides=8)
    mesh.cylinder((-3.25, 2.05, -1.1), .47, .54, "#f5d8a5", .26)
    return mesh.faces


def sofa(mesh, horizontal, depth):
    for offset in (-.7, .7):
        mesh.box((horizontal + offset, .15, depth), (.1, .3, .65), "#695d59")
    mesh.box((horizontal, .47, depth), (1.85, .55, 1.07), "#427d89")
    mesh.box((horizontal, .94, depth - .43), (1.85, .87, .21), "#588d96")
    for offset in (-.87, .87):
        mesh.box((horizontal + offset, .77, depth), (.22, .52, 1.12), "#62979e")
    for offset in (-.39, .39):
        mesh.box((horizontal + offset, .78, depth + .06), (.73, .13, .73), "#88b4b4")
    mesh.box((horizontal - .46, 1.03, depth - .2), (.4, .42, .18), "#eed4ac")
    mesh.box((horizontal + .45, 1.03, depth - .2), (.42, .43, .2), "#a7c8bd")


def build_terrace(weather):
    mesh = Mesh()
    mesh.box((0, -.2, 0), (8.3, .4, 6), "#29465a")
    for index in range(20):
        mesh.box((-3.8 + index * .4, .02, 0), (.385, .06, 5.8),
                 ("#b5c4c4", "#a6b9c0")[index % 2] if weather == "snow" else ("#9e9d91", "#aba899")[index % 2])
    for horizontal in (-3.8, -2.85, -1.9, -.95, 0, .95, 1.9, 2.85, 3.8):
        mesh.box((horizontal, .58, -2.75), (.07, 1.1, .08), "#719394")
        mesh.cylinder((horizontal, 1.12, -2.75), .075, .07, "#ffdd9b", sides=8)
    mesh.box((0, 1.1, -2.75), (7.9, .09, .12), "#9cb7b1")
    for horizontal in (-3.7, 3.7):
        mesh.box((horizontal, 1.68, -2.55), (.16, 3.3, .16), "#527782")
    mesh.box((0, 3.27, -2.55), (7.8, .16, .2), "#6a9297")
    for index in range(13):
        horizontal = -3.4 + index * .57
        vertical = 3.05 - .35 * math.sin(index / 12 * math.pi)
        mesh.cylinder((horizontal, vertical, -2.5), .065, .12, "#ffe6b6", .09, sides=8)
    mesh.box((0, .062, .2), (3.4, .025, 2.5), "#44798a")
    mesh.box((0, .08, .2), (3.1, .012, 2.2), "#558e9b")
    sofa(mesh, -2.45, -1.95)
    mesh.plant(-3.45, .4, .9)
    mesh.plant(3.4, 1.6, 1.05)
    mesh.plant(3.3, -2.05, .85)
    mesh.cylinder((2.9, 0, -.5), .5, .13, "#345865")
    mesh.cylinder((2.9, .13, -.5), .045, 1.4, "#d1b989", sides=8)
    mesh.box((2.9, 1.55, -.55), (.35, .34, 1.05), "#84abb8")
    mesh.box((2.9, 1.55, -1.1), (.38, .37, .08), "#31536d")
    return mesh.faces


def build_cloud(weather):
    mesh = Mesh()
    mesh.box((0, -.35, .1), (12.2, .7, 8.4), "#355b6b")
    mesh.box((0, -.02, .1), (12, .12, 8.2), "#c0cfcb" if weather == "snow" else "#91b2ab")
    mesh.box((0, .055, .55), (11.5, .04, 6.8), "#779d87")
    mesh.ring((0, .085, .55), 4.95, 1.3, "#546d7c", stretch=.48)
    mesh.ring((0, .091, .55), 4.98, .06, "#d2ddca", stretch=.48)
    mesh.ring((0, .091, .55), 3.65, .06, "#d2ddca", stretch=.48)
    for index in range(36):
        angle = index / 36 * math.tau
        mesh.box((4.3 * math.cos(angle), .1, .55 + 2.05 * math.sin(angle)), (.16, .015, .06), "#ecd8a0")
    mesh.box((0, .09, -1.65), (5.6, .05, .5), "#d3c8ac")
    mesh.box((0, .09, 1.8), (5.9, .05, .6), "#d3c8ac")
    mesh.box((0, .1, .25), (2.35, .1, 1.45), "#c4c9b4")
    mesh.box((0, .17, .25), (2.05, .05, 1.18), "#469ba8")
    mesh.cylinder((0, .2, .25), .45, .13, "#d7d8c3", sides=16)
    mesh.cylinder((0, .33, .25), .16, .63, "#e6d3ac", .07)
    mesh.cylinder((0, .91, .25), .44, .08, "#d8c79d", .4)
    mesh.box((0, .18, -2.9), (5.35, .3, 2.25), "#b6c5bb")
    mesh.box((0, 1.55, -3.75), (4.95, 2.5, .12), "#456b7f")
    for horizontal in (-2.35, -1.2, 1.2, 2.35):
        mesh.box((horizontal, 1.55, -2.05), (.16, 2.55, .16), "#e1cfb1")
        mesh.box((horizontal, 1.55, -3.65), (.16, 2.55, .16), "#e1cfb1")
    for horizontal in (-1.8, 1.8):
        mesh.box((horizontal, 1.52, -2.12), (1.0, 2.25, .045), "#72a6ad")
        for vertical in (.75, 1.5, 2.25):
            mesh.box((horizontal, vertical, -2.08), (1.0, .025, .03), "#b1d0c6")
    mesh.box((0, 1.48, -2.32), (1.55, 2.3, .05), "#2e5366")
    mesh.box((0, 2.83, -2.9), (5.65, .16, 2.55), "#d8ceaf")
    mesh.box((0, 2.97, -2.9), (5.3, .12, 2.2), "#406f78")
    mesh.box((0, 3.14, -2.9), (4.7, .2, 1.7), "#8da8a4")
    for index in range(9):
        mesh.box((-2.3 + index * .57, 3.32, -2.9), (.1, .28, 1.85), "#d8c29b")
    for side in (-1, 1):
        for section in range(8):
            mesh.box((side * 5.8, .46, -3.4 + section), (.1, .65, 1), "#acc5bb")
            mesh.box((side * 5.8, .82, -3.4 + section), (.14, .08, 1), "#f0d4a0")
        for depth in (-3.5, -1.5, .5, 2.5, 3.7):
            mesh.box((side * 5.8, .48, depth), (.13, .9, .13), "#618b8c")
        mesh.plant(side * 5.15, -2.8, 1.25)
        mesh.plant(side * 5.15, 3.1, .9)
        mesh.box((side * 2.7, .26, .2), (.5, .4, 1.25), "#c9c5aa")
        mesh.plant(side * 2.7, .2, .7)
    for horizontal in (-4.9, 4.9):
        mesh.cylinder((horizontal, 0, 1.1), .12, 1.9, "#456f7c", sides=8)
        mesh.cylinder((horizontal, 1.9, 1.1), .22, .18, "#ffe1a0", .17, sides=8)
    mesh.box((-4, .15, -.7), (1.4, .2, 1.2), "#9bb88c")
    for index in range(5):
        mesh.plant(-4.45 + index * .23, -1.0, .4)
    mesh.box((-4, .43, -.4), (1.2, .14, .36), "#d7bb91")
    mesh.box((4.05, .15, -.85), (1.35, .22, 1.3), "#b6c8bc")
    mesh.cylinder((4.05, .25, -1.15), .045, 1.1, "#d8bf8e", sides=8)
    mesh.box((4.05, 1.4, -1.25), (.27, .27, .7), "#406f86")
    for horizontal in (3.05, 4.15):
        mesh.box((horizontal, .115, 2.5), (.04, .02, .9), "#e2d7b7")
    mesh.box((0, .46, 4.08), (11.7, .07, .08), "#b2cebd")
    return mesh.faces


def car_mesh(position, heading, clock):
    mesh = Mesh()
    mesh.box((0, .46, 0), (1.05, .38, 1.65), "#f2c978")
    mesh.box((0, .72, .59), (.94, .2, .5), "#db895e")
    mesh.box((0, .76, -.55), (.95, .27, .35), "#64a8ac")
    mesh.box((0, .77, -.13), (.82, .1, .52), "#36596e")
    for side in (-1, 1):
        mesh.box((side * .47, .77, -.03), (.1, .25, .93), "#eab26d")
        mesh.box((side * .34, .56, .84), (.22, .13, .035), "#fff5b0")
        mesh.box((side * .48, 1.05, -.65), (.045, .55, .045), "#d8d6bc")
        for depth in (-.51, .54):
            mesh.box((side * .55, .27, depth), (.16, .36, .37), "#273d4b")
            phase = clock * 9
            mesh.box((side * .641, .27 + math.sin(phase) * .07, depth + math.cos(phase) * .07), (.015, .065, .065), "#b4d6d0")
    mesh.box((0, 1.33, -.65), (1.02, .055, .055), "#f0e1b2")
    transformed = []
    for face in mesh.faces:
        vertices = [(position[0] + horizontal * math.cos(heading) + depth * math.sin(heading), vertical,
                     position[1] - horizontal * math.sin(heading) + depth * math.cos(heading)) for horizontal, vertical, depth in face.vertices]
        transformed.append(Face(tuple(vertices), face.color, face.light, face.double_sided))
    return transformed


class Projector:
    def __init__(self, width, height, camera, focus=(0, 1.4, 0)):
        yaw, pitch, distance = camera
        yaw, pitch = math.radians(yaw), math.radians(pitch)
        self.eye = (focus[0] + math.sin(yaw) * math.cos(pitch) * distance, focus[1] + math.sin(pitch) * distance,
                    focus[2] + math.cos(yaw) * math.cos(pitch) * distance)
        self.right = (math.cos(yaw), 0, -math.sin(yaw))
        self.up = (-math.sin(yaw) * math.sin(pitch), math.cos(pitch), -math.cos(yaw) * math.sin(pitch))
        self.forward = (-math.sin(yaw) * math.cos(pitch), -math.sin(pitch), -math.cos(yaw) * math.cos(pitch))
        self.width, self.height = width, height
        self.focal = min(width * 1.42, height * 1.65)

    def project(self, point):
        relative = [coordinate - eye for coordinate, eye in zip(point, self.eye)]
        depth = sum(coordinate * axis for coordinate, axis in zip(relative, self.forward))
        if depth <= .1:
            return None
        horizontal = sum(coordinate * axis for coordinate, axis in zip(relative, self.right))
        vertical = sum(coordinate * axis for coordinate, axis in zip(relative, self.up))
        return self.width / 2 + horizontal * self.focal / depth, self.height * .5 - vertical * self.focal / depth, depth


class WorldRenderer:
    def __init__(self):
        self.meshes = {}
        self.cache_key = None
        self.layers = None
        self.projected_key = None
        self.projected = []
        self.floor = None
        self.sky_key = None
        self.sky_image = None
        self.vehicle_key = None
        self.vehicle_faces = []

    def _prepare(self, snapshot):
        mesh_key = self._mesh_key(snapshot)
        if mesh_key not in self.meshes:
            faces = {"room": build_room, "terrace": build_terrace, "cloud": build_cloud}[snapshot["scene"]](snapshot["weather"])
            parked = snapshot.get("parked_vehicle")
            if snapshot["scene"] == "cloud" and parked:
                faces += car_mesh(parked["position"], parked["heading"], 0)
            vertices, materials = [], []
            for face in faces:
                start = len(vertices)
                vertices.extend(face.vertices)
                normal = np.cross(np.subtract(face.vertices[1], face.vertices[0]), np.subtract(face.vertices[2], face.vertices[0]))
                color = QColor(face.color)
                light = face.light * (.72 if snapshot["weather"] == "night" else .94 if snapshot["weather"] == "rain" else 1)
                color = QColor(min(255, int(color.red() * light)), min(255, int(color.green() * light)), min(255, int(color.blue() * light)))
                ceiling = max(point[1] for point in face.vertices)
                materials.append((start, len(vertices), normal, face.double_sided, ceiling, color))
            starts = np.array([material[0] for material in materials])
            counts = np.array([material[1] - material[0] for material in materials])
            normals = np.array([material[2] for material in materials])
            self.meshes[mesh_key] = np.asarray(vertices, dtype=float), materials, starts, counts, normals
            while len(self.meshes) > 24:
                self.meshes.pop(next(iter(self.meshes)))
        return self.meshes[mesh_key]

    @staticmethod
    def _mesh_key(snapshot):
        parked = snapshot.get("parked_vehicle") if snapshot["scene"] == "cloud" else None
        vehicle = (*parked["position"], parked["heading"]) if parked else None
        return snapshot["scene"], snapshot["weather"], vehicle

    def _project(self, width, height, snapshot, camera, quality, focus=(0, 1.4, 0)):
        projection = Projector(width, height, camera, focus)
        vertices, materials, starts, counts, normals = self._prepare(snapshot)
        relative = vertices - np.asarray(projection.eye)
        transformed = relative @ np.asarray((projection.right, projection.up, projection.forward)).T
        screen = transformed[:, :2] * (projection.focal / np.maximum(.1, transformed[:, 2:3]))
        screen[:, 0] += width / 2
        screen[:, 1] = height * .5 - screen[:, 1]
        facing = np.sum(normals * relative[starts], axis=1) > 0
        depths = np.add.reduceat(transformed[:, 2], starts) / counts
        visible = np.minimum.reduceat(transformed[:, 2], starts) > .1
        points = {}
        ground, polygons = [], []
        for index, (start, end, _, double_sided, ceiling, color) in enumerate(materials):
            if not double_sided and not facing[index]:
                continue
            if not visible[index]:
                continue
            depth = float(depths[index])
            corners = []
            for point in screen[start:end]:
                key = (float(point[0]), float(point[1]))
                if key not in points:
                    points[key] = QPointF(*key)
                corners.append(points[key])
            polygon = QPolygonF(corners)
            if ceiling <= .12:
                ground.append((ceiling, -depth, polygon, color))
            else:
                polygons.append((depth, polygon, color))
        polygons.sort(key=lambda item: item[0], reverse=True)
        self.floor = QImage(round(width * quality), round(height * quality), QImage.Format.Format_ARGB32_Premultiplied)
        self.floor.fill(0)
        painter = QPainter(self.floor)
        painter.scale(self.floor.width() / width, self.floor.height() / height)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, quality >= 1)
        painter.setPen(Qt.PenStyle.NoPen)
        for _, _, polygon, color in sorted(ground, key=lambda item: item[:2]):
            painter.setBrush(color)
            painter.drawPolygon(polygon)
        painter.end()
        self.projected = polygons

    def _layers(self, width, height, snapshot, camera, actor_depth, quality=1, focus=(0, 1.4, 0)):
        projected_key = (width, height, self._mesh_key(snapshot), tuple(round(value, 4) for value in (*camera, *focus)), quality)
        if self.projected_key != projected_key:
            self._project(width, height, snapshot, camera, quality, focus)
            self.projected_key = projected_key
        split = sum(depth >= actor_depth for depth, _, _ in self.projected)
        key = (projected_key, split)
        if key == self.cache_key:
            return self.layers
        layers = []
        for foreground in (False, True):
            image = QImage(self.floor.size(), QImage.Format.Format_ARGB32_Premultiplied) if foreground else self.floor.copy()
            if foreground:
                image.fill(0)
            painter = QPainter(image)
            painter.scale(image.width() / width, image.height() / height)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, quality >= 1)
            painter.setPen(Qt.PenStyle.NoPen)
            polygons = self.projected[split:] if foreground else self.projected[:split]
            for _, polygon, color in polygons:
                painter.setBrush(color)
                painter.drawPolygon(polygon)
            painter.end()
            layers.append(image)
        self.cache_key, self.layers = key, layers
        return layers

    def _sky_background(self, painter, width, height, weather):
        colors = {"clear": ("#6dabc3", "#d8e8dc"), "rain": ("#364e69", "#8ea9b3"),
                  "snow": ("#879eaf", "#e1ece8"), "night": ("#101e3c", "#466681")}
        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0, QColor(colors[weather][0]))
        gradient.setColorAt(1, QColor(colors[weather][1]))
        painter.fillRect(QRectF(0, 0, width, height), gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        if weather in ("clear", "night"):
            glow = QRadialGradient(width * .73, height * .23, height * .25)
            glow.setColorAt(0, QColor(255, 238, 185, 110))
            glow.setColorAt(1, QColor(255, 238, 185, 0))
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(width * .73, height * .23), height * .25, height * .25)
            painter.setBrush(QColor("#fff0c3"))
            painter.drawEllipse(QPointF(width * .73, height * .23), height * .048, height * .048)
        for layer in range(3):
            ridge = [QPointF(0, height)]
            for index in range(13):
                ridge.append(QPointF(index * width / 12, height * (.5 + layer * .08) + math.sin(index * 1.7 + layer) * height * .045))
            ridge.append(QPointF(width, height))
            painter.setBrush(QColor(("#477b95", "#6394a6", "#79a6af")[layer] if weather != "night" else ("#203750", "#28485e", "#335b70")[layer]))
            painter.drawPolygon(QPolygonF(ridge))
    def _sky(self, painter, width, height, weather, clock, particles):
        key = (width, height, weather)
        if key != self.sky_key:
            image = QImage(int(width), int(height), QImage.Format.Format_RGB32)
            canvas = QPainter(image)
            canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._sky_background(canvas, width, height, weather)
            canvas.end()
            self.sky_key, self.sky_image = key, image
        painter.drawImage(QPointF(), self.sky_image)
        if weather == "night" and particles:
            for index in range(42):
                alpha = int(130 + math.sin(clock * 1.2 + index) * 80)
                painter.setBrush(QColor(225, 245, 255, alpha))
                painter.drawEllipse(QPointF((index * 137.3) % width, (index * 71.7) % (height * .52)), 1.1, 1.1)
        if weather in ("rain", "snow") and particles:
            self._precipitation(painter, width, height, weather, clock)

    def _precipitation(self, painter, width, height, weather, clock, foreground=False):
        for index in range(35 if foreground else 65):
            speed = 145 + index % 5 * 20 if weather == "rain" else 19 + index % 5 * 4
            horizontal = (index * 97.31 + clock * (22 if weather == "rain" else 8) + math.sin(clock + index) * 7) % (width + 25) - 12
            vertical = (index * 53.17 + clock * speed) % (height + 25) - 12
            alpha = 70 if foreground else 140
            if weather == "rain":
                painter.setPen(QPen(QColor(204, 234, 247, alpha), 1))
                painter.drawLine(QPointF(horizontal, vertical), QPointF(horizontal + 3, vertical + 14))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(246, 254, 255, alpha + 50))
                radius = 1.4 + index % 3 * .6
                painter.drawEllipse(QPointF(horizontal, vertical), radius, radius)

    def draw(self, painter, scene, state, bounds, now, show_actor=True):
        settings = state["config"].get("world") or {}
        snapshot = state.get("world") or WorldDirector(settings).snapshot()
        extra = 0 if state.get("paused") or snapshot.get("paused") else max(0, now - state.get("world_at", now))
        current = view(snapshot, extra)
        left, top, width, height = bounds
        projection = Projector(width, height, current["camera"], current["focus"])
        horizontal, depth = current["position"]
        sleep_blend = min(1, current["activity_age"] / .8) if current["activity"] == "sleep" else 0
        sleep_blend = sleep_blend * sleep_blend * (3 - 2 * sleep_blend)
        elevation = .68 if current["activity"] in ("drive", "parked") else .08 + .82 * sleep_blend
        foot = projection.project((horizontal, elevation, depth))
        body = projection.project((horizontal, 1.35, depth))
        camera_move = snapshot["camera_move"]
        moving_camera = (camera_move["from"] != camera_move["to"] and camera_move["elapsed"] + extra < camera_move["duration"]) or (
            current["activity"] in ("sleep", "window", "lookout", "tea") and current["activity_age"] < 1.4)
        back, front = self._layers(width, height, snapshot, current["camera"], body[2], .8 if moving_camera else 1, current["focus"])
        painter.save()
        painter.setClipRect(QRectF(*bounds), Qt.ClipOperation.IntersectClip)
        painter.translate(left, top)
        rounded = QPainterPath()
        rounded.addRoundedRect(QRectF(0, 0, width, height), 20, 20)
        painter.setClipPath(rounded, Qt.ClipOperation.IntersectClip)
        clock = state.get("animation_time", 0)
        self._sky(painter, width, height, snapshot["weather"], clock, settings.get("particles", True))
        if snapshot["scene"] == "cloud":
            self._cloud_sea(painter, width, height, snapshot["weather"], clock if settings.get("particles", True) else 0)
        painter.drawImage(QRectF(0, 0, width, height), back)
        if snapshot["scene"] == "cloud":
            self._fountain(painter, projection, clock, settings.get("particles", True))
        vehicle = self._vehicle_faces(projection, current, clock if current["activity"] == "drive" else 0) if current["activity"] in ("drive", "parked") else []
        if show_actor:
            self._vehicle(painter, vehicle, body[2], foreground=False)
            self._actor(painter, scene, state, snapshot, extra, foot, projection, clock, now, current)
            self._vehicle(painter, vehicle, body[2], foreground=True)
        painter.drawImage(QRectF(0, 0, width, height), front)
        if snapshot["scene"] != "room" and snapshot["weather"] in ("rain", "snow") and settings.get("particles", True):
            self._precipitation(painter, width, height, snapshot["weather"], clock, foreground=True)
        if current["activity"] == "window":
            self._window_view(painter, scene, width, height, snapshot["weather"], clock, current["activity_age"], settings.get("particles", True))
        if current["activity"] in ACTIVITIES or current["activity"] == "parked":
            label = "已停靠 · 发“下车”继续走动" if current["activity"] == "parked" else ACTIVITIES[current["activity"]] + "  ·  发“停止移动”结束"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 43, 57, 190))
            painter.drawRoundedRect(QRectF(14, height - 43, min(340, width - 28), 29), 14, 14)
            scene.text(painter, (26, height - 43, min(318, width - 52), 29), label, 12, "#e9e5cb")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(11, 32, 48, 180))
        painter.drawRoundedRect(QRectF(12, 12, min(310, width - 24), 29), 14, 14)
        scene.text(painter, (24, 12, min(288, width - 48), 29), SCENES[snapshot["scene"]] + "  ·  " + WEATHERS[snapshot["weather"]], 13, "#e5f5ee")
        painter.restore()
    def _actor(self, painter, scene, state, snapshot, extra, foot, projection, clock, now, current=None):
        current = current or view(snapshot, extra)
        settings = state["config"].get("stage") or {}
        now = state.get("paused_at", now) if state.get("paused") else now
        scale = min(1.4, max(.6, float(settings.get("scale", .64)) / .64))
        height = min(projection.height * .82, 2.8 * projection.focal / foot[2] * scale)
        sleep_blend = min(1, current["activity_age"] / .8) if current["activity"] == "sleep" else 0
        sleep_blend = sleep_blend * sleep_blend * (3 - 2 * sleep_blend)
        if current["activity"] in ("drive", "parked"):
            height = 1.2 * projection.focal / foot[2]
        elif current["activity"] == "sleep":
            height += (1.95 * projection.focal / foot[2] - height) * sleep_blend
        width = height * scene.avatar.width() / scene.avatar.height()
        reaction = state["reaction"] if now < state.get("reaction_until", 0) else {}
        transition = snapshot["movement"]
        traveling = current["moving"]
        activity = current["activity"]
        if traveling or activity in ("sleep", "tea", "lookout", "parked"):
            motion = "drive" if activity == "parked" else activity if activity in ("run", "drive", "sleep", "tea", "lookout") else "travel"
            reaction = {"motion": motion, "started": now - transition["elapsed"] - extra, "duration": 86400,
                        "animation_id": str(snapshot["revision"]) + motion, "overlay": reaction}
        movement = pose(reaction.get("overlay") or reaction, now)
        if activity == "sleep":
            movement["eyes"] = .98
            movement["mouth"] = 0
        groove = music_view(state, now)
        beat = math.sin(groove["position"] * 3.6) if groove["playing"] and settings.get("native_music_motion", True) else 0
        center = foot[0] + movement["x"] * height / 520
        bottom = foot[1] - 4 + math.sin(clock * 1.5) * 3 + movement["y"] * height / 520
        if activity == "run":
            bottom -= abs(math.sin(current["activity_age"] * 9)) * height * .05
        if activity in ("sleep", "drive", "parked"):
            bottom = foot[1]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(14, 43, 51, 55))
        painter.drawEllipse(QPointF(foot[0], foot[1]), width * .35, height * .035)
        painter.save()
        pivot = .5 if reaction.get("motion") in ("handstand", "spin", "sleep") else .7
        lift = (1 - pivot) * (1 - sleep_blend) + .12 * sleep_blend
        painter.translate(center, bottom - height * lift)
        tilt = movement["tilt"] + beat * 2
        if traveling and activity != "drive":
            tilt += math.sin(current["heading"]) * (20 if activity == "run" else 12)
        if activity == "sleep":
            tilt = (-82 + math.sin(clock * 1.4) * 1.2) * sleep_blend
        elif activity == "drive":
            tilt = math.sin(clock * 7) * 1.2
        elif activity == "parked":
            tilt = 0
        painter.rotate(tilt)
        painter.scale((-1 if settings.get("flip") else 1) * movement["scale_x"], movement["scale_y"])
        painter.setOpacity(float(settings.get("opacity", 1)))
        scene.draw_character(painter, state, QRectF(-width / 2, -height * pivot, width, height), clock, now, reaction, movement)
        painter.restore()
        if activity == "sleep":
            for index in range(3):
                rise = (clock * .35 + index / 3) % 1
                scene.text(painter, (center - width * .6 + rise * 20, bottom - height * .35 - rise * 40, 28, 25), "Z", 12 + index * 2, "#e6f6ef")
        elif activity == "tea":
            cup = QPointF(center + width * .25, bottom - height * .43)
            painter.setPen(QPen(QColor("#e4d4ad"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cup.x() + 8, cup.y() - 3), 4, 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ede3c7"))
            painter.drawRoundedRect(QRectF(cup.x() - 5, cup.y() - 10, 12, 13), 3, 3)
            painter.setPen(QPen(QColor(230, 244, 231, 160), 1.2))
            for index in range(3):
                horizontal = center + width * .22 + index * 4
                painter.drawLine(QPointF(horizontal, bottom - height * .46), QPointF(horizontal + math.sin(clock * 2 + index) * 4, bottom - height * .56))
        elif activity == "run":
            painter.setPen(Qt.PenStyle.NoPen)
            for index in range(5):
                phase = (clock * 1.8 + index * .2) % 1
                painter.setBrush(QColor(223, 237, 217, int((1 - phase) * 85)))
                painter.drawEllipse(QPointF(foot[0] - math.sin(current["heading"]) * phase * 36, foot[1] - phase * 10), 3 + phase * 5, 2 + phase * 3)
        if settings.get("native_effects", True):
            draw_particles(painter, state["reaction"] if now < state.get("reaction_until", 0) else {},
                           (center - width, bottom - height, width * 2, height), now)

    def _vehicle_faces(self, projection, current, clock):
        key = None
        if current["activity"] == "parked":
            key = (projection.width, projection.height, projection.eye, projection.right, projection.up,
                   tuple(current["position"]), current["heading"])
            if key == self.vehicle_key:
                return self.vehicle_faces
        faces = []
        for face in car_mesh(current["position"], current["heading"], clock):
            normal = np.cross(np.subtract(face.vertices[1], face.vertices[0]), np.subtract(face.vertices[2], face.vertices[0]))
            if not face.double_sided and np.dot(normal, np.subtract(face.vertices[0], projection.eye)) <= 0:
                continue
            points = [projection.project(point) for point in face.vertices]
            if any(point is None for point in points):
                continue
            color = QColor(face.color)
            color = color.darker(round(100 / face.light))
            faces.append((sum(point[2] for point in points) / len(points), QPolygonF([QPointF(*point[:2]) for point in points]), color))
        faces.sort(key=lambda item: item[0], reverse=True)
        self.vehicle_key, self.vehicle_faces = key, faces
        return faces

    def _vehicle(self, painter, faces, depth, foreground):
        painter.setPen(Qt.PenStyle.NoPen)
        for distance, polygon, color in faces:
            if (distance < depth) == foreground:
                painter.setBrush(color)
                painter.drawPolygon(polygon)

    def _fountain(self, painter, projection, clock, enabled):
        if not enabled:
            return
        painter.setPen(QPen(QColor(199, 243, 243, 170), 1.3))
        for index in range(12):
            phase = (clock * .6 + index / 12) % 1
            angle = index * 2.399
            point = projection.project((math.cos(angle) * phase * .65, 1.1 + math.sin(phase * math.pi) * .7 - phase * .9, .25 + math.sin(angle) * phase * .4))
            if point:
                painter.drawLine(QPointF(point[0], point[1]), QPointF(point[0], point[1] + 3))

    def _cloud_sea(self, painter, width, height, weather, clock):
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(8):
            center = QPointF((index * width * .29 + clock * 3) % (width * 1.4) - width * .2,
                             height * (.57 + index % 3 * .13))
            radius = width * (.2 + index % 2 * .06)
            glow = QRadialGradient(center, radius)
            glow.setColorAt(0, QColor(185, 211, 223, 75) if weather == "night" else QColor(239, 250, 242, 125))
            glow.setColorAt(1, QColor(220, 241, 239, 0))
            painter.setBrush(glow)
            painter.drawEllipse(center, radius, height * .1)

    def _window_view(self, painter, scene, width, height, weather, clock, age, particles):
        progress = min(1, max(0, age / 1.4))
        painter.save()
        painter.setOpacity(progress)
        left, top = width * .07, height * .15
        pane_width, pane_height = width * .86, height * .48
        painter.translate(left, top)
        painter.setClipRect(QRectF(0, 0, pane_width, pane_height), Qt.ClipOperation.IntersectClip)
        self._sky_background(painter, pane_width, pane_height, weather)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(7):
            horizontal = pane_width * (.08 + index * .14)
            building_height = pane_height * (.17 + index % 3 * .07)
            painter.setBrush(QColor("#446d83" if weather != "night" else "#1c364e"))
            painter.drawRoundedRect(QRectF(horizontal, pane_height - building_height, pane_width * .065, building_height), 3, 3)
            painter.setBrush(QColor("#f5dda2"))
            for floor in range(3):
                painter.drawRect(QRectF(horizontal + 5, pane_height - building_height + 8 + floor * 10, pane_width * .045, 2))
        if particles and weather in ("rain", "snow"):
            self._precipitation(painter, pane_width, pane_height, weather, clock)
        painter.setPen(QPen(QColor("#dfd0ac"), 7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(3, 3, pane_width - 6, pane_height - 6))
        for fraction in (.33, .67):
            painter.drawLine(QPointF(pane_width * fraction, 0), QPointF(pane_width * fraction, pane_height))
        scene.text(painter, (16, 8, pane_width - 32, 26), "窗外 · 云海与远方", 14, "#fff4d2")
        painter.restore()
