from __future__ import annotations

import heapq
import math


BOUNDS = {"room": (-2.5, 2.5, -1.8, 1.7), "terrace": (-2.5, 2.5, -1.5, 1.7),
          "cloud": (-5.3, 5.3, -2.8, 3.2)}
LANDMARKS = {
    "room": {"center": (0, .7), "window": (0, -1.35), "sofa": (-2.3, -1.45), "tea": (1.85, 1.1), "door": (1.9, -.9)},
    "terrace": {"center": (0, .7), "sofa": (-2.3, -1.4), "lookout": (1.9, -.6), "door": (0, -1.3)},
    "cloud": {"center": (0, 2.5), "door": (0, -1.65), "garden": (-3.9, -.7), "lookout": (3.95, -.8), "parking": (3.6, 2.45)},
}
OBSTACLES = {"room": ((2.15, 3.8, -2.8, -1.2), (2.15, 3.7, 1.2, 2.7)),
             "terrace": (), "cloud": ((-2.6, 2.6, -4, -1.9), (-1.3, 1.3, -.65, 1.1))}


def walkable(scene, point):
    horizontal, depth = point
    left, right, back, front = BOUNDS[scene]
    return left - .001 <= horizontal <= right + .001 and back - .001 <= depth <= front + .001 and not any(
        minimum < horizontal < maximum and rear < depth < near for minimum, maximum, rear, near in OBSTACLES[scene])


def clear_line(scene, origin, target):
    steps = max(1, math.ceil(math.dist(origin, target) / .08))
    return all(walkable(scene, [start + (end - start) * index / steps for start, end in zip(origin, target)])
               for index in range(steps + 1))


def route(scene, origin, target):
    if clear_line(scene, origin, target):
        return [list(origin), list(target)]
    step = .25
    start = tuple(round(value / step) for value in origin)
    finish = tuple(round(value / step) for value in target)
    frontier = [(0, start)]
    costs, parents = {start: 0}, {}
    while frontier:
        _, current = heapq.heappop(frontier)
        if current == finish:
            chain = [list(target)]
            while current != start:
                chain.append([value * step for value in current])
                current = parents[current]
            chain.append(list(origin))
            chain.reverse()
            simplified = [chain[0]]
            index = 0
            while index < len(chain) - 1:
                following = len(chain) - 1
                while following > index + 1 and not clear_line(scene, chain[index], chain[following]):
                    following -= 1
                simplified.append(chain[following])
                index = following
            return simplified
        for delta in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
            neighbor = tuple(value + shift for value, shift in zip(current, delta))
            point = [value * step for value in neighbor]
            if not clear_line(scene, [value * step for value in current], point):
                continue
            cost = costs[current] + math.hypot(*delta)
            if cost < costs.get(neighbor, math.inf):
                costs[neighbor], parents[neighbor] = cost, current
                heapq.heappush(frontier, (cost + math.dist(neighbor, finish), neighbor))
    return [list(origin), list(origin)]


def promenade():
    return [[4.3 * math.cos(index / 64 * math.tau), .55 + 2.05 * math.sin(index / 64 * math.tau)] for index in range(65)]


def path_sample(points, fraction):
    lengths = [math.dist(origin, target) for origin, target in zip(points, points[1:])]
    remaining = min(1, max(0, fraction)) * sum(lengths)
    for index, distance in enumerate(lengths):
        if remaining <= distance or index == len(lengths) - 1:
            origin, target = points[index:index + 2]
            progress = min(1, remaining / max(.00001, distance))
            position = [start + (end - start) * progress for start, end in zip(origin, target)]
            heading = math.atan2(target[0] - origin[0], target[1] - origin[1])
            return position, heading
        remaining -= distance
    return list(points[-1]), 0
