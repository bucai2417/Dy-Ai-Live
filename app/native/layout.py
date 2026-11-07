from __future__ import annotations

from dataclasses import dataclass


Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class StageLayout:
    width: int
    height: int
    stage: Rect
    effects: Rect
    bubbles: Rect
    video: Rect
    stats: tuple[Rect, ...]
    tokens: Rect
    ranking: Rect
    messages: Rect
    subtitle: Rect
    music: Rect
    like_meter: Rect
    avatar_top: float
    avatar_height: float


def stage_layout(portrait: bool, show_tokens: bool = True, show_stats: bool = True) -> StageLayout:
    if portrait:
        return StageLayout(
            width=720, height=1280,
            stage=(16, 148, 688, 788), effects=(16, 148, 688, 788),
            bubbles=(8, 8, 704, 1264), video=(32, 178, 656, 680),
            stats=tuple((24 + index * 92, 76, 86, 34) for index in range(3)),
            tokens=(308, 76, 388, 34), ranking=(16, 948, 264, 134),
            messages=(292, 948, 412, 134), subtitle=(32, 844, 656, 76),
            music=(16, 1094, 688, 138), like_meter=(24, 114, 672, 26),
            avatar_top=224, avatar_height=670,
        )
    return StageLayout(
        width=1280, height=720,
        stage=(16, 148, 952, 526), effects=(16, 148, 952, 526),
        bubbles=(8, 8, 1264, 704), video=(40, 160, 904, 440),
        stats=tuple((24 + index * 116, 76, 108, 34) for index in range(3)),
        tokens=(868, 76, 388, 34), ranking=(984, 148, 280, 184),
        messages=(984, 344, 280, 136), subtitle=(36, 586, 912, 72),
        music=(984, 492, 280, 182), like_meter=(24, 114, 1232, 26),
        avatar_top=144, avatar_height=530,
    )
