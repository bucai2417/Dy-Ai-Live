from __future__ import annotations

import copy
import math
import re
import time
import uuid


KINDS = {"idiom": "成语接龙", "drawing": "AI画你猜"}
PALETTE = ("#24364b", "#e27e68", "#e9b85d", "#72b99d", "#689ec7", "#a28ac4", "#f6f0df", "#ffffff")


def answer_text(text):
    text = re.sub(r"[\s，,。.!！?？：:、~～]", "", str(text or ""))
    text = re.sub(r"^(?:我猜是|我猜|答案是|答案|这是|是)", "", text)
    return text.casefold()


def idiom_text(text):
    value = re.sub(r"[\s，,。.!！?？：:、~～]", "", str(text or ""))
    value = re.sub(r"^(?:我猜是|我猜|答案是|答案)", "", value)
    return value if re.fullmatch(r"[\u3400-\u9fff]{4}", value) else ""


def validate_drawing(value):
    if not isinstance(value, list) or not 3 <= len(value) <= 48:
        raise ValueError("画作需包含3到48笔")
    strokes = []
    for item in value:
        if not isinstance(item, dict) or item.get("shape") not in ("line", "ellipse", "rect"):
            raise ValueError("画作包含不支持的图形")
        points = item.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 40:
            raise ValueError("画作坐标过多或缺失")
        if item["shape"] != "line" and len(points) != 2:
            raise ValueError("矩形和椭圆需要两个角坐标")
        for point in points:
            if (not isinstance(point, list) or len(point) != 2 or any(isinstance(number, bool)
                    or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 100 for number in point)):
                raise ValueError("画作坐标不在画布内")
        color, fill = item.get("color", PALETTE[0]), item.get("fill", "none")
        if color not in PALETTE or fill not in (*PALETTE, "none"):
            raise ValueError("画作颜色不在允许范围")
        strokes.append({"shape": item["shape"], "points": copy.deepcopy(points), "color": color, "fill": fill})
    return strokes


def validate_question(kind, result):
    if not isinstance(result, dict):
        raise ValueError("AI没有返回题目")
    if kind == "idiom":
        word = idiom_text(result.get("word"))
        meaning = result.get("meaning")
        if not word or word != result.get("word") or not isinstance(meaning, str) or not 2 <= len(meaning) <= 100:
            raise ValueError("成语题目格式无效")
        return {"word": word, "meaning": meaning}
    answer = result.get("answer")
    aliases, hints = result.get("aliases", []), result.get("hints", [])
    if not isinstance(answer, str) or not re.fullmatch(r"[\u3400-\u9fff]{2,6}", answer):
        raise ValueError("猜画答案需为2到6个汉字")
    if (not isinstance(aliases, list) or len(aliases) > 6 or any(not isinstance(word, str) or not 1 <= len(word) <= 12 for word in aliases)
            or not isinstance(hints, list) or len(hints) != 2 or any(not isinstance(hint, str) or not 2 <= len(hint) <= 45 for hint in hints)):
        raise ValueError("猜画提示格式无效")
    allowed = list(dict.fromkeys([answer_text(answer), *(answer_text(word) for word in aliases)]))
    if any(not word or not re.fullmatch(r"[\u3400-\u9fff]{1,12}", word) for word in allowed):
        raise ValueError("猜画同义名称无效")
    if any(word in hint for word in allowed if word for hint in hints):
        raise ValueError("画作提示泄露了答案")
    return {"answer": answer, "aliases": allowed, "hints": hints, "strokes": validate_drawing(result.get("strokes"))}


class MiniGame:
    def __init__(self):
        self.active = False
        self.paused_at = None
        self.kind = ""
        self.phase = "idle"
        self.identifier = ""
        self.question_id = ""
        self.question = {}
        self.used = []
        self.round = 0
        self.total = 0
        self.deadline = 0
        self.started = 0
        self.feedback = ""
        self.winner = None
        self.scores = {}
        self.seen = {}
        self.ready_at = 0
        self.owner = ""
        self.config = {}
        self.correct = False
        self.final = False

    def clock(self, now=None):
        return self.paused_at if self.paused_at is not None else time.monotonic() if now is None else now

    def start(self, kind, config, owner="", now=None):
        if kind not in KINDS:
            raise ValueError("未知小游戏")
        now = self.clock(now)
        self.active, self.kind = True, kind
        self.identifier = uuid.uuid4().hex
        self.question_id = uuid.uuid4().hex
        self.config = dict(config)
        self.owner = owner
        self.phase, self.round, self.total = "preparing", 1, int(config["rounds"])
        self.question, self.used, self.scores, self.seen = {}, [], {}, {}
        self.winner = None
        self.feedback = "AI正在出题，请稍候…"
        self.correct = self.final = False
        self.started, self.deadline = now, now + 40

    def install(self, question, now=None):
        now = self.clock(now)
        self.question = question
        if self.kind == "idiom":
            self.used.append(question["word"])
        self.phase, self.started = "question", now
        self.deadline = now + self.config["round_seconds"]
        self.feedback = "同字接龙，不接受谐音；AI核验成语" if self.kind == "idiom" else "直接发弹幕猜答案，也可以说“我猜是…”"
        self.winner = None
        self.correct = self.final = False

    def pause(self, paused, now=None):
        now = time.monotonic() if now is None else now
        if paused and self.paused_at is None:
            self.paused_at = now
        elif not paused and self.paused_at is not None:
            delay = now - self.paused_at
            self.started += delay
            self.deadline += delay
            self.paused_at = None

    def finish(self, text, winner=None, answer="", now=None):
        now = self.clock(now)
        self.phase, self.feedback, self.winner = "result", text, winner
        self.correct = winner is not None
        self.final = self.round >= self.total or not self.correct
        if self.kind == "idiom" and answer:
            self.question = {"word": answer, "meaning": text}
            self.used.append(answer)
        self.started, self.deadline = now, now + 8

    def award(self, user, answer, meaning="", now=None):
        key = user.get("id") or "name:" + user["name"]
        row = self.scores.setdefault(key, {"id": user.get("id", ""), "name": user["name"], "score": 0, "correct": 0})
        points = 10 if self.kind == "idiom" else max(10, 30 - int((self.clock(now) - self.started) / 5) * 2)
        row["score"] += points
        row["correct"] += 1
        row["name"] = user["name"]
        self.finish(f"{user['name']} 答对了！+{points}分" + (" · " + meaning if meaning else ""), user, answer, now)
        if self.kind == "idiom":
            self.question["meaning"] = meaning
        return points

    def stop(self, now=None):
        self.active, self.phase = False, "idle"
        self.ready_at = (time.monotonic() if now is None else now) + 10
        self.identifier = uuid.uuid4().hex
        self.paused_at = None

    def tick(self, now=None):
        if not self.active or self.paused_at is not None:
            return ""
        now = self.clock(now)
        if now < self.deadline:
            return ""
        if self.phase == "preparing":
            self.finish("AI出题超时，本轮结束；可以稍后再开局", now=now)
            return "changed"
        if self.phase == "question":
            self.finish("时间到啦，这轮没有人答对", now=now)
            return "changed"
        if self.phase == "result":
            if self.final:
                self.stop(now)
                return "stopped"
            self.round += 1
            self.question_id = uuid.uuid4().hex
            if self.kind == "idiom":
                self.phase, self.started = "question", now
                self.deadline = now + self.config["round_seconds"]
                self.feedback = "接上一个成语的最后一个字，同局不能重复"
                self.winner = None
                return "changed"
            self.phase, self.started, self.deadline = "preparing", now, now + 40
            self.question = {}
            self.feedback = "AI正在画下一题…"
            return "prepare"
        return ""

    def snapshot(self, now=None):
        now = self.clock(now)
        question = {}
        if self.kind == "idiom" and self.question:
            question = {"word": self.question["word"], "meaning": self.question["meaning"], "required": self.question["word"][-1]}
        elif self.question:
            age = max(0, now - self.started)
            question = {"strokes": self.question["strokes"], "letters": len(self.question["answer"]),
                        "hint": self.question["hints"][1 if age >= self.config["round_seconds"] * .7 else 0] if age >= self.config["round_seconds"] * .4 else ""}
            if self.phase == "result":
                question["answer"] = self.question["answer"]
        return {"active": self.active, "id": self.identifier, "question_id": self.question_id, "kind": self.kind,
                "label": KINDS.get(self.kind, "小游戏"), "phase": self.phase, "round": self.round, "total": self.total,
                "remaining": max(0, self.deadline - now), "elapsed": max(0, now - self.started), "paused": self.paused_at is not None,
                "question": copy.deepcopy(question), "feedback": self.feedback, "winner": copy.deepcopy(self.winner),
                "scores": copy.deepcopy(sorted(self.scores.values(), key=lambda row: -row["score"])[:5]), "final": self.final,
                "draw_seconds": self.config.get("draw_seconds", 12)}
