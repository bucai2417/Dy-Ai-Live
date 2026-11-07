from __future__ import annotations

import os
import sys
from pathlib import Path


def runtime_paths():
    source = Path(__file__).resolve().parent.parent
    frozen = getattr(sys, "frozen", False)
    resources = Path(getattr(sys, "_MEIPASS", source)).resolve()
    default_home = Path(sys.executable).resolve().parent if frozen else source
    runtime = Path(os.environ.get("DEEPY_HOME") or default_home).expanduser().resolve()
    return runtime, resources


ROOT, RESOURCE_ROOT = runtime_paths()


def renderer_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--renderer"]
    return [sys.executable, "-m", "app.renderer"]
