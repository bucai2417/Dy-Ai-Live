from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path

from .paths import ROOT


def dispatch(arguments):
    if arguments and arguments[0] == "--self-check":
        from .packaged_check import run

        return run(Path(arguments[1]))
    if arguments and arguments[0] == "--renderer":
        from .renderer import main

        return main(arguments[1:])
    from .__main__ import main

    if arguments and arguments[0] == "--server":
        return main(arguments[1:])
    return main(arguments if "--version" in arguments else ["--desktop", *arguments])


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if sys.stdout is None:
            sys.stdout = (log_dir / "launcher.log").open("a", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = sys.stdout
        return dispatch(arguments)
    except Exception as error:
        logging.exception("启动失败")
        if arguments and arguments[0] == "--self-check":
            if len(arguments) > 1:
                report = Path(arguments[1])
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps({"ok": False, "error": traceback.format_exc()}, ensure_ascii=False), encoding="utf-8")
        else:
            from PySide6.QtWidgets import QApplication, QMessageBox

            application = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, "启动失败", f"{error}\n\n请查看 {ROOT / 'logs'} 中的日志。")
        return 1
