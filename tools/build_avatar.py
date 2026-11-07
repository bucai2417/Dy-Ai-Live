from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SIZE = (983, 1407)
BONES = [
    ("root", None, (492, 885)), ("torso", "root", (0, -70)),
    ("head", "torso", (0, -205)),
    ("hair_left", "head", (-270, -45)), ("hair_right", "head", (270, -45)),
    ("upper_left", "torso", (-165, -115)), ("forearm_left", "upper_left", (-42, 105)),
    ("hand_left", "forearm_left", (-35, 113)),
    ("upper_right", "torso", (165, -115)), ("forearm_right", "upper_right", (42, 105)),
    ("hand_right", "forearm_right", (35, 113)),
    ("tail", "root", (0, 170)), ("fin_left", "tail", (-25, 145)),
    ("fin_right", "tail", (25, 145)),
]


def build(destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    source = QImage(str(ROOT / "web/assets/whale/art/deepy.png"))
    if source.isNull():
        raise ValueError("缺少 Deepy 原始立绘")
    origins = {}
    for name, parent, position in BONES:
        origin = origins.get(parent, (0, 0))
        origins[name] = (origin[0] + position[0], origin[1] + position[1])
    slots = []

    def save(name, bone, image, order):
        pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(image.height(), image.bytesPerLine() // 4, 4)
        vertical, horizontal = np.where(pixels[:, :image.width(), 3] > 0)
        if not horizontal.size:
            raise ValueError("空图层：" + name)
        left, top = int(horizontal.min()), int(vertical.min())
        width, height = int(horizontal.max()) - left + 1, int(vertical.max()) - top + 1
        relative = name + ".png"
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not image.copy(left, top, width, height).save(str(target)):
            raise OSError("图层保存失败：" + str(target))
        origin = origins[bone]
        return {"bone": bone, "file": relative, "offset": [left - origin[0], top - origin[1]], "order": order}

    def canvas():
        image = QImage(*SIZE, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def raster(name, bone, points, order):
        image = canvas()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clipping = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            clipping.lineTo(QPointF(*point))
        clipping.closeSubpath()
        painter.setClipPath(clipping)
        painter.drawImage(QRectF(0, 0, *SIZE), source)
        painter.end()
        slots.append({"name": name, **save("base/" + name, bone, image, order)})

    def vector(name, bone, body, order):
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="983" height="1407" viewBox="0 0 983 1407"><defs><linearGradient id="hair" x2=".8" y2="1"><stop stop-color="#cce8ff"/><stop offset=".55" stop-color="#93c8f0"/><stop offset="1" stop-color="#76a4dd"/></linearGradient><linearGradient id="skin" x2="1" y2=".6"><stop stop-color="#fff1e9"/><stop offset="1" stop-color="#f0b8aa"/></linearGradient><linearGradient id="glove" x2="1" y2="1"><stop stop-color="#b2dcff"/><stop offset="1" stop-color="#4f83d2"/></linearGradient></defs><g stroke="#4167a7" stroke-width="4" stroke-linejoin="round" stroke-linecap="round">{body}</g></svg>'
        image = canvas()
        painter = QPainter(image)
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            raise ValueError("无效 SVG：" + name)
        renderer.render(painter)
        painter.end()
        return save(name, bone, image, order)

    hair_points = [(0, 530), (220, 520), (325, 580), (350, 655), (330, 715), (300, 760),
                   (288, 806), (252, 853), (220, 890), (182, 927), (189, 964), (230, 990),
                   (227, 1013), (208, 1034), (175, 1032), (100, 1025), (0, 930)]
    for side in ("left", "right"):
        points = hair_points if side == "left" else [(983 - horizontal, vertical) for horizontal, vertical in hair_points]
        raster("hair_" + side, "hair_" + side, points, 0)
    raster("tail", "tail", [(385, 1060), (600, 1060), (615, 1188), (550, 1250), (435, 1250), (370, 1188)], 1)
    raster("fin_left", "fin_left", [(492, 1165), (492, 1407), (0, 1407), (0, 1180)], 2)
    raster("fin_right", "fin_right", [(491, 1165), (983, 1180), (983, 1407), (491, 1407)], 2)
    body = '<path fill="url(#skin)" d="M451 590 L533 590 L538 661 Q645 676 657 725 L623 845 L646 981 Q492 1040 339 981 L360 845 L327 725 Q346 675 447 661Z"/><path fill="#a9cfff" d="M361 829 Q493 859 623 829 L642 980 Q492 1030 341 980Z"/>'
    slots.append({"name": "body", **vector("base/body", "torso", body, 3)})
    for side, flip in (("left", ""), ("right", 'transform="translate(984 0) scale(-1 1)"')):
        upper = f'<g {flip}><path fill="url(#skin)" d="M329 672 Q356 677 359 708 L322 811 Q306 830 278 813 Q265 802 273 780 L303 692Z"/></g>'
        lower = f'<g {flip}><path fill="url(#skin)" d="M282 787 Q310 783 321 808 L287 923 Q276 942 250 927 Q236 915 242 894 L265 809Z"/></g>'
        hand = f'<g {flip}><path fill="url(#glove)" d="M245 895 Q264 883 281 906 Q291 936 274 957 Q261 965 250 947 Q219 967 205 955 Q191 942 217 923 Q199 920 209 907 Q224 895 245 895Z"/><path fill="none" stroke="#cbe7ff" stroke-width="5" d="M220 935 L244 921"/></g>'
        for name, bone, content, order in (("arm_", "upper_", upper, 9), ("forearm_", "forearm_", lower, 10), ("hand_", "hand_", hand, 12)):
            slots.append({"name": name + side, **vector("base/" + name + side, bone + side, content, order)})
    raster("head", "head", [(0, 0), (983, 0), (983, 615), (680, 615), (600, 608), (535, 631), (447, 631), (367, 606), (300, 615), (0, 615)], 15)
    skins = {}
    for skin, label, main, shade, trim in (
        ("sailor", "蓝海水手服", "#eef7ff", "#8abcf1", "#204b9d"),
        ("raincoat", "柠檬雨衣", "#ffe27d", "#efa936", "#327b8b"),
        ("starry", "星空礼服", "#686cc6", "#272c69", "#ffe2a0"),
    ):
        definitions = f'<defs><linearGradient id="cloth" x2=".8" y2="1"><stop stop-color="{main}"/><stop offset="1" stop-color="{shade}"/></linearGradient></defs>'
        if skin == "raincoat":
            torso = f'<path fill="url(#cloth)" d="M429 647 Q490 684 555 647 L637 685 L634 874 Q493 900 350 874 L348 685Z"/><path fill="{trim}" d="M429 648 L492 706 L453 747 L409 667Z M555 648 L492 706 L530 747 L578 667Z"/><path fill="none" stroke="{trim}" stroke-width="7" d="M492 708 L492 878"/>'
            skirt = f'<path fill="url(#cloth)" d="M351 850 Q492 878 634 850 L701 1067 Q492 1138 282 1067Z"/><path fill="none" stroke="{trim}" stroke-width="9" d="M295 1054 Q492 1111 688 1054"/><path fill="{main}" d="M339 949 L414 961 L410 1017 L329 1000Z M570 961 L645 949 L656 1000 L574 1017Z"/><path stroke="{trim}" stroke-width="7" d="M492 864 L492 1105"/>'
        elif skin == "starry":
            torso = f'<path fill="url(#cloth)" d="M422 656 Q492 699 561 656 L636 698 L616 869 Q492 908 369 869 L348 698Z"/><path fill="{trim}" d="M420 655 L492 710 L563 655 L547 704 L492 737 L438 704Z"/><path fill="none" stroke="{trim}" stroke-width="5" d="M386 724 Q492 816 599 724"/>'
            skirt = f'<path fill="url(#cloth)" d="M370 846 Q492 875 616 846 Q645 974 741 1093 Q492 1181 243 1093 Q337 974 370 846Z"/><path fill="{main}" opacity=".55" d="M379 865 Q368 989 294 1100 L428 1130 L492 867 L558 1130 L693 1100 Q621 990 605 865Z"/><path fill="none" stroke="{trim}" stroke-width="7" d="M258 1093 Q492 1170 726 1093"/>'
            for horizontal, vertical in ((371, 1010), (586, 1038), (474, 1080), (531, 950)):
                skirt += f'<path fill="{trim}" stroke="none" d="M{horizontal} {vertical-14} l4 10 11 4 -11 4 -4 10 -4 -10 -11 -4 11 -4Z"/>'
        else:
            torso = f'<path fill="url(#cloth)" d="M420 649 Q492 687 564 649 L639 692 L616 860 Q492 891 369 860 L345 692Z"/><path fill="{trim}" d="M417 649 L492 708 L565 649 L596 678 L492 756 L387 678Z"/><path fill="none" stroke="#edf6ff" stroke-width="5" d="M417 663 L492 730 L568 663"/><path fill="#629ce3" d="M480 745 L505 745 L526 819 L492 848 L461 819Z"/><circle fill="#eaf6ff" cx="576" cy="770" r="28"/><path fill="#427dc2" stroke="none" d="M557 765 Q577 743 592 766 Q594 785 573 783 L554 772 L548 777 L550 763Z"/>'
            skirt = f'<path fill="url(#cloth)" d="M370 844 Q492 872 615 844 L700 1068 Q492 1140 283 1068Z"/><path fill="none" stroke="#6196d1" stroke-width="3" d="M414 866 L383 1094 M464 877 L451 1106 M521 877 L534 1106 M570 866 L601 1094"/><path fill="none" stroke="{trim}" stroke-width="11" d="M295 1055 Q492 1120 688 1055"/><path fill="{trim}" d="M370 842 Q492 866 616 842 L624 875 Q492 907 361 875Z"/>'
        attachments = {}
        attachments["clothes"] = vector(f"skins/{skin}/clothes", "torso", definitions + torso, 5)
        attachments["skirt"] = vector(f"skins/{skin}/skirt", "root", definitions + skirt, 6)
        for side, flip in (("left", ""), ("right", 'transform="translate(984 0) scale(-1 1)"')):
            end = 799 if skin == "raincoat" else 757
            sleeve = f'<g {flip}><path fill="url(#cloth)" d="M312 675 Q338 663 365 693 L344 {end} Q311 {end+14} 277 {end-14} L292 696Z"/><path fill="none" stroke="{trim}" stroke-width="10" d="M281 {end-16} Q314 {end+7} 341 {end-3}"/></g>'
            attachments["sleeve_" + side] = vector(f"skins/{skin}/sleeve_{side}", "upper_" + side, definitions + sleeve, 11)
        skins[skin] = {"label": label, "slots": attachments}
    model = {"schema_version": 1, "id": "deepy-layered-v1", "canvas": list(SIZE),
             "bones": [{"name": name, "parent": parent, "position": list(position)} for name, parent, position in BONES],
             "slots": slots, "skins": skins}
    (destination / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(slots)} 个基础图层、{len(skins)} 套服装：{destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从现有立绘和矢量图层构建原生 Deepy 模型")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/avatars/deepy")
    arguments = parser.parse_args()
    application = QGuiApplication.instance() or QGuiApplication([])
    build(arguments.output)
