from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTabWidget, QTextEdit, QVBoxLayout, QWidget
from .actions import ACTIONS, ACCESSORIES, SKINS
from .line_editor import LineEditor
from ..like_goal import STUNTS
from ..world import CAMERAS, SCENES, WEATHERS


class Preferences(QDialog):
    def __init__(self, client, parent=None, audio=None):
        super().__init__(parent)
        self.client = client
        self.audio = audio
        self.setWindowTitle("直播设置")
        self.resize(650, 650)
        self.fields = {}
        self.saving = False
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.form = None
        self._tab("人设与 AI")
        self._field("persona", "name", "主播名字")
        self._field("persona", "style", "性格风格", "text")
        self._field("persona", "rules", "回复规则", "text")
        self._field("llm", "base_url", "模型接口地址")
        self._field("llm", "model", "模型名称")
        secret = self._field("llm", "api_key", "API 密钥")
        secret.setEchoMode(QLineEdit.EchoMode.Password)
        secret.setPlaceholderText("留空保留现有密钥或 .env 配置")
        self._tab("Token 用量与费用")
        self._field("stage", "show_tokens", "直播画面显示 Token / 估算费用", "bool")
        for key, label in (("price_in", "输入价格（元 / 百万 Token）"),
                           ("price_out", "输出价格（元 / 百万 Token）")):
            field = self._field("llm", key, label, (0, 1000000, 0.1))
            field.setDecimals(4)
        note = QLabel("输入与输出分别计价。按当前单价估算今日用量；同日重启恢复，跨天重新累计。\n"
                      "包含意图识别与回复；接口未返回完整用量时标注估算，费用仅供参考。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("语音与节奏")
        self._field("tts", "engine", "语音引擎", [("Edge 在线语音", "edge"), ("仅显示字幕", "none")])
        self._field("tts", "voice", "音色标识")
        self._field("tts", "rate", "语速（例如 +8%）")
        self._field("tts", "volume", "音量（例如 +0%）")
        self._field("pace", "min_speak_gap", "最小回复间隔（秒）", (0, 120, 0.1))
        self._field("pace", "idle_chat_seconds", "冷场等待（秒）", (0, 3600, 1))
        self._field("templates", "enabled", "启用预设互动话术", "bool")
        self._field("templates", "welcome", "进场欢迎", "bool")
        self._field("templates", "idle", "冷场主动聊天", "bool")
        self._field("templates", "gift", "礼物感谢", "bool")
        self._field("templates", "follow", "关注感谢", "bool")
        self._field("templates", "prefer_llm", "优先使用 AI 即兴台词", "bool")
        self._field("fallback", "offline_reply", "模型不可用时使用内置话术", "bool")
        self._field("fallback", "offline_voice", "朗读内置话术", "bool")
        self._field("pace", "max_reply_chars", "模型回复目标字数（按完整句收尾）", (20, 2000, 10))
        self._tab("内置话术")
        self.line_editor = LineEditor(client)
        self.form.addRow(self.line_editor)
        self._tab("冷场互动")
        self._field("idle", "dream_talk", "睡着后说梦话（保持睡姿）", "bool")
        self._field("idle", "dream_seconds", "梦话间隔（秒）", (15, 3600, 5))
        self._field("idle", "gameplay_invites", "主动邀请观众体验已开启的玩法", "bool")
        self._field("idle", "invite_seconds", "玩法邀请最短间隔（秒）", (30, 3600, 10))
        self._field("idle", "actions_enabled", "无人互动时自动做动作", "bool")
        self._field("idle", "action_seconds", "冷场动作间隔（秒）", (5, 3600, 1))
        self._field("idle", "movement_enabled", "普通场景久无互动时自主活动", "bool")
        self._field("idle", "movement_seconds", "自主活动前安静多久（秒）", (30, 3600, 5))
        self._field("idle", "scene_interactions", "自动到沙发小憩、窗边观景、喝茶等", "bool")
        self._field("idle", "interaction_seconds", "到达后停留时长（上下浮动 20%）", (8, 60, 1))
        self._field("idle", "scene_switching", "允许自主走到门口切换场景", "bool")
        self._field("idle", "scene_switch_seconds", "同一场景最少停留（秒）", (120, 3600, 30))
        self._field("idle", "movement_voice", "自主活动时说出想法（话术可编辑）", "bool")
        note = QLabel("小憩、观景后自动起身回中央，再等待下一轮。真实观众互动优先；观众主动要求睡觉时不会自动叫醒。常驻钓鱼使用独立的钓台小动作和换点规则。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._field("idle", "action_mode", "动作选择", [("依次轮换", "cycle"), ("随机不连续重复", "random")])
        self._field("idle", "speech_during_music", "音乐播放期间也允许冷场聊天", "bool")
        self.idle_actions = {}
        actions_widget = QWidget()
        actions_layout = QGridLayout(actions_widget)
        preferred = ["stretch", "peek", "float", "heart", "sleepy"]
        ordered = preferred + [key for key in ACTIONS if key not in preferred]
        for index, key in enumerate(ordered):
            box = QCheckBox(ACTIONS[key]["label"])
            self.idle_actions[key] = box
            actions_layout.addWidget(box, index // 2, index % 2)
        self.form.addRow(actions_widget)
        preview = QPushButton("预览一个冷场动作（使用已保存设置）")
        preview.clicked.connect(lambda: self.client.send({"cmd": "idle_preview"}))
        self.form.addRow(preview)
        note = QLabel("收到观众互动后重新计时；播报、待回复消息、点赞整活和暂停期间不会自动插入。\n"
                      "动作与冷场聊天独立开关；聊天间隔在「语音与节奏」设置，话题和邀请台词在「内置话术」修改。\n"
                      "普通场景自动散步约24秒；观众互动会让路，睡觉、驾驶或主动场景行为不被打断。\n"
                      "钓台的独立自动动作、换点和邀请请在‘钓鱼玩法’设置；这些功能不会自行进入钓鱼模式。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("音乐与画面")
        self._field("music", "enabled", "启用音乐", "bool")
        self._field("music", "autoplay", "自动播放队列", "bool")
        self._field("music", "show_player", "直播画面显示音乐播放器", "bool")
        self._field("music", "volume", "音乐音量（0–1）", (0, 1, 0.05))
        self._field("music", "duck", "说话时音乐音量比例", (0, 1, 0.05))
        self._tab("歌曲限制")
        self._field("music", "ai_dj", "启用 AI 自动点歌（仅限确认歌单）", "bool")
        field = self._field("music", "ai_allowlist", "已确认自动点歌歌单（每行：歌名 | 歌手）", "text")
        field.setPlaceholderText("请填写你已核对的歌曲；留空时不自动点歌。歌手可选。")
        field = self._field("music", "blocked_keywords", "额外禁播歌名 / 关键词（逗号或换行分隔）", "text")
        field.setPlaceholderText("可补充别名、歌手、专辑或分类关键词")
        note = QLabel("内置国歌、军歌、红歌等关键词及常见歌名限制；禁播规则优先于确认歌单。\n"
                      "弹幕、手动、礼物、本地曲库和 AI 共用检查。规则无法识别所有别名或音频内容，"
                      "自动歌单请先试听确认。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("人物与互动")
        self._field("stage", "scale", "人物大小", (0.1, 3, 0.05))
        self._field("stage", "offset_x", "人物水平位置（经典舞台）", (-1, 1, 0.01))
        self._field("stage", "offset_y", "人物垂直位置（经典舞台）", (-1, 1, 0.01))
        self._field("interact", "enabled", "自动响应弹幕 / 礼物动作", "bool")
        self._field("stage", "native_effects", "人物爱心 / 星光 / 彩纸特效", "bool")
        self._field("stage", "native_music_motion", "播放音乐时轻轻摇摆", "bool")
        self._field("stage", "scene_reactions", "画面联动：音乐开播跳舞 / 榜首更替庆祝", "bool")
        self._field("stage", "avatar_mode", "人物显示方式", [("分层骨架（可抬手 / 换衣）", "layered"), ("原始立绘", "image")])
        self._field("stage", "outfit", "人物服装", [(label, key) for key, label in SKINS.items()])
        self._field("stage", "costume", "人物配饰", [(label, key) for key, label in ACCESSORIES.items()])
        self._field("stage", "show_bones", "显示骨架辅助线（会进入直播画面）", "bool")
        self._field("stage", "show_ballpit", "送礼头像与昵称漂浮气泡", "bool")
        self._field("stage", "ball_max", "最多显示观众数（0–10）", (0, 10, 1))
        self._field("stage", "ball_rank_by", "头像气泡排行依据", [("礼物累计价值", "value"), ("礼物累计数量", "count")])
        self._field("stage", "ball_speed", "礼物气泡移动速度", (0.1, 3, 0.1))
        self._field("stage", "ball_size", "礼物气泡大小", (0.6, 1.8, 0.1))
        self._field("stage", "ball_bounce", "气泡碰到边缘回弹", "bool")
        for key, label in (("show_subtitle", "显示字幕"), ("show_danmu", "显示聊天室"),
                           ("show_datapods", "顶部统计图标（在线 / 弹幕 / 回应）"), ("show_ranking", "显示守护榜"), ("flip", "镜像人物")):
            self._field("stage", key, label, "bool")
        self._tab("三维场景")
        self._field("world", "enabled", "启用原生三维场景", "bool")
        self._field("world", "scene", "开播场景", [(label, key) for key, label in SCENES.items()])
        self._field("world", "weather", "开播天气", [(label, key) for key, label in WEATHERS.items()])
        self._field("world", "camera", "开播镜头", [(label, key) for key, label in CAMERAS.items()])
        self._field("world", "chat_control", "允许弹幕移动人物 / 转镜头 / 换天气", "bool")
        self._field("world", "cooldown", "弹幕场景操作间隔（秒）", (0, 120, .5))
        self._field("world", "particles", "显示雨雪和星光粒子", "bool")
        note = QLabel("三维家具与透视场景搭配现有二维骨架人物。室内雨雪出现在窗外。\n"
                      "弹幕示例：去窗边、往左游、拉近镜头、切换俯视、下雪、去露台。\n"
                      "操作台和弹幕调整只影响本场；这里保存开播默认值。关闭后恢复原来的舞台。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("钓鱼玩法")
        self._field("fishing", "enabled", "启用协力钓鱼", "bool")
        self._field("fishing", "dedicated", "钓鱼专用直播（常驻界面，自动续竿）", "bool")
        self._field("fishing", "chat_switch", "允许管理员 / 榜单前三切换常驻界面", "bool")
        self._field("fishing", "rank_control", "获得切换权限的今日榜单", [("钓鱼价值榜或礼物价值榜前三", "either"), ("仅钓鱼价值榜前三", "fishing"), ("仅礼物价值榜前三", "gifts"), ("仅管理员", "admins")])
        self._field("fishing", "admin_ids", "额外管理员用户ID（每行一个）", "text")
        self._field("fishing", "item_chance", "趣味物品咬钩概率（0.18 = 18%）", (0, .5, .01))
        self._field("fishing", "voice", "下竿 / 咬钩 / 催拉 / 收获互动语音", "bool")
        self._field("fishing", "likes_per_pull", "多少赞相当于拉一次钩", (1, 10000, 1))
        self._field("fishing", "escape_chance", "每次拉钩脱钩概率（0.06 = 6%）", (0, .5, .01))
        self._field("fishing", "bite_min", "等待咬钩最短时间（秒）", (3, 300, 1))
        self._field("fishing", "bite_max", "等待咬钩最长时间（秒）", (3, 300, 1))
        self._field("fishing", "fight_min", "溜鱼最短时间（秒）", (3, 300, 1))
        self._field("fishing", "fight_max", "溜鱼最长时间（秒）", (3, 300, 1))
        self._field("fishing", "gift_pulls", "每件礼物相当于拉钩次数", (2, 30, 1))
        self._field("fishing", "day_mode", "钓台昼夜", [("自动循环", "cycle"), ("跟随电脑当地时间", "realtime"), ("固定白天", "day"), ("固定夜晚", "night")])
        self._field("fishing", "day_cycle", "昼夜一轮时长（秒）", (120, 7200, 30))
        self._field("fishing", "net_enabled", "常驻时非咬钩礼物触发撒网", "bool")
        self._field("fishing", "net_multiplier", "每礼物价值单位对应图鉴分", (1, 1000, 1))
        self._field("fishing", "loosen_enabled", "允许弹幕松线减少进度", "bool")
        self._field("fishing", "loosen_amount", "松线减少进度（0.08 = 8个百分点）", (.01, .25, .01))
        self._field("fishing", "auto_actions", "常驻等待时自动观察 / 伸展 / 挪位 / 检查鱼线", "bool")
        self._field("fishing", "quiet_seconds", "自动小动作前安静多久（秒）", (10, 600, 5))
        self._field("fishing", "auto_relocate", "久无收获或连续空竿自动换钓点", "bool")
        self._field("fishing", "dry_seconds", "多久没有成功收获就换点（秒）", (60, 3600, 10))
        self._field("fishing", "failed_limit", "连续多少竿失败后换点", (1, 20, 1))
        self._field("fishing", "auto_voice", "常驻冷场主动介绍当前钓点与玩法", "bool")
        self._field("fishing", "auto_invite_seconds", "冷场邀请间隔（秒）", (30, 900, 5))
        note = QLabel("发“钓鱼 / 去钓鱼”开始一局。每竿独立抽取咬钩等待和溜鱼时限；提前拉满即可捕获。\n"
                      "本轮贡献 = 拉钩次数 + 实名点赞数 ÷ 每拉所需点赞数 + 礼物数量 × 礼物力度；最高者获得鱼获，同分先参与优先。\n"
                      "仅全场汇总且无法识别用户的点赞计入匿名合力，不会伪造名字。今日累计鱼获榜同日重启保留。\n"
                      "常驻模式允许跳舞、整活、场景互动后自动回钓台；场景行为暂演约20秒。换装直接保留。\n"
                      "发“跳水 / 花式跳水 / 下水游泳”：放竿、跳水、游圈、回台，约24秒，惊走当前鱼并重抛。\n"
                      "常驻时未咬钩的礼物触发专属撒网，图鉴分按礼物总价值×倍率×随机0.8～1.4换算。未知价值按数量估算。\n"
                      "撒网后继续原鱼竿；连送排队，同一用户待播礼物合并。关闭撒网或非专用直播时保留原礼物演出及助力。\n"
                      "发松减少进度（全场间隔2秒）；投饵缩短剩余等待35%（间隔12秒）；加油使基础脱钩风险减半10秒。\n"
                       "管理员或所选今日榜单前三可发‘切换钓鱼界面 / 进入钓鱼界面 / 退出钓鱼界面’。重复进入不会退出。\n"
                       "权限按接入源的管理员标记或用户ID判断；填写ID而非昵称。无身份数据的弹幕不能取得榜单权限。\n"
                       "今日水族和奇物按实际咬钩记录（含脱钩），在水面底部轮播，重启保留。过滤器等是可收藏计分的趣味道具。\n"
                       "久无互动的小动作不延后咬钩；咬钩、礼物、收获和跳水时不会自动换点。\n"
                       "无收获时间或失败竿数任一达标，等本竿结束后转场约10秒，到新钓点重抛。礼物排队保留。\n"
                       "可发换个钓点 / 去芦苇浅湾 / 去星礁海岸 / 回云汐钓台；AI知道当前钓点和自动行动原因。\n"
                       "价值为玩法积分，不可兑换；默认10分钟昼夜循环，亦可跟随电脑时间。暂停直播会冻结动作与循环。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._field("stage", "subtitle_size", "字幕字号（横竖屏自动适配）", (14, 26, 1))
        self._field("stage", "subtitle_backing", "字幕增加轻薄底色（默认仅描边）", "bool")
        self._tab("礼物演出")
        self._field("gift_show", "enabled", "完整礼物动作、道具与送礼者名片", "bool")
        self._field("gift_show", "voice", "演出开始时专属感谢语音", "bool")
        self._field("gift_show", "beer_video", "大啤酒随机播放奶蛙（否则热茶回礼）", "bool")
        note = QLabel("鲜花捧花炫耀、巨型棒棒糖、墨镜走秀、爱心拥抱、火箭与加冕等专属演出，每段 10～12 秒。\n"
                      "礼物按收到顺序完整播放，同一用户等待中的同款连送合并展示数量。演出期间冻结钓鱼与场景，结束后恢复。\n"
                      "奶蛙需要在互动视频中配置有效文件；未就绪时保留热茶动作。所有互动台词可在内置话术中修改。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("背景 BGM")
        self._field("bgm", "enabled", "循环播放背景音乐", "bool")
        self.bgm_file = self._field("bgm", "path", "本地音频路径")
        self.bgm_file.textEdited.connect(self._bgm_edited)
        choose = QPushButton("选择背景音乐文件…")
        choose.clicked.connect(self._choose_bgm)
        self.form.addRow(choose)
        self._field("bgm", "volume", "背景音量（0–1）", (0, 1, .05))
        self._field("bgm", "duck", "说话 / 视频期间音量比例", (0, 1, .05))
        self.bgm_status = QLabel(audio.bgm_status() if audio else "保存后在工作台播放背景音乐")
        self.bgm_status.setWordWrap(True)
        self.form.addRow("当前播放状态", self.bgm_status)
        if audio:
            audio.bgm_status_changed.connect(self.bgm_status.setText)
            retry = QPushButton("重试已保存的 BGM")
            retry.clicked.connect(audio.retry_bgm)
            self.form.addRow(retry)
        note = QLabel("选择文件后自动勾选开启，保存后循环播放。点歌实际播放时 BGM 暂歇，停止或失败后恢复。直播暂停、断线时暂停。\n"
                      "此处状态对应已保存配置。OBS 媒体源只有画面，声音请添加本工作台的应用程序音频捕获。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("滚动公告")
        self._field("ticker", "enabled", "显示底部滚动公告", "bool")
        self._field("ticker", "text", "玩法介绍（最多 2000 字）", "text")
        self._field("ticker", "speed", "滚动速度（设计像素 / 秒）", (5, 240, 5))
        note = QLabel("从右向左循环滚动；换行会合并成分隔符。保存后立即生效，空文本隐藏公告。横竖屏均预留底部位置。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("点赞整活")
        self._field("like_goal", "enabled", "启用点赞达标整活", "bool")
        self._field("like_goal", "show_progress", "直播画面显示点赞目标进度", "bool")
        field = self._field("like_goal", "step", "每增加多少赞触发一次", (1, 1000000, 10))
        field.setDecimals(0)
        self._field("like_goal", "cooldown", "两段整活之间休息（秒）", (0, 120, 1))
        self._field("like_goal", "mode", "整活选择", [("轮流播放", "cycle"), ("随机播放", "random")]
                    + [(value["label"], key) for key, value in STUNTS.items()])
        note = QLabel("默认每新增 100 赞表演一次；只统计本次接入后的点赞，不补播开播前的历史目标。\n"
                      "新增 16 秒流光环游，也可播放骑车、蛛丝摆荡和火箭。最多等待 3 段。\n"
                      "暂停时冻结动画和目标进度；恢复后续播。修改阈值后从当前累计值重新开始目标。")
        note.setWordWrap(True)
        self.form.addRow(note)
        self._tab("互动视频")
        self._field("stage", "video_enabled", "启用奶蛙互动视频", "bool")
        self._field("stage", "video_path", "本地视频路径（支持透明 WebM，最多 8 秒）")
        self._field("stage", "video_keywords", "弹幕触发词（用逗号分隔）")
        self._field("stage", "video_volume", "视频音量（0–1）", (0, 1, 0.05))
        self._field("stage", "video_scale", "视频显示大小", (0.4, 1.4, 0.1))
        self.status = QLabel("正在读取设置…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        close = QPushButton("取消")
        close.clicked.connect(self.reject)
        self.save = QPushButton("保存并生效")
        self.save.setObjectName("primary")
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        buttons.addWidget(close)
        buttons.addWidget(self.save)
        layout.addLayout(buttons)
        self.client.response.connect(self._response)
        self.client.get("/api/config")

    def _tab(self, label):
        content = QWidget()
        self.form = QFormLayout(content)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, label)

    def _field(self, section, key, label, kind="line"):
        if kind == "bool":
            field = QCheckBox()
        elif kind == "text":
            field = QTextEdit()
            field.setMaximumHeight(90)
        elif isinstance(kind, list):
            field = QComboBox()
            for title, value in kind:
                field.addItem(title, value)
        elif isinstance(kind, tuple):
            field = QDoubleSpinBox()
            field.setRange(kind[0], kind[1])
            field.setSingleStep(kind[2])
        else:
            field = QLineEdit()
        self.form.addRow(label, field)
        self.fields[(section, key)] = field
        return field

    def _choose_bgm(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择背景音乐", self.bgm_file.text(),
                                             "音频文件 (*.mp3 *.wav *.ogg *.flac *.m4a *.aac);;所有文件 (*)")
        if path:
            self.bgm_file.setText(path)
            self._bgm_edited(path)

    def _bgm_edited(self, path):
        if path.strip():
            self.fields[("bgm", "enabled")].setChecked(True)
            self.bgm_status.setText("已选择背景音乐并勾选开启，点击“保存并生效”开始播放")

    def _response(self, route, result):
        if route == "GET /api/config":
            if result.get("error"):
                self.status.setText("读取失败：" + result["error"])
                return
            self.line_editor.load(result)
            from ..settings_store import DEFAULTS
            idle_actions = (result.get("idle") or DEFAULTS["idle"]).get("actions", [])
            for key, box in self.idle_actions.items():
                box.setChecked(key in idle_actions)
            for (section, key), field in self.fields.items():
                value = (result.get(section) or {}).get(key)
                if key == "api_key" or value is None:
                    continue
                if isinstance(field, QCheckBox):
                    field.setChecked(bool(value))
                elif isinstance(field, QDoubleSpinBox):
                    field.setValue(float(value))
                elif isinstance(field, QComboBox):
                    field.setCurrentIndex(max(0, field.findData(value)))
                elif isinstance(field, QTextEdit):
                    field.setPlainText(str(value))
                else:
                    field.setText(str(value))
            self.save.setEnabled(True)
            self.status.setText("保存后立即生效；密钥留空保留原配置。")
        elif route == "POST /api/config" and self.saving:
            self.saving = False
            self.save.setEnabled(True)
            if result.get("ok"):
                self.accept()
            else:
                self.status.setText(result.get("error") or "保存失败")

    def _save(self):
        patch = {}
        for (section, key), field in self.fields.items():
            if isinstance(field, QCheckBox):
                value = field.isChecked()
            elif isinstance(field, QDoubleSpinBox):
                value = field.value()
            elif isinstance(field, QComboBox):
                value = field.currentData()
            elif isinstance(field, QTextEdit):
                value = field.toPlainText().strip()
            else:
                value = field.text().strip()
            if key == "api_key" and not value:
                continue
            patch.setdefault(section, {})[key] = value
        line_patch = self.line_editor.patch()
        patch["lines"] = line_patch["lines"]
        patch.setdefault("interact", {}).update(line_patch["interact"])
        patch.setdefault("idle", {})["actions"] = [key for key, box in self.idle_actions.items() if box.isChecked()]
        self.saving = True
        self.save.setEnabled(False)
        self.status.setText("正在保存…")
        self.client.post("/api/config", patch)
