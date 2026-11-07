from __future__ import annotations

import math


def gift_pose(effect, age, duration):
    receive = {"upper_right": -20, "forearm_right": 45, "upper_left": 15, "forearm_left": -30, "head": 5}
    if effect in ("tea", "video"):
        hold = {"upper_right": 110, "forearm_right": 55, "upper_left": -25, "forearm_left": -70, "head": 8}
        perform = {**hold, "upper_right": 130, "forearm_right": 55, "head": -4, "hand_right": 18}
        flourish = {**receive, "forearm_right": 80, "head": 9, "upper_left": 105, "forearm_left": 10}
    elif effect in ("flowers", "heart"):
        hold = {"upper_left": -35, "forearm_left": -45, "upper_right": 35, "forearm_right": 45, "head": 9}
        perform = {**hold, "torso": -4, "head": -10, "upper_right": 40, "upper_left": -40,
                   "forearm_left": -60, "forearm_right": 60}
        flourish = {**hold, "torso": 4, "head": 10}
    elif effect == "sunglasses":
        hold = {"upper_right": 120, "forearm_right": 30, "head": -6}
        perform = {"upper_left": 20, "forearm_left": -55, "upper_right": -75, "forearm_right": -55,
                   "hand_right": 20, "head": -12, "torso": -4}
        flourish = {**perform, "head": 12, "torso": 5, "upper_right": -105}
    else:
        hold = {"upper_right": -50, "forearm_right": -75, "upper_left": 55, "forearm_left": 15, "head": -9}
        perform = {**hold, "upper_right": -68, "forearm_right": -55, "head": 9, "torso": -4}
        flourish = {**hold, "upper_left": 105, "hand_left": math.sin(age * 6) * 14, "torso": 4}
    bow = {"torso": 8, "head": 16, "upper_right": -10, "forearm_right": 45, "upper_left": 10}
    frames = [(0, {}), (1.4, receive), (3.3, hold), (5.2, perform),
              (7.3, perform), (duration - 2.2, flourish), (duration - .6, bow), (duration, {})]
    for (start, previous), (end, following) in zip(frames, frames[1:]):
        if age <= end:
            fraction = max(0, min(1, (age - start) / max(.01, end - start)))
            fraction = fraction * fraction * (3 - 2 * fraction)
            return {bone: previous.get(bone, 0) + (following.get(bone, 0) - previous.get(bone, 0)) * fraction
                    for bone in previous.keys() | following.keys()}
    return {}
