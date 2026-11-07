from __future__ import annotations

import json
from pathlib import Path


from ..paths import ROOT
DEFAULT_SIZE = (720, 1280)


def display_path(port: int) -> Path:
    return ROOT / "data" / f"native-display-{port}.json"


def validate_size(width: int, height: int) -> tuple[int, int]:
    if any(isinstance(value, bool) or not isinstance(value, int) or not 320 <= value <= 1920
           for value in (width, height)):
        raise ValueError("画面尺寸应为 320～1920 的整数")
    return width, height


def load_size(path: Path) -> tuple[int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_size(data["width"], data["height"])
    except (OSError, ValueError, TypeError, KeyError):
        return DEFAULT_SIZE


def resolve_size(width: int | None, height: int | None, path: Path) -> tuple[int, int]:
    if width is None and height is None:
        return load_size(path)
    return validate_size(width if width is not None else DEFAULT_SIZE[0],
                         height if height is not None else DEFAULT_SIZE[1])


def orientation_size(width: int, height: int, orientation: str) -> tuple[int, int]:
    if orientation not in ("portrait", "landscape"):
        raise ValueError("请选择横屏或竖屏")
    short, long = sorted(validate_size(width, height))
    if short == long:
        short, long = DEFAULT_SIZE
    return (short, long) if orientation == "portrait" else (long, short)


def save_size(path: Path, width: int, height: int):
    validate_size(width, height)
    data = {"width": width, "height": height}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
