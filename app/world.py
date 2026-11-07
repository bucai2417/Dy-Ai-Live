from __future__ import annotations

import math
import time
import copy

from .world_navigation import BOUNDS, LANDMARKS, path_sample, promenade, route, walkable


SCENES = {"room": "海风会客厅", "terrace": "星海露台", "cloud": "云上天境 · 景观云台"}
SCENE_COMMANDS = {"next": "换个场景", **SCENES}
WEATHERS = {"clear": "晴空", "rain": "听雨", "snow": "飘雪", "night": "星夜"}
CAMERAS = {"front": "全景", "left": "左侧视角", "right": "右侧视角", "close": "人物近景", "overhead": "俯视"}
MOVES = {"left": "向左游", "right": "向右游", "forward": "向前游", "back": "向后游",
         "window": "去窗边观景", "sofa": "去沙发睡觉", "center": "回到中央", "stop": "停在这里",
         "tea": "去茶桌喝茶", "garden": "去花园", "lookout": "去观景台", "parking": "去停车位", "door": "去门口"}
ACTIVITIES = {"run": "跑步巡游", "drive": "驾驶观光车", "sleep": "沙发小憩", "window": "窗边观景",
              "tea": "喝杯热茶", "lookout": "远眺云海", "wake": "起床 / 结束互动", "enter": "回家 / 进入会客厅", "exit": "出门去云台",
              "park": "停车留在驾驶位", "dismount": "下车走到车旁", "walk": "散步逛一圈"}
CAMERA_STEPS = {"turn_left": "镜头向左转", "turn_right": "镜头向右转", "up": "镜头抬高",
                "down": "镜头降低", "closer": "拉近镜头", "wider": "拉远镜头", **CAMERAS}
COMMANDS = {"move": MOVES, "camera": CAMERA_STEPS, "weather": WEATHERS, "scene": SCENE_COMMANDS, "activity": ACTIVITIES}
ACTIVITY_LABELS = {"idle": "站在原地休息", "travel": "前往目的地", "run": "跑步巡游", "drive": "开观光车巡游",
                   "parked": "已停车，仍坐在车内", "sleep": "躺在沙发上睡觉", "window": "在窗边看风景",
                   "tea": "在茶桌旁喝茶", "lookout": "在观景台远眺", "walk": "散步", "dismount": "下车"}
CAMERA_POSES = {"front": (0, 19, 12.8), "left": (-26, 24, 12.8), "right": (26, 24, 12.8),
                "close": (0, 13, 9.4), "overhead": (0, 40, 13.8)}


def interpolate(transition, extra=0):
    duration = max(.001, transition.get("duration", 1))
    progress = min(1, max(0, (transition.get("elapsed", 0) + extra) / duration))
    if progress >= 1:
        return list(transition["to"])
    if progress <= 0:
        return list(transition["from"])
    progress = progress * progress * (3 - 2 * progress)
    return [origin + (target - origin) * progress
            for origin, target in zip(transition["from"], transition["to"])]


def view(snapshot, extra=0):
    movement = snapshot["movement"]
    elapsed = max(0, movement.get("elapsed", 0) + extra)
    duration = movement["duration"]
    moving = elapsed < duration and movement["from"] != movement["to"]
    heading = movement.get("heading", 0)
    if movement.get("path"):
        position, heading = path_sample(movement["path"], elapsed / max(.001, duration))
        moving = elapsed < duration
    elif moving:
        position = interpolate(movement, extra)
        heading = math.atan2(movement["to"][0] - movement["from"][0], movement["to"][1] - movement["from"][1])
    else:
        position = interpolate(movement, extra)
    activity = snapshot.get("activity", "idle")
    active = activity if (not moving or activity in ("run", "drive", "walk", "dismount")) else "travel"
    if activity in ("run", "drive", "walk", "dismount") and not moving:
        active = "parked" if activity == "drive" else "idle"
    camera = interpolate(snapshot["camera_move"], extra)
    focus = interpolate(snapshot["focus_move"], extra) if "focus_move" in snapshot else [0, 1.4, 0]
    progress = min(1, max(0, (elapsed - duration) / 1.4))
    blend = progress * progress * (3 - 2 * progress)
    if snapshot.get("auto_camera", True) and active in ("window", "lookout", "sleep", "tea"):
        target_camera, target_focus = {
            "window": ([0, 12, 7.4], [0, 2.1, -2.7]),
            "lookout": ([-18, 18, 10], [position[0] * .55, 1.9, -1.6]),
            "sleep": ([-12, 26, 9.4], [-1.7, 1.0, -.7]),
            "tea": ([18, 23, 9.4], [1.5, 1.2, .8]),
        }[active]
        camera = [origin + (target - origin) * blend for origin, target in zip(camera, target_camera)]
        focus = [origin + (target - origin) * blend for origin, target in zip(focus, target_focus)]
    return {"position": position, "camera": camera, "focus": focus, "heading": heading, "moving": moving,
            "activity": active, "activity_age": max(0, elapsed - (0 if activity in ("run", "drive") else duration))}


class WorldDirector:
    def __init__(self, config, now=None):
        self.config = {}
        self.paused_at = None
        self.last_chat_at = -math.inf
        self.revision = 0
        self.configure(config, now)

    def configure(self, config, now=None):
        changed = any(self.config.get(key) != config.get(key) for key in ("scene", "weather", "camera"))
        self.config = dict(config)
        if changed or not hasattr(self, "movement"):
            self.reset(now)

    def reset(self, now=None):
        now = self._clock(now)
        self.scene = self.config.get("scene", "room")
        self.weather = self.config.get("weather", "clear")
        self.camera_name = self.config.get("camera", "front")
        self.movement = self._transition((0, .7), (0, .7), now, 1)
        camera = CAMERA_POSES[self.camera_name]
        self.camera_move = self._transition(camera, camera, now, 1)
        if self.scene == "cloud":
            self.camera_move = self._transition((0, 28, 20), (0, 28, 20), now, 1)
            self.movement = self._transition((0, 2.5), (0, 2.5), now, 1)
        self.focus_move = self._transition((0, 1.4, 0), (0, 1.4, 0), now, 1)
        self.activity = "idle"
        self.parked_vehicle = None
        self.auto_camera = True
        self.last_chat_at = -math.inf
        self.revision += 1

    def _clock(self, now):
        return self.paused_at if self.paused_at is not None else time.monotonic() if now is None else now

    @staticmethod
    def _transition(origin, target, now, duration):
        return {"from": list(origin), "to": list(target), "started": now, "duration": duration}

    def snapshot(self, now=None):
        now = self._clock(now)
        def sample(transition):
            return {key: copy.deepcopy(value) for key, value in transition.items()
                    if key != "started"} | {"elapsed": max(0, now - transition["started"])}
        return {"scene": self.scene, "weather": self.weather, "camera": self.camera_name,
                "revision": self.revision, "movement": sample(self.movement), "camera_move": sample(self.camera_move),
                "focus_move": sample(self.focus_move), "activity": self.activity, "auto_camera": self.auto_camera,
                "parked_vehicle": copy.deepcopy(self.parked_vehicle), "paused": self.paused_at is not None}

    def describe(self, now=None):
        snapshot = self.snapshot(now)
        current = view(snapshot)
        destination = next((MOVES.get(name, name) for name, point in LANDMARKS[self.scene].items()
                            if math.dist(snapshot["movement"]["to"], point) < .5), "指定位置")
        return {"scene": self.scene, "scene_label": SCENES[self.scene], "weather": self.weather,
                "weather_label": WEATHERS[self.weather], "camera": self.camera_name,
                "activity": current["activity"], "activity_label": ACTIVITY_LABELS[current["activity"]],
                "moving": current["moving"], "position": [round(value, 2) for value in current["position"]],
                "destination": destination if current["moving"] else "已到达",
                "on_arrival": ACTIVITY_LABELS.get(self.activity, "休息") if current["moving"] else "",
                "remaining_seconds": round(max(0, snapshot["movement"]["duration"] - snapshot["movement"]["elapsed"]), 1) if current["moving"] else 0,
                "in_vehicle": current["activity"] in ("drive", "parked"), "paused": self.paused_at is not None,
                "enabled": self.config.get("enabled", True), "chat_control": self.config.get("chat_control", True)}

    def pause(self, paused, now=None):
        now = time.monotonic() if now is None else now
        if paused and self.paused_at is None:
            self.paused_at = now
        elif not paused and self.paused_at is not None:
            delta = now - self.paused_at
            self.movement["started"] += delta
            self.camera_move["started"] += delta
            self.focus_move["started"] += delta
            if math.isfinite(self.last_chat_at):
                self.last_chat_at += delta
            self.paused_at = None

    def command(self, kind, value, manual=False, now=None):
        if not isinstance(kind, str) or kind not in COMMANDS or not isinstance(value, str) or value not in COMMANDS[kind]:
            raise ValueError("未知场景指令")
        if self.paused_at is not None:
            raise ValueError("直播已暂停，请先恢复直播")
        if not self.config.get("enabled", True):
            raise ValueError("请先在设置中开启三维场景")
        now = self._clock(now)
        if not manual:
            if not self.config.get("chat_control", True):
                raise ValueError("弹幕场景控制暂时关闭了")
            interrupt = (kind == "activity" and value in ("wake", "park", "dismount")) or (kind == "move" and value == "stop")
            if not interrupt and now - self.last_chat_at < self.config.get("cooldown", 2):
                raise ValueError("场景正在响应，请稍后再试")
        current = view(self.snapshot(now))
        if kind == "activity":
            self._activity(value, current, now)
        elif kind == "scene":
            if value == "next":
                sequence = ("room", "cloud", "terrace")
                value = sequence[(sequence.index(self.scene) + 1) % len(sequence)]
            self._scene(value, current, now)
        elif kind == "move":
            if value == "stop" and current["activity"] in ("drive", "parked"):
                self._activity("park", current, now)
                self.revision += 1
                return ACTIVITIES["park"]
            if value not in ("left", "right", "forward", "back", "stop") and value not in LANDMARKS[self.scene]:
                raise ValueError("当前场景没有这个地点，请先切换到对应场景")
            origin = current["position"]
            target = list(origin)
            delta = {"left": (-.9, 0), "right": (.9, 0), "forward": (0, .8), "back": (0, -.8)}.get(value)
            if delta:
                target = [coordinate + shift for coordinate, shift in zip(origin, delta)]
            elif value != "stop":
                target = LANDMARKS[self.scene][value]
            left, right, back, front = BOUNDS[self.scene]
            target = [min(right, max(left, target[0])), min(front, max(back, target[1]))]
            if not walkable(self.scene, target):
                raise ValueError("前方是建筑或景观，请换个方向或选择目的地")
            self._leave_vehicle(current)
            self._move(origin, target, now)
            self.activity = "idle"
            if value != "stop":
                for place, behavior in (("sofa", "sleep"), ("window", "window"), ("tea", "tea"), ("lookout", "lookout")):
                    anchor = LANDMARKS[self.scene].get(place)
                    if anchor and math.dist(target, anchor) < .48:
                        self.activity = behavior
                        break
            self._release_camera(current, now)
        elif kind == "camera":
            origin = current["camera"]
            target = list(origin)
            if value in CAMERA_POSES:
                target = CAMERA_POSES[value]
            else:
                axis, delta = {"turn_left": (0, -16), "turn_right": (0, 16), "up": (1, 8),
                               "down": (1, -8), "closer": (2, -1.2), "wider": (2, 1.2)}[value]
                target[axis] += delta
            if value in CAMERA_POSES and self.scene == "cloud":
                target = [target[0], max(24, target[1]), target[2] + 7.2]
            target = [min(38, max(-38, target[0])), min(42, max(10, target[1])), min(24 if self.scene == "cloud" else 16, max(9.4, target[2]))]
            self.camera_move = self._transition(origin, target, now, 1.2)
            self.focus_move = self._transition(current["focus"], (0, 1.4, 0), now, 1.2)
            self.auto_camera = False
            self.camera_name = value
        elif kind == "weather":
            self.weather = value
        self.revision += 1
        if not manual:
            self.last_chat_at = now
        return COMMANDS[kind][value]

    def _release_camera(self, current, now):
        self.auto_camera = True
        target = (0, 28, 20) if self.scene == "cloud" else CAMERA_POSES.get(self.camera_name, CAMERA_POSES["front"])
        self.camera_move = self._transition(current["camera"], target, now, 1.2)
        self.focus_move = self._transition(current["focus"], (0, 1.4, 0), now, 1.2)

    def _move(self, origin, target, now, speed=.85):
        points = route(self.scene, origin, target)
        duration = max(.35, sum(math.dist(start, end) for start, end in zip(points, points[1:])) / speed)
        self.movement = self._transition(points[0], points[-1], now, duration)
        if len(points) > 2:
            self.movement["path"] = points

    def _scene(self, name, current, now):
        self._leave_vehicle(current)
        self.scene = name
        center = LANDMARKS[name]["center"]
        self.movement = self._transition(center, center, now, .35)
        self.activity = "idle"
        self.camera_name = "front"
        self._release_camera(current, now)

    def _activity(self, value, current, now):
        if value in ("enter", "exit"):
            self._scene("room" if value == "enter" else "cloud", current, now)
            return
        if value in ("park", "dismount"):
            if current["activity"] not in ("drive", "parked"):
                raise ValueError("现在没有在车上，可以先说“开车”")
            if value == "park":
                self.movement = self._transition(current["position"], current["position"], now, .01)
                self.movement["heading"] = current["heading"]
                self.activity = "drive"
            else:
                self._leave_vehicle(current)
                candidates = [[current["position"][0] + shift[0], current["position"][1] + shift[1]]
                              for shift in ((-.85, 0), (.85, 0), (0, -.85), (0, .85))]
                target = next((point for point in candidates if walkable(self.scene, point)), current["position"])
                self._move(current["position"], target, now, speed=1.1)
                self.activity = "dismount"
        elif value == "wake":
            self._leave_vehicle(current)
            self._move(current["position"], LANDMARKS[self.scene]["center"], now)
            self.activity = "idle"
        elif value in ("run", "drive", "walk"):
            self._leave_vehicle(current)
            if value == "drive" and self.scene != "cloud":
                self._scene("cloud", current, now)
                current = view(self.snapshot(now))
            if self.scene == "cloud":
                circuit = promenade()
                points = route(self.scene, current["position"], circuit[0]) + circuit[1:]
                if value == "drive":
                    points += route(self.scene, points[-1], LANDMARKS["cloud"]["parking"])[1:]
            else:
                points = [current["position"]]
                for target in ((-1.4, .8), (-1.4, -.6), (1.3, -.6), (1.3, .9), (0, .7)):
                    points += route(self.scene, points[-1], target)[1:]
            self.movement = self._transition(points[0], points[-1], now, {"drive": 18, "run": 15, "walk": 24}[value])
            self.movement["path"] = points
            self.activity = value
            if value == "drive":
                self.parked_vehicle = None
        else:
            self._leave_vehicle(current)
            place = "sofa" if value == "sleep" else value
            if place not in LANDMARKS[self.scene]:
                self._scene("room" if value in ("sleep", "window", "tea") else "cloud", current, now)
                current = view(self.snapshot(now))
            self._move(current["position"], LANDMARKS[self.scene][place], now)
            self.activity = value
        self._release_camera(current, now)

    def _leave_vehicle(self, current):
        if self.scene == "cloud" and current["activity"] in ("drive", "parked"):
            self.parked_vehicle = {"position": list(current["position"]), "heading": current["heading"]}
