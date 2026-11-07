from __future__ import annotations

import copy
from string import Formatter

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..lines import LINE_LABELS, LINE_PARAMETERS, SAMPLE_PARAMETERS
from ..settings_store import DEFAULTS


class LineEditor(QWidget):
    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.values = {}
        self.rules = []
        self.current = None
        layout = QVBoxLayout(self)
        note = QLabel("每行一条候选话术，保存后生效。空白类别使用默认话术；关闭播报请使用对应开关。\n"
                      "优先使用 AI 开启时，欢迎、感谢和冷场可能由模型即兴生成；离线话术在模型不可用时使用。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.categories = QComboBox()
        self.categories.currentIndexChanged.connect(self._select)
        layout.addWidget(self.categories)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.editor = QTextEdit()
        self.editor.setMinimumHeight(160)
        self.editor.textChanged.connect(self._preview)
        self.editor.cursorPositionChanged.connect(self._preview)
        layout.addWidget(self.editor)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        row = QHBoxLayout()
        reset = QPushButton("恢复本类默认")
        reset.clicked.connect(self._reset)
        self.play = QPushButton("播报光标所在话术")
        self.play.clicked.connect(self._play)
        row.addWidget(reset)
        row.addWidget(self.play)
        layout.addLayout(row)
        note = QLabel("播报会停止当前发言，使用已保存音色和语速，并进入直播音频输出；可在未开播或静音时检查字幕。")
        note.setWordWrap(True)
        layout.addWidget(note)

    def load(self, config):
        self.values = {key: list((config.get("lines") or {}).get(key, DEFAULTS["lines"][key])) for key in LINE_LABELS}
        self.rules = copy.deepcopy((config.get("interact") or {}).get("rules") or [])
        self.current = None
        self.categories.blockSignals(True)
        self.categories.clear()
        for key, label in LINE_LABELS.items():
            self.categories.addItem(label, key)
        for index, rule in enumerate(self.rules):
            label = rule.get("match") or {"gift": "礼物", "follow": "关注", "enter": "进场"}.get(rule.get("on"), "互动")
            self.categories.addItem(f"规则台词 · {label} · {rule.get('id', index)}", index)
        self.categories.blockSignals(False)
        self._select()

    def _store(self):
        if isinstance(self.current, int):
            self.rules[self.current]["reply"] = self.editor.toPlainText().strip()
        elif self.current is not None:
            self.values[self.current] = [line.strip() for line in self.editor.toPlainText().splitlines() if line.strip()]

    def _select(self):
        self._store()
        self.current = self.categories.currentData()
        if self.current is None:
            return
        parameters = {"name", "gift", "count"} if isinstance(self.current, int) else LINE_PARAMETERS.get(self.current, set())
        placeholders = "、".join("{" + key + "}" for key in sorted(parameters)) or "无"
        self.hint.setText("可用占位符：" + placeholders + ("；规则台词整体播报，留空只做动作。" if isinstance(self.current, int) else "；每行一条。"))
        text = self.rules[self.current].get("reply", "") if isinstance(self.current, int) else "\n".join(self.values[self.current])
        self.editor.setPlainText(text)
        self._preview()

    def sample(self):
        text = self.editor.toPlainText().strip() if isinstance(self.current, int) else self.editor.textCursor().block().text().strip()
        allowed = {"name", "gift", "count"} if isinstance(self.current, int) else LINE_PARAMETERS.get(self.current, set())
        for _, field, spec, conversion in Formatter().parse(text):
            if field is not None and (field not in allowed or spec or conversion):
                raise ValueError("不支持的占位符：" + str(field))
        return text.format(**SAMPLE_PARAMETERS)

    def _preview(self):
        try:
            text = self.sample()
            self.preview.setText("示例：" + (text or "当前行为空"))
            self.play.setEnabled(bool(text))
        except (ValueError, KeyError) as error:
            self.preview.setText("请检查占位符：" + str(error))
            self.play.setEnabled(False)

    def _play(self):
        try:
            text = self.sample()
        except (ValueError, KeyError):
            return
        if text:
            self.client.send({"cmd": "say", "text": text})

    def _reset(self):
        if isinstance(self.current, int):
            identifier = self.rules[self.current].get("id")
            text = next((rule.get("reply", "") for rule in DEFAULTS["interact"]["rules"] if rule.get("id") == identifier), "")
        else:
            text = "\n".join(DEFAULTS["lines"].get(self.current, []))
        self.editor.setPlainText(text)

    def patch(self):
        self._store()
        return {"lines": copy.deepcopy(self.values), "interact": {"rules": copy.deepcopy(self.rules)}}
