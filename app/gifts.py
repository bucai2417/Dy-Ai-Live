from __future__ import annotations

import heapq
import time


MAX_GIFT_BUBBLES = 10


class GiftBoard:
    def __init__(self):
        self.records = {}

    def add(self, event: dict):
        name = str(event.get("userName") or "观众").strip()[:80] or "观众"
        identifier = str(event.get("userId") or "")
        key = "id:" + identifier if identifier else "name:" + name
        row = self.records.setdefault(key, {"key": key, "userId": identifier, "name": name,
            "avatar": "", "value": 0, "count": 0, "order": len(self.records), "ts": 0.0})
        row["name"] = name
        row["value"] += max(0, int(event.get("giftValue") or 0))
        row["count"] += max(1, int(event.get("giftCount") or 1))
        row["ts"] = time.time()
        if event.get("avatar"):
            row["avatar"] = str(event["avatar"])[:2048]
        return row

    def ranking(self, limit=10, by="value"):
        metric = "count" if by == "count" else "value"
        selected = heapq.nsmallest(max(0, min(MAX_GIFT_BUBBLES, limit)), self.records.values(),
                                  key=lambda row: (-row[metric], row["order"]))
        return [{**row, "rank": index + 1} for index, row in enumerate(selected)]

    def snapshot(self):
        return {"value": self.ranking(by="value"), "count": self.ranking(by="count")}

    def clear(self):
        self.records.clear()
