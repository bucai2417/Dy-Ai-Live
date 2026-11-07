from __future__ import annotations

import asyncio
import copy
import json
import math
import shutil
from unittest.mock import AsyncMock

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from app import settings_store
from app.brain.intent import decode_intent, local_intent
from app.native.actions import SKINS
from app.native.avatar.rig import LayeredAvatar, MODEL_PATH, joint_pose
from app.native.scene import Scene
from app.native.state import StageState
from app.settings_store import Effective, SettingsStore, store
from app.wardrobe import wear
from tests.test_renderer import qt
from tests.test_runtime import isolated_runtime


def test_child_joint_inherits_parent_transform_and_hand_actually_raises(qt):
    rig = LayeredAvatar()
    resting = rig.transforms({})
    raised = rig.transforms({"upper_right": -100})
    wrist = resting["hand_right"].map(QPointF())
    raised_wrist = raised["hand_right"].map(QPointF())
    assert raised_wrist.y() < wrist.y() - 130
    assert raised["hand_left"] == resting["hand_left"]
    for name in ("forearm_right", "hand_right"):
        parent = rig.bones[name][0]
        point, origin = raised[name].map(QPointF()), raised[parent].map(QPointF())
        assert math.hypot(point.x() - origin.x(), point.y() - origin.y()) == pytest.approx(math.hypot(*rig.bones[name][1]))


def test_actions_return_to_idle_without_mutating_other_channels():
    reaction = {"motion": "wave", "started": 10, "duration": 4}
    assert joint_pose(reaction, 10, 1) == joint_pose({}, 10, 1)
    assert joint_pose(reaction, 14, 1) == joint_pose({}, 14, 1)
    assert joint_pose(reaction, 11, 1)["upper_right"] < -80
    assert "mouth" not in joint_pose(reaction, 11, 1)


def test_interrupting_action_blends_without_joint_teleport(qt):
    rig = LayeredAvatar()
    wave = {"motion": "wave", "started": 0, "duration": 4}
    before = rig.animated_angles(wave, 1, 1)
    replacement = {"motion": "stretch", "started": 1, "duration": 5}
    assert rig.animated_angles(replacement, 1, 1) == before
    after = rig.animated_angles(replacement, 1.3, 1.3)
    expected = joint_pose(replacement, 1.3, 1.3)
    assert after["upper_left"] == pytest.approx(expected["upper_left"])
    assert after["upper_left"] != before.get("upper_left", 0)


@pytest.mark.parametrize("problem", ["cycle", "unknown_parent", "missing_image", "outside", "partial_skin", "nan"])
def test_invalid_model_is_rejected_before_display(qt, tmp_path, problem):
    directory = tmp_path / "model"
    shutil.copytree(MODEL_PATH.parent, directory)
    data = json.loads((directory / "model.json").read_text(encoding="utf-8"))
    if problem == "cycle":
        data["bones"][0]["parent"] = "head"
    elif problem == "unknown_parent":
        data["bones"][0]["parent"] = "missing"
    elif problem == "missing_image":
        data["skins"]["raincoat"]["slots"]["clothes"]["file"] = "missing.png"
    elif problem == "outside":
        data["slots"][0]["file"] = "../outside.png"
    elif problem == "partial_skin":
        del data["skins"]["starry"]["slots"]["sleeve_left"]
    else:
        data["bones"][0]["position"][0] = float("nan")
    (directory / "model.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        LayeredAvatar(directory / "model.json")


def draw_avatar(scene, state, skin, mouth=0):
    state["outfit"] = skin
    state["mouth"] = mouth
    state["mouth_at"] = 10
    image = QImage(492, 704, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        anchors = scene.draw_character(painter, state, QRectF(0, 0, 492, 704), 1, 10,
                                       {"motion": "wave", "started": 9, "duration": 4})
    finally:
        painter.end()
    return image, anchors


def test_real_clothing_replaces_layers_and_preserves_head_pose_and_mouth(qt):
    scene = Scene(720, 1280)
    state = StageState().snapshot()
    sailor, initial = draw_avatar(scene, state, "sailor")
    raincoat, changed = draw_avatar(scene, state, "raincoat")
    assert initial == changed
    assert sailor.copy(0, 0, 492, 290) == raincoat.copy(0, 0, 492, 290)
    assert sailor.copy(150, 330, 190, 190) != raincoat.copy(150, 330, 190, 190)
    speaking, _ = draw_avatar(scene, state, "raincoat", mouth=1)
    assert speaking != raincoat
    for skin in SKINS:
        slots = scene.rig.skins[skin]
        assert len([slot for slot in slots if slot[0] == "clothes"]) == 1
        assert len([slot for slot in slots if slot[0] == "skirt"]) == 1


def test_outfit_and_accessory_save_and_restore_together(tmp_path):
    async def scenario():
        state = StageState()
        bus = AsyncMock()
        bus.broadcast.side_effect = lambda message, **kwargs: state.apply(message)
        await wear("starry", bus)
        await wear("cool", bus)
        await wear("raincoat", bus)
        assert state.outfit == "raincoat" and state.costume == "cool"
        persisted = SettingsStore().section("stage")
        assert persisted["outfit"] == "raincoat" and persisted["costume"] == "cool"
        reconnected = StageState()
        reconnected.apply({"type": "hello", "config": {"stage": Effective.stage()}})
        assert reconnected.outfit == "raincoat" and reconnected.costume == "cool"
        await wear("original", bus)
        assert state.outfit == "sailor" and state.costume == "original"

    asyncio.run(scenario())


def test_failed_save_does_not_broadcast_false_success(monkeypatch):
    async def scenario():
        previous = copy.deepcopy(store._data)
        bus = AsyncMock()
        monkeypatch.setattr(store, "save", lambda: (_ for _ in ()).throw(OSError("写入失败")))
        with pytest.raises(OSError):
            await wear("raincoat", bus)
        assert store._data == previous
        bus.broadcast.assert_not_awaited()

    asyncio.run(scenario())


def test_original_image_mode_reports_unavailable_outfit():
    async def scenario():
        store._data["stage"]["avatar_mode"] = "image"
        bus = AsyncMock()
        with pytest.raises(ValueError, match="分层骨架"):
            await wear("raincoat", bus)
        bus.broadcast.assert_not_awaited()
        await wear("cool", bus)
        assert Effective.stage()["costume"] == "cool"

    asyncio.run(scenario())


def test_pause_freezes_animation_and_resumes_same_joint_pose(monkeypatch):
    from app.native import state as state_module

    clock = [100.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: clock[0])
    state = StageState()
    state.apply({"type": "reaction", "motion": "wave", "manual": True})
    clock[0] = 101
    state.apply({"type": "state", "paused": True})
    before = state.snapshot()
    clock[0] = 180
    during = state.snapshot()
    assert before["animation_time"] == during["animation_time"]
    state.apply({"type": "state", "paused": False})
    after = state.snapshot()
    assert after["animation_time"] == before["animation_time"]
    assert joint_pose(before["reaction"], 101, before["animation_time"]) == joint_pose(after["reaction"], 180, after["animation_time"])


def test_missing_rig_keeps_original_image_working(qt, monkeypatch):
    from app.native import scene as scene_module

    def missing():
        raise ValueError("模型文件缺失")

    monkeypatch.setattr(scene_module, "LayeredAvatar", missing)
    scene = Scene(720, 1280)
    assert scene.rig is None and scene.avatar_error
    assert not scene.render(StageState().snapshot(), 1, 1).isNull()


@pytest.mark.parametrize("text,expected", [("换衣服", "next_outfit"), ("穿上星空礼服", "starry"),
                                           ("换回原装", "original"), ("穿水手服", "sailor")])
def test_natural_outfit_commands(text, expected):
    assert local_intent(text).motion == expected
    assert decode_intent(json.dumps({"action": "costume", "motion": expected, "confidence": .99})).motion == expected
    assert local_intent("不要" + text).action == "chat"


def test_tail_command_and_invalid_settings():
    assert local_intent("摆尾巴").motion == "tail_sway"
    with pytest.raises(ValueError):
        store.update({"stage": {"outfit": "unknown"}})
    with pytest.raises(ValueError):
        store.update({"stage": {"avatar_mode": "browser"}})
