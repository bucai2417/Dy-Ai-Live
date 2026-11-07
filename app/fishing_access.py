from __future__ import annotations

import re


def fishing_controller(event, config, metrics):
    if event is None or not config.get("chat_switch", True):
        return ""
    identifier = str(event.user_id or "").strip()
    administrators = {value for value in re.split(r"[\s,，;；]+", config.get("admin_ids", "")) if value}
    if event.is_admin is True or identifier and identifier in administrators:
        return "管理员"
    if not identifier:
        return ""
    mode = config.get("rank_control", "either")
    if mode in ("either", "fishing"):
        leaders = metrics.fishing_ranking(3)["ranking"]
        if any(row.get("key") == "id:" + identifier for row in leaders):
            return "今日钓鱼榜前三"
    if mode in ("either", "gifts"):
        if any(row.get("userId") == identifier for row in metrics.ranking(3, "value")):
            return "今日礼物价值榜前三"
    return ""
