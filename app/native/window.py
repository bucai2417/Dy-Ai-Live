from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QTabWidget, QTextEdit, QVBoxLayout, QWidget

from ..paths import ROOT
from .audio import AudioController
from .video import VideoController
from .client import LiveClient
from .output import VideoOutput
from .scene import RenderWorker
from .state import FrameStore, StageState
from .actions import ACTIONS, COSTUMES, SKINS, ACCESSORIES
from ..like_goal import STUNTS
from ..world import ACTIVITIES, CAMERAS, CAMERA_STEPS, MOVES, SCENES, SCENE_COMMANDS, WEATHERS
from .music_view import music_view
from .display import display_path, orientation_size, resolve_size, save_size

STYLE = """
QWidget { background: #0c1424; color: #d9e7f5; font: 13px 'Microsoft YaHei UI'; }
QLabel#title { font-size: 22px; font-weight: 600; color: #f1f6fc; }
QLabel#muted { color: #829ab4; font-size: 12px; }
QLabel#status { color: #7cd8c9; padding: 12px; background: #102630; border-radius: 10px; }
QFrame#card { background: #121f33; border: 1px solid #253951; border-radius: 14px; }
QFrame#card QLabel { background: transparent; border: none; }
QPushButton { background: #1b2e46; border: 1px solid #304860; padding: 10px 14px; border-radius: 9px; }
QPushButton:hover { background: #284561; border-color: #5b839e; }
QPushButton:checked { background: #214c55; border-color: #6bd8cb; color: #a6f4e8; }
QPushButton:disabled { color: #56657a; background: #152136; }
QPushButton#primary { background: #58d5c4; color: #082b32; border: 0; font-weight: 600; }
QPushButton#pause { background: #e6bf83; color: #33251d; border: 0; font-weight: 600; }
QLineEdit,QTextEdit,QComboBox { background: #091323; border: 1px solid #304860; border-radius: 8px; padding: 9px; }
QLineEdit:focus,QTextEdit:focus { border-color: #6bd8cb; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 10px 18px; color: #8ba4be; background: #0c1424; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #7be0d2; border-bottom: 2px solid #7be0d2; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #101c2e; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #35536b; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background: none; }
QCheckBox { padding: 8px 0; }
QProgressBar { border: none; background: #23384d; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #76dccc; border-radius: 3px; }
"""


class Preview(QWidget):
    def __init__(self, frames, parent=None):
        super().__init__(parent)
        self.frames = frames
        self.sequence = -1
        self.setMinimumSize(240, 300)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(33)

    def refresh(self):
        _, sequence = self.frames.latest()
        if sequence != self.sequence:
            self.sequence = sequence
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#070d18"))
        image, _ = self.frames.latest()
        if image:
            scale = min(self.width() / image.width(), self.height() / image.height())
            width, height = image.width() * scale, image.height() * scale
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height), image)
        else:
            painter.setPen(QColor("#9cb8d0"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.frames.error or "正在准备直播画面…")
        painter.end()


class StudioWindow(QMainWindow):
    def __init__(self, base_url, width=None, height=None, fps=30, output_port=18849, muted=False,
                 display_settings_path=None):
        super().__init__()
        self.display_settings_path = Path(display_settings_path) if display_settings_path is not None else display_path(output_port)
        width, height = resolve_size(width, height, self.display_settings_path)
        self._displayed_size = (width, height)
        self._pending_display_save = None
        self.setWindowTitle("深蓝直播工作台 · 原生画面")
        self.setStyleSheet(STYLE)
        self.resize(1200 if width > height else 1020, 860)
        self.state = StageState()
        self.frames = FrameStore()
        self.worker = RenderWorker(self.state, self.frames, width, height, fps)
        self.output = VideoOutput(self.frames, output_port)
        self.client = LiveClient(self.state, base_url)
        self.audio = AudioController(self.state, base_url, muted)
        self.video = VideoController(self.state, muted)
        self.client.message.connect(self.audio.handle)
        self.client.message.connect(self.video.handle)
        self.client.message.connect(self._message)
        self.client.response.connect(self._ai_context_response)
        self._context_requested_at = 0.0
        self._context_pending = False
        self.client.status.connect(self.notice)
        self.client.connection.connect(self._connection)
        self.audio.notice.connect(self.notice)
        self.audio.voice_finished.connect(lambda identifier, status: self.client.send(
            {"cmd": "voice_finished", "id": identifier, "status": status}) if identifier and not self.client.closing else None)
        self.video.notice.connect(self.notice)
        self.audio.music_ended.connect(lambda identifier: self.client.post("/api/music/played", {"id": identifier}))
        self.popout = None
        self.status_path = ROOT / "data" / f"native-renderer-{output_port}.json"
        self._build(width, height, fps)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._telemetry)
        self.timer.start(1000)
        self.worker.start()
        self.client.open()
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.toggle_pause)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)

    def _build(self, width, height, fps):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        preview_column = QVBoxLayout()
        title = QLabel("深蓝 · LIVE STUDIO")
        title.setObjectName("title")
        preview_column.addWidget(title)
        self.render_label = QLabel(f"原生渲染   /   {width} × {height}   /   目标 {fps} FPS")
        self.render_label.setObjectName("muted")
        preview_column.addWidget(self.render_label)
        self.storage_label = QLabel("今日数据自动保存 · 正在同步")
        self.storage_label.setObjectName("muted")
        self.storage_label.setWordWrap(True)
        preview_column.addWidget(self.storage_label)
        self.preview = Preview(self.frames)
        self.preview.timer.timeout.connect(self._sync_output_size)
        direction_row = QHBoxLayout()
        self.orientation_group = QButtonGroup(self)
        self.orientation_buttons = {}
        for key, label in (("portrait", "竖屏"), ("landscape", "横屏")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked((height > width) == (key == "portrait"))
            button.setToolTip("切换直播画面布局并记住选择；音乐与互动继续运行")
            button.clicked.connect(lambda checked=False, direction=key: self.set_orientation(direction))
            self.orientation_group.addButton(button)
            self.orientation_buttons[key] = button
            direction_row.addWidget(button)
        direction_row.addStretch()
        preview_column.addLayout(direction_row)
        preview_column.addWidget(self.preview, 1)
        preview_column.addWidget(QLabel("画面独立渲染 · 操作面板不会进入直播输出"))
        layout.addLayout(preview_column, 1)
        panel = QWidget()
        panel.setMinimumWidth(340)
        panel.setMaximumWidth(400)
        sidebar = QVBoxLayout(panel)
        sidebar.setContentsMargins(0, 0, 0, 0)
        title = QLabel("直播工作台")
        title.setObjectName("title")
        sidebar.addWidget(title)
        self.status = QLabel("正在连接直播服务…")
        self.status.setWordWrap(True)
        self.status.setObjectName("status")
        sidebar.addWidget(self.status)
        self.pause_button = QPushButton("暂停直播")
        self.pause_button.setObjectName("pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        sidebar.addWidget(self.pause_button)
        explanation = QLabel("暂停后停止 AI 回复、语音和音乐，并显示休息画面。Ctrl+P 切换，F11 全屏。")
        explanation.setWordWrap(True)
        explanation.setObjectName("muted")
        sidebar.addWidget(explanation)
        tabs = QTabWidget()
        for label, content in (("直播控制", self._controls()), ("场景导演", self._world_controls()), ("接入设置", self._settings()),
                               ("画面输出", self._output_controls())):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            tabs.addTab(scroll, label)
        sidebar.addWidget(tabs, 1)
        layout.addWidget(panel)
        self.setCentralWidget(root)

    def _controls(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.addWidget(QLabel("人工播报"))
        self.say_input = QTextEdit()
        self.say_input.setPlaceholderText("想对观众说些什么？")
        self.say_input.setMaximumHeight(95)
        layout.addWidget(self.say_input)
        row = QHBoxLayout()
        speak = QPushButton("立即播报")
        speak.setObjectName("primary")
        speak.clicked.connect(self.say)
        skip = QPushButton("跳过当前")
        skip.clicked.connect(lambda: self.client.send({"cmd": "skip"}))
        row.addWidget(speak)
        row.addWidget(skip)
        layout.addLayout(row)
        self.mute_box = QCheckBox("静音输出（包括 OBS 捕获的声音）")
        self.mute_box.setChecked(self.audio.muted)
        self.mute_box.toggled.connect(self.audio.mute)
        self.mute_box.toggled.connect(self.video.mute)
        layout.addWidget(self.mute_box)
        layout.addWidget(QLabel("点歌 / 播放列表"))
        self.song = QLineEdit()
        self.song.setPlaceholderText("输入歌名，回车加入队列")
        self.song.returnPressed.connect(self.request_song)
        layout.addWidget(self.song)
        player = QFrame()
        player.setObjectName("card")
        player_layout = QVBoxLayout(player)
        self.music_label = QLabel("当前：等待点歌")
        self.music_label.setTextFormat(Qt.TextFormat.PlainText)
        self.music_label.setWordWrap(True)
        player_layout.addWidget(self.music_label)
        self.music_progress = QProgressBar()
        self.music_progress.setRange(0, 1000)
        self.music_progress.setTextVisible(False)
        player_layout.addWidget(self.music_progress)
        self.music_time = QLabel("00:00 / --:--")
        self.music_time.setObjectName("muted")
        player_layout.addWidget(self.music_time)
        self.next_music = QLabel("下一首：暂无待播歌曲")
        self.next_music.setWordWrap(True)
        self.next_music.setTextFormat(Qt.TextFormat.PlainText)
        self.next_music.setObjectName("muted")
        player_layout.addWidget(self.next_music)
        layout.addWidget(player)
        row = QHBoxLayout()
        for label, action in (("点歌", self.request_song), ("播放", self.toggle_music),
                              ("下一首", lambda: self.client.post("/api/music/next"))):
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
            if label == "播放":
                self.music_play_button = button
        layout.addLayout(row)
        layout.addWidget(QLabel("人物动作 · 点击后会出现在直播画面"))
        row = QHBoxLayout()
        self.action_input = QComboBox()
        for identifier, preset in ACTIONS.items():
            self.action_input.addItem(preset["label"], identifier)
        row.addWidget(self.action_input, 1)
        self.action_button = QPushButton("播放动作")
        self.action_button.clicked.connect(self.trigger_action)
        row.addWidget(self.action_button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.costume_input = QComboBox()
        self.costume_input.addItem("下一套服装", "next_outfit")
        self.costume_input.addItem("换个配饰", "next_accessory")
        for identifier, label in COSTUMES.items():
            self.costume_input.addItem(label, identifier)
        row.addWidget(self.costume_input, 1)
        costume_button = QPushButton("切换装扮")
        costume_button.clicked.connect(lambda: self.client.send({"cmd": "costume", "name": self.costume_input.currentData()}))
        row.addWidget(costume_button)
        layout.addLayout(row)
        video_button = QPushButton("播放奶蛙视频")
        self.wardrobe_status = QLabel("当前服装：蓝海水手服")
        self.wardrobe_status.setWordWrap(True)
        layout.addWidget(self.wardrobe_status)
        video_button.clicked.connect(lambda: self.client.send({"cmd": "video", "clip": "milk-frog"}))
        layout.addWidget(video_button)
        layout.addWidget(QLabel("点赞达标整活"))
        self.like_status = QLabel("等待点赞数据…")
        self.like_status.setWordWrap(True)
        layout.addWidget(self.like_status)
        row = QHBoxLayout()
        self.stunt_input = QComboBox()
        for identifier, preset in STUNTS.items():
            self.stunt_input.addItem(preset["label"], identifier)
        row.addWidget(self.stunt_input, 1)
        self.stunt_button = QPushButton("预览整活")
        self.stunt_button.clicked.connect(lambda: self.client.send({"cmd": "stunt", "motion": self.stunt_input.currentData()}))
        row.addWidget(self.stunt_button)
        layout.addLayout(row)
        self.test_likes = QPushButton("测试点赞 +10")
        self.test_likes.clicked.connect(lambda: self.client.post("/api/inject", {"kind": "like", "count": 10, "user": "测试观众"}))
        layout.addWidget(self.test_likes)
        layout.addWidget(QLabel("互动测试"))
        self.event_text = QLineEdit()
        self.event_text.setPlaceholderText("例如：主播你好呀")
        self.event_text.returnPressed.connect(self.inject_chat)
        layout.addWidget(self.event_text)
        row = QHBoxLayout()
        for label, action in (("发送弹幕", self.inject_chat), ("测试礼物", lambda: self.client.post("/api/inject", {"kind": "gift", "gift": "小心心", "user": "测试观众"}))):
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        layout.addStretch()
        clear = QPushButton("清空本场弹幕和歌单")
        clear.clicked.connect(self.clear_session)
        layout.addWidget(clear)
        return page

    def _world_controls(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        self.world_enabled = QCheckBox("启用原生三维场景")
        self.world_enabled.clicked.connect(lambda checked: self.client.post("/api/config", {"world": {"enabled": checked}}))
        layout.addWidget(self.world_enabled)
        self.world_status = QLabel("等待场景同步…")
        self.world_status.setWordWrap(True)
        layout.addWidget(self.world_status)
        self.ai_state_label = QLabel("AI 状态：等待同步…")
        self.ai_state_label.setWordWrap(True)
        self.ai_state_label.setObjectName("status")
        layout.addWidget(self.ai_state_label)
        fishing_row = QHBoxLayout()
        for label, action in (("去钓鱼", "start"), ("协力拉钩", "pull"), ("花式跳水", "dive"), ("收竿返回", "cancel")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, operation=action: self.client.send({"cmd": "fishing", "action": operation}))
            fishing_row.addWidget(button)
        layout.addLayout(fishing_row)
        mode_row = QHBoxLayout()
        for label, action in (("进入常驻钓台", "enter_dedicated"), ("退出常驻钓台", "exit_dedicated")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, operation=action: self.client.send({"cmd": "fishing", "action": operation}))
            mode_row.addWidget(button)
        layout.addLayout(mode_row)
        action_row = QHBoxLayout()
        for label, action in (("投饵引鱼", "feed"), ("加油护线", "cheer"), ("松线捣蛋", "loosen")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, operation=action: self.client.send({"cmd": "fishing", "action": operation}))
            action_row.addWidget(button)
        layout.addLayout(action_row)
        spot_row = QHBoxLayout()
        for label, action in (("换个钓点", "relocate"), ("芦苇浅湾", "spot_reeds"), ("星礁海岸", "spot_reef"), ("云汐钓台", "spot_cloud")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, operation=action: self.client.send({"cmd": "fishing", "action": operation}))
            spot_row.addWidget(button)
        layout.addLayout(spot_row)
        fishing_help = QLabel("常驻退出：管理员 / 获授权榜单前三发送“退出常驻钓鱼模式”；主播可直接点击退出常驻钓台。")
        fishing_help.setWordWrap(True)
        layout.addWidget(fishing_help)
        self.fishing_status = QLabel("钓鱼：等待开局")
        self.fishing_status.setWordWrap(True)
        layout.addWidget(self.fishing_status)
        self.world_buttons = []
        self.world_inputs = {}
        for kind, label, choices in (("scene", "切换场景", SCENE_COMMANDS), ("weather", "切换天气", WEATHERS),
                                     ("camera", "调整镜头", CAMERA_STEPS), ("move", "移动人物", MOVES), ("activity", "场景互动", ACTIVITIES)):
            row = QHBoxLayout()
            selector = QComboBox()
            selector.setObjectName("world_" + kind)
            for value, title in choices.items():
                selector.addItem(title, value)
            self.world_inputs[kind] = selector
            row.addWidget(selector, 1)
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, action=kind, field=selector:
                                   self.client.send({"cmd": "world", "action": action, "value": field.currentData()}))
            self.world_buttons.append(button)
            row.addWidget(button)
            layout.addLayout(row)
        for choices in ((("← 左游", "left"), ("回中央", "center"), ("右游 →", "right")),
                        (("向前游", "forward"), ("停止", "stop"), ("向后游", "back"))):
            row = QHBoxLayout()
            for label, value in choices:
                button = QPushButton(label)
                button.clicked.connect(lambda checked=False, target=value:
                                       self.client.send({"cmd": "world", "action": "move", "value": target}))
                self.world_buttons.append(button)
                row.addWidget(button)
            layout.addLayout(row)
        note = QLabel("弹幕可说：\n切换场景 / 换个场景 / 出门 / 回家\n跑步 / 开车兜风 / 停车 / 下车 / 散步\n去沙发睡觉 / 去窗边看风景 / 喝茶\n起床 / 停止动作 / 再来一次 / 你在干嘛\n你会什么 / 拉近镜头 / 下点雪\n\n"
                      "换场景依次轮换会客厅、云台、露台；回家返回会客厅。\n跑步 15 秒，开车 18 秒，散步 24 秒。停车后仍在车内，发下车走到车旁。\nAI 会读取当前位置、睡眠、驾驶和动作进度；暂停会冻结进度。\n\n"
                      "开播默认场景和弹幕控制权限在「直播设置 → 三维场景」修改。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("直播间名称"))
        self.name_input = QLineEdit()
        layout.addWidget(self.name_input)
        layout.addWidget(QLabel("弹幕来源"))
        self.source_input = QComboBox()
        for label, value in (("模拟弹幕", "mock"), ("外部助手桥接", "bridge"),
                             ("抖音浏览器采集", "douyin"), ("抖音直连", "douyin_direct")):
            self.source_input.addItem(label, value)
        layout.addWidget(self.source_input)
        self.room_input = QLineEdit()
        self.room_input.setPlaceholderText("直播间房间号或完整链接")
        layout.addWidget(self.room_input)
        self.ingest_enabled = QCheckBox("启用弹幕接入")
        layout.addWidget(self.ingest_enabled)
        save = QPushButton("保存并生效")
        save.setObjectName("primary")
        save.clicked.connect(self.save_settings)
        layout.addWidget(save)
        preferences = QPushButton("人设 / AI / 语音 / 画面设置")
        preferences.clicked.connect(self.show_preferences)
        layout.addWidget(preferences)
        note = QLabel("原生画面使用当前深蓝立绘和眼睛、嘴部贴片。后台上传的 Cubism / Live2D 模型仍用于网页预览。抖音浏览器采集只用于接收弹幕，与画面渲染独立。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _output_controls(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("OBS 持续视频源 · 固定 25 FPS"))
        address = QLineEdit(self.output.url)
        address.setReadOnly(True)
        layout.addWidget(address)
        copy = QPushButton("复制视频源地址")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.output.url))
        layout.addWidget(copy)
        note = QLabel("OBS 添加「媒体源」，取消「本地文件」，把上面的地址填入输入框，输入格式填 mpjpeg，并关闭「源不可见时关闭文件」。即使工作台最小化，独立渲染仍持续输出。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.output_size_label = QLabel()
        self.output_size_label.setWordWrap(True)
        layout.addWidget(self.output_size_label)
        self._output_size_text(*self.worker.size)
        note = QLabel("预览上方可切换横屏 / 竖屏，自动记住上次选择。切换后请在 OBS 中调整画布比例并重新适配媒体源；视频地址保持不变。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        note = QLabel("视频源不包含音频。请用 OBS「应用程序音频捕获」选择本工作台，或采集桌面音频；避免同时采集两份声音。暂停直播是休息画面，不会替你停止 OBS 推流。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        popout = QPushButton("打开纯净画面窗口")
        popout.clicked.connect(self.show_output)
        layout.addWidget(popout)
        shot = QPushButton("保存画面截图")
        shot.clicked.connect(self.save_snapshot)
        layout.addWidget(shot)
        layout.addStretch()
        return page

    def notice(self, text):
        self.status.setText(text)

    def _connection(self, connected):
        self.pause_button.setEnabled(connected)
        self.action_button.setEnabled(connected and not self.state.paused)
        if not connected:
            self.audio.music.pause()
            self.audio.bgm.pause()
            self.ai_state_label.setText("AI 状态：服务已断开，等待重新同步")

    def _fill_settings(self):
        config = self.state.config
        self.name_input.setText(config.get("name") or "")
        self.room_input.setText(config.get("room") or "")
        source = config.get("ingestConfiguredSource") or config.get("ingest") or "bridge"
        self.source_input.setCurrentIndex(max(0, self.source_input.findData(source)))
        self.ingest_enabled.setChecked(config.get("ingest") != "off")

    def _message(self, message):
        kind = message.get("type")
        if kind in ("hello", "fishing"):
            game = self.state.fishing
            self.fishing_status.setText("钓鱼：" + game.get("label", "等待开局"))
        if kind in ("hello", "stats"):
            storage = self.state.stats.get("storage") or {}
            storage_text = "今日数据已保存 · " + storage["savedAt"] if storage.get("savedAt") else "今日数据自动保存 · 等待首次保存"
            self.storage_label.setText(storage.get("error") or storage_text)
        if kind in ("hello", "state"):
            self.pause_button.setText("恢复直播" if self.state.paused else "暂停直播")
            self.notice("直播已暂停，恢复后继续互动" if self.state.paused else "直播运行中 · 已连接服务")
        if kind in ("hello", "config_changed"):
            self._fill_settings()
        if kind in ("hello", "config_changed", "world", "state"):
            enabled = (self.state.config.get("world") or {}).get("enabled", False)
            self.world_enabled.setChecked(enabled)
            snapshot = self.state.world
            self.world_status.setText(f"{SCENES.get(snapshot.get('scene'), '海风会客厅')} · "
                                      f"{WEATHERS.get(snapshot.get('weather'), '晴空')} · "
                                      f"{CAMERA_STEPS.get(snapshot.get('camera'), '全景')}" if enabled else "当前为经典舞台")
            for button in self.world_buttons:
                button.setEnabled(enabled and self.state.connected and not self.state.paused)
        if kind in ("hello", "config_changed", "costume", "reset"):
            stage = self.state.config.get("stage") or {}
            outfit = self.state.outfit or stage.get("outfit", "sailor")
            accessory = self.state.costume or stage.get("costume", "original")
            outfit_label = SKINS.get(outfit, "蓝海水手服")
            accessory_label = "无配饰" if accessory == "original" else ACCESSORIES.get(accessory, "无配饰")
            mode = "原始立绘" if stage.get("avatar_mode") == "image" else "分层骨架"
            self.wardrobe_status.setText(f"{mode} · {outfit_label} · {accessory_label}")
        if kind in ("hello", "music", "state", "reset", "config_changed"):
            self._music_ui()
            self.action_button.setEnabled(self.state.connected and not self.state.paused)
        if kind == "error":
            self.notice(message.get("error") or "操作失败")
        if kind in ("hello", "like_goal", "state", "reset", "config_changed"):
            goal = self.state.like_goal
            active = goal.get("active")
            label = active["label"] if active else "等待达标" if goal.get("enabled", True) else "已关闭"
            self.like_status.setText(f"今日收到 {goal.get('total', 0):,} 赞 · 距下一档 {goal.get('remaining', 100):,} 赞\n"
                                     f"{label} · 待播 {goal.get('pending', 0)} 段")
            self.stunt_button.setEnabled(self.state.connected and not self.state.paused)
            self.test_likes.setEnabled(self.state.connected and not self.state.paused)

    def toggle_pause(self):
        self.client.send({"cmd": "resume" if self.state.paused else "pause"})

    def trigger_action(self):
        if self.state.paused:
            self.notice("直播已暂停，请先恢复直播")
            return
        self.client.send({"cmd": "reaction", "motion": self.action_input.currentData()})

    def _music_ui(self):
        view = music_view(self.state.snapshot(), time.monotonic())
        self.music_label.setText("当前：" + view["title"] + ("\n" + view["artist"] if view["artist"] else ""))
        self.next_music.setText("下一首：" + view["next_title"] + f"\n待播 {view['queue_count']} 首")
        self.music_time.setText(view["status"] + " · " + view["time"])
        self.music_progress.setValue(int((view["progress"] or 0) * 1000))
        self.music_play_button.setText("暂停音乐" if self.state.music.get("playing") else "播放音乐")

    def say(self):
        if self.state.paused:
            self.notice("直播已暂停，请先恢复直播；输入内容已保留")
            return
        text = self.say_input.toPlainText().strip()
        if text and self.client.send({"cmd": "say", "text": text}):
            self.say_input.clear()

    def request_song(self):
        text = self.song.text().strip()
        if text:
            self.client.post("/api/music/request", {"query": text, "user": "主播"})

    def toggle_music(self):
        self.client.post("/api/music/stop" if self.state.music.get("playing") else "/api/music/resume")

    def inject_chat(self):
        text = self.event_text.text().strip()
        if text and self.client.send({"cmd": "fake_danmu", "text": text}):
            self.event_text.clear()

    def save_settings(self):
        self.client.post("/api/config", {"persona": {"name": self.name_input.text().strip()},
                         "ingest": {"enabled": self.ingest_enabled.isChecked(),
                                    "source": self.source_input.currentData(), "douyin_room": self.room_input.text().strip()}})

    def show_preferences(self):
        from .preferences import Preferences
        dialog = Preferences(self.client, self, audio=self.audio)
        dialog.exec()
        dialog.deleteLater()

    def clear_session(self):
        if QMessageBox.question(self, "清空本场", "停止当前播报并清空弹幕和歌单？今日统计会保留。") == QMessageBox.StandardButton.Yes:
            self.client.post("/api/session/boot")

    def show_output(self):
        if self.popout is None:
            self.popout = Preview(self.frames)
            self.popout.setWindowTitle("深蓝 · 直播纯净画面")
            self._resize_popout(*self.worker.size)
        self.popout.show()
        self.popout.raise_()

    def set_orientation(self, orientation):
        size = orientation_size(*self.worker.requested_size, orientation)
        self.orientation_buttons[orientation].setChecked(True)
        self._pending_display_save = size
        self.worker.request_size(*size)
        self.notice("正在切换横屏布局…" if orientation == "landscape" else "正在切换竖屏布局…")

    def _output_size_text(self, width, height):
        direction = "竖屏" if height > width else "横屏"
        self.output_size_label.setText(f"当前输出：{direction}  {width} × {height}\n横屏三栏：数据与弹幕 / 人物舞台 / 榜单与音乐")

    def _resize_popout(self, width, height):
        if self.popout is None or self.popout.isFullScreen() or self.popout.isMaximized():
            return
        screen = self.popout.screen() or self.screen()
        available = screen.availableGeometry()
        scale = min(1, 960 / width, 960 / height, available.width() * 0.85 / width,
                    available.height() * 0.85 / height)
        self.popout.resize(round(width * scale), round(height * scale))

    def _sync_output_size(self):
        image, _ = self.frames.latest()
        if image is None:
            return
        size = (image.width(), image.height())
        if size != self._displayed_size:
            self._displayed_size = size
            self._output_size_text(*size)
            self._resize_popout(*size)
            self.render_label.setText(f"原生渲染   /   {size[0]} × {size[1]}   /   目标 {self.worker.target_fps} FPS")
        if size == self._pending_display_save:
            self._pending_display_save = None
            try:
                save_size(self.display_settings_path, *size)
                self.notice(("已切换横屏" if size[0] >= size[1] else "已切换竖屏") + " · 已记住选择，OBS 请同步调整画布比例")
            except OSError as error:
                self.notice(f"画面已切换，但无法记住选择：{error}")

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def save_snapshot(self):
        image, _ = self.frames.latest()
        if image:
            folder = ROOT / "logs" / "screenshots"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / time.strftime("直播画面-%Y%m%d-%H%M%S.png")
            if image.save(str(target)):
                self.notice("截图已保存：" + target.name)

    def _telemetry(self):
        self._music_ui()
        now = time.monotonic()
        if self.ai_state_label.isVisible() and self.state.connected and now - self._context_requested_at >= 2:
            if not self._context_pending or now - self._context_requested_at > 12:
                self._context_pending = True
                self._context_requested_at = now
                self.client.get("/api/ai/context")
        frames = self.frames
        image, _ = frames.latest()
        width, height = (image.width(), image.height()) if image is not None else self.worker.size
        self.render_label.setText(f"{width} × {height}   /   {frames.fps:.1f} FPS   /   {frames.render_ms:.1f} ms")
        if frames.error:
            self.notice("渲染异常：" + frames.error)
        data = {"pid": os.getpid(), "updated": time.time(), "backend": "pyside6",
                "fps": round(frames.fps, 1), "frames": frames.sequence, "error": frames.error,
                "output_url": self.output.url, "paused": self.state.paused}
        data["stream_fps"] = self.output.stream_fps
        data.update(width=width, height=height, orientation="portrait" if height > width else "landscape")
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.status_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.status_path)
        except OSError:
            pass

    def _ai_context_response(self, route, result):
        if route != "GET /api/ai/context":
            return
        self._context_pending = False
        context = result.get("context")
        if not context:
            self.ai_state_label.setText("AI 状态：暂时无法同步")
            return
        from ..brain.capabilities import status_line
        text = "AI 当前知道：" + status_line(context)
        operation = context.get("last_operation")
        if operation:
            text += "\n最近操作：" + ("" if operation["ok"] else "未执行 · ") + operation["result"]
        self.ai_state_label.setText(text)

    def closeEvent(self, event):
        self.timer.stop()
        self.client.close()
        self.video.close()
        self.audio.close()
        if self.popout:
            self.popout.close()
        self.worker.stop()
        self.output.close()
        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.status_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        event.accept()
