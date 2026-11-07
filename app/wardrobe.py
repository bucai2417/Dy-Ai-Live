from __future__ import annotations

from .native.actions import ACCESSORIES, COSTUMES, SKINS
from .settings_store import Effective, store


async def wear(name, hub, **details):
    if name not in COSTUMES and name not in ("next_outfit", "next_accessory"):
        raise ValueError("未知人物装扮")
    stage = Effective.stage()
    if name in ("next_outfit", "next_accessory"):
        choices = tuple(SKINS) if name == "next_outfit" else tuple(key for key in ACCESSORIES if key != "original")
        current = stage.get("outfit" if name == "next_outfit" else "costume")
        name = choices[(choices.index(current) + 1) % len(choices)] if current in choices else choices[0]
    if name in SKINS and stage["avatar_mode"] != "layered":
        raise ValueError("请在设置中开启分层骨架后再换衣服")
    patch = {"outfit": name} if name in SKINS else {"costume": name}
    if name == "original":
        patch["outfit"] = "sailor"
    if any(stage.get(key) != value for key, value in patch.items()):
        store.update({"stage": patch})
    await hub.broadcast({"type": "config_changed", "config": {"stage": Effective.stage()}}, remember=False)
    await hub.broadcast({"type": "costume", "name": name, **details}, remember=False)
    return name
