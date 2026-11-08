from __future__ import annotations

import copy

from .fishing_catalog import FISH, FISH_KINDS, ITEM_KINDS


class FishingBoard:
    def __init__(self):
        self.records = {}
        self.rounds = set()
        self.community = {"count": 0, "value": 0}
        self.encounters = {}
        self.species = {}

    def clear(self):
        self.records.clear()
        self.rounds.clear()
        self.community = {"count": 0, "value": 0}
        self.encounters.clear()
        self.species.clear()

    def encounter(self, round_id, fish):
        kind = (fish or {}).get("kind")
        if not round_id or round_id in self.encounters or kind not in FISH:
            return False
        self.encounters[round_id] = kind
        self.species[kind] = self.species.get(kind, 0) + 1
        return True

    def add(self, round_id, fish, winner):
        if not round_id or round_id in self.rounds:
            return False
        value = self._count(fish["value"])
        self.rounds.add(round_id)
        if not winner:
            self.community["count"] += 1
            self.community["value"] += value
            return True
        name = str(winner.get("name") or "观众")[:80]
        user_id = str(winner.get("id") or "")[:100]
        key = "id:" + user_id if user_id else "name:" + name
        record = self.records.setdefault(key, {"key": key, "name": name, "value": 0, "count": 0,
                                               "best": 0, "fish": "", "kind": "", "order": len(self.rounds)})
        record["name"] = name
        record["value"] += value
        record["count"] += 1
        if value > record["best"]:
            record.update(best=value, fish=fish["name"], kind=fish["kind"])
        return True

    def snapshot(self, limit=10):
        ranked = sorted(self.records.values(), key=lambda record: (-record["value"], record["order"]))
        return {"ranking": [dict(record, rank=index + 1) for index, record in enumerate(ranked[:limit])],
                "players": len(ranked), "catches": len(self.rounds), "community": dict(self.community),
                "species": [{"kind": kind, "name": FISH[kind]["name"], "count": count} for kind, count in self.species.items() if kind in FISH_KINDS],
                "items": [{"kind": kind, "name": FISH[kind]["name"], "count": count} for kind, count in self.species.items() if kind in ITEM_KINDS],
                "encounters": len(self.encounters), "species_total": len(FISH_KINDS), "items_total": len(ITEM_KINDS),
                "rule": "今日累计鱼获价值；鱼竿收获归拉钩、实名点赞与礼物合计贡献最高者，同分先参与优先；礼物撒网鱼获全部归送礼者"}

    def export(self):
        return copy.deepcopy({"records": self.records, "rounds": sorted(self.rounds), "community": self.community,
                              "encounters": self.encounters})

    @staticmethod
    def _count(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("无效的鱼获分数")
        return value

    @classmethod
    def restore(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("records", {}), dict):
            raise ValueError("钓鱼榜数据格式错误")
        board = cls()
        encounters = data.get("encounters", {})
        if not isinstance(encounters, dict):
            raise ValueError("鱼种记录格式错误")
        for round_id, kind in encounters.items():
            if not isinstance(round_id, str) or not isinstance(kind, str) or kind not in FISH:
                raise ValueError("鱼种记录无效")
            board.encounter(round_id, {"kind": kind})
        rounds = data.get("rounds", [])
        if not isinstance(rounds, list) or any(not isinstance(round_id, str) for round_id in rounds):
            raise ValueError("钓鱼回合记录错误")
        board.rounds = set(rounds)
        community = data.get("community", {})
        if not isinstance(community, dict):
            raise ValueError("全场协力记录错误")
        board.community = {key: cls._count(community.get(key, 0)) for key in ("count", "value")}
        for key, record in data.get("records", {}).items():
            if (not isinstance(record, dict) or not isinstance(record.get("name"), str)
                    or record.get("kind") not in FISH or not isinstance(record.get("fish"), str)):
                raise ValueError("钓鱼榜用户记录错误")
            board.records[key] = {"key": key, "name": record["name"][:80], "fish": record["fish"], "kind": record["kind"],
                                  **{field: cls._count(record.get(field, 0)) for field in ("value", "count", "best", "order")}}
        return board
