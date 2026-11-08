from __future__ import annotations

import random


OFFLINE_DEFAULTS = {
    "greet": [
        "{name}好呀，我是大肥鱼，一条会聊天的鱼",
        "哟，{name}来了，来了就是鱼友",
        "{name}晚上好，今天过得咋样",
    ],
    "age": [
        "按鱼的算法我还很年轻，按体重算我大概是资深玩家",
        "年龄是秘密，反正比这缸水老一点",
    ],
    "food": [
        "别提吃的，我一听就饿，可惜直播间只有弹幕没有外卖",
        "光听你说我就流口水了，鱼也是有胃口的",
    ],
    "sing": [
        "我这嗓子唱出来像水烧开，你确定要听吗",
        "唱歌可以，但你要负责后果啊",
    ],
    "praise": [
        "谢谢夸奖，我知道我长得挺离谱，但可爱这件事我很自信",
        "嘴真甜，我这条鱼都要脸红了",
        "被你这么一夸，我尾巴都翘起来了",
    ],
    "intro": [
        "我是大肥鱼，一条会聊天的鱼，以后常来玩",
        "我是这缸里的主播，专职陪你唠嗑",
    ],
    "how": [
        "这个问题我琢磨过，急不得，鱼生很长，慢慢来",
        "你这么一问，我脑袋里的水都晃了一下，我想想啊",
    ],
    "mood": [
        "{name}辛苦了，先深呼吸一下，我陪你说说话",
        "别急，天塌下来有我这身材顶着",
        "{name}，累了就歇会儿，直播间不跑",
    ],
    "fallback": [
        "你这话我接住了，就是一时不知道怎么回，再说一句",
        "嗯，说得好有道理，我先记在小本本上",
        "我在认真听，你继续说",
        "这个话题有点深，鱼脑需要转一会儿",
        "哈哈，你这么一说我还真没想过",
    ],
    "short": [
        "哈哈，我看你笑得比我还开心",
        "收到，鱼友的话我都记着呢",
        "嗯嗯，我在呢，接着说呀",
        "你这一句我都听懂了，默契",
    ],
    "emoji": [
        "谢谢你的心意，我都收到啦",
        "这一串我看懂了，谢谢啊",
        "看你这表情我就乐了",
    ],
}

OFFLINE_LABELS = {"greet": "打招呼", "age": "年龄", "food": "吃饭", "sing": "才艺",
                  "praise": "被夸奖", "intro": "自我介绍", "how": "问题回应", "mood": "安慰陪伴",
                  "fallback": "普通聊天", "short": "简短弹幕", "emoji": "表情弹幕"}
EXTRA_LINES = {
    "welcome_group": ["欢迎{names}进直播间"],
    "welcome_many": ["欢迎新进来的{count}位鱼友", "又来了{count}位朋友，欢迎欢迎"],
    "follow_batch": ["另外谢谢刚才{count}位鱼友点了关注"],
    "like_thanks": ["{name}点了{count}个赞，谢谢你的小心心"],
    "action_ack": ["好呀，给你表演{action}"],
    "costume_ack": ["换上{costume}啦"],
    "idle_dj": ["点首歌送给大家，气氛搞起来"],
    "idle_fishing": ["有点想去钓鱼了，你可以发钓鱼。咬钩后发拉钩，点赞也能帮我收线。",
                     "要不要一起去钓鱼？发去钓鱼就能出发，本轮出力最多的鱼友钓获后还能上今日鱼获榜。"],
    "fishing_waiting": ["新的一竿下水啦，看看这次会钓到什么！", "浮漂已经放好了，可以发投饵帮我引鱼，咬钩后发拉钩或加油！"],
    "fishing_net_cast": ["谢谢{name}送的{gift}！这就为你撒一网，看看今天能收获什么宝贝！"],
    "fishing_net_caught": ["恭喜{name}！这一网捕到了{fish}，一共{count}条，收获{value}图鉴分！这些鱼获都记在你的名下啦！"],
    "fishing_diving": ["鱼竿先放一放，我要跳水啦！看看这次能溅起多大的水花，游几圈就回来钓鱼。", "来一段花式跳水，鱼儿们先让一让，我游完就回来重新抛竿！"],
    "fishing_hooked": ["有鱼上钩啦！快发拉钩，或者点点赞，帮我把它拉上来！", "浮漂动了！鱼友们快发拉，咱们一起收线！"],
    "fishing_remind": ["这条鱼还在挣扎，快发拉钩，别让它溜走啦！", "鱼线绷紧了，点赞也能帮忙，咱们再加把劲！"],
    "fishing_caught": ["恭喜{name}！这条{fish}价值{value}，你是本轮出力最多的鱼友！"],
    "fishing_community": ["大家一起钓到了{fish}，价值{value}！这次没有收到实名贡献，下次发拉钩就能记录你的名字啦。"],
    "fishing_item_hooked": ["钩住了一个神秘物品！发拉钩或点点赞，帮我把它打捞上来！"],
    "fishing_item_remind": ["这件宝贝有点沉，快发拉钩，我们一起把它捞上来！"],
    "fishing_item_caught": ["恭喜{name}！你帮大家捞到了{fish}，图鉴价值{value}分！"],
    "fishing_item_community": ["大家合力捞到了{fish}，图鉴价值{value}分！发拉钩就能记录你的贡献名字啦。"],
    "fishing_item_escaped": ["哎呀，宝贝滑回水里了，下次再来打捞！"],
    "fishing_escaped": ["哎呀，鱼儿溜走了，别灰心，下一竿再来！", "这条鱼太灵活了，下次咬钩我们早点拉！"],
    "dream_talk": ["呼噜……梦里也想跟你们玩，发醒醒就能叫醒我哦。", "嗯……谁在偷偷点歌呀？叫我醒醒，我们一起玩。", "我梦见钓到一条大鱼……鱼友们，快叫醒我陪你玩呀。"],
    "gift_tea": ["谢谢{name}的大啤酒！我用热茶回敬你，先吹一吹，再喝一口。"],
    "gift_video": ["谢谢{name}的大啤酒！送你一段奶蛙表演，快乐加倍！"],
    "gift_flowers": ["谢谢{name}送的{gift}！抱紧这束花，给大家看看，真好看！"],
    "gift_lollipop": ["谢谢{name}的棒棒糖！这么大一颗，举起来给你炫耀一下！"],
    "gift_sunglasses": ["谢谢{name}的墨镜！戴上，摆个造型，今天和你一起耍帅！"],
    "gift_special": ["谢谢{name}送的{gift}！这场专属表演送给你！"],
    "idle_scene": ["想换个风景吗？发换个场景，或者出门、回家，我们一起逛逛。"],
    "idle_outfit": ["今天想看我穿哪套？发换装就能换下一套衣服，再发一次还会继续换。"],
    "idle_stunt": ["想看我整点活吗？发环游或者荡蛛丝，给你表演一段。"],
    "idle_song": ["想听什么歌呀？发播放加歌名，我帮你加入点歌队列。"],
    "idle_invites": ["想看我比心还是转圈？发弹幕告诉我，我来表演。", "伸个懒腰，陪我放松一下吧。",
                     "潜水的鱼友，发个你好，让我看看今天谁在陪我。", "给我的新造型起个名字吧，我等你们的脑洞。"],
    **{"offline_" + key: values for key, values in OFFLINE_DEFAULTS.items()},
}
LINE_LABELS = {"welcome": "进场欢迎", "welcome_group": "多人欢迎", "welcome_many": "批量欢迎",
               "follow": "关注感谢", "follow_batch": "合并关注感谢", "thanks_small": "普通礼物感谢",
               "thanks_big": "大额礼物感谢", "like_thanks": "点赞感谢", "action_ack": "动作回应",
               "costume_ack": "换装回应", "idle_topics": "冷场话题", "idle_invites": "冷场互动邀请",
               "idle_dj": "自动点歌开场", "idle_fishing": "玩法邀请 · 钓鱼", "idle_scene": "玩法邀请 · 场景",
               "idle_outfit": "玩法邀请 · 换装", "idle_stunt": "玩法邀请 · 整活", "idle_song": "玩法邀请 · 点歌",
               "fishing_waiting": "钓鱼 · 下竿", "fishing_hooked": "钓鱼 · 咬钩提醒", "fishing_remind": "钓鱼 · 再次催拉",
               "fishing_caught": "钓鱼 · 贡献冠军", "fishing_community": "钓鱼 · 匿名合力", "fishing_escaped": "钓鱼 · 脱钩",
                "fishing_diving": "钓鱼 · 花式跳水",
                "fishing_net_cast": "钓鱼 · 礼物撒网", "fishing_net_caught": "钓鱼 · 撒网恭喜送礼者",
                "fishing_item_hooked": "奇物 · 钩住提醒", "fishing_item_remind": "奇物 · 再次催拉",
                "fishing_item_caught": "奇物 · 贡献冠军", "fishing_item_community": "奇物 · 匿名合力", "fishing_item_escaped": "奇物 · 滑落",
               "dream_talk": "睡觉 · 梦话", "gift_tea": "礼物 · 热茶", "gift_video": "礼物 · 奶蛙",
               "gift_flowers": "礼物 · 鲜花", "gift_lollipop": "礼物 · 棒棒糖", "gift_sunglasses": "礼物 · 墨镜",
               "gift_special": "礼物 · 其它专属演出",
               **{"offline_" + key: "离线 · " + label for key, label in OFFLINE_LABELS.items()}}
LINE_PARAMETERS = {"welcome": {"a"}, "welcome_group": {"names"}, "welcome_many": {"count"},
                   "follow": {"name"}, "follow_batch": {"count"}, "thanks_small": {"name", "gift"},
                   "thanks_big": {"name", "gift"}, "like_thanks": {"name", "count"},
                   "action_ack": {"action"}, "costume_ack": {"costume"},
                   "fishing_caught": {"name", "fish", "value"}, "fishing_community": {"fish", "value"},
                   "fishing_net_cast": {"name", "gift", "count", "value", "fish"},
                   "fishing_net_caught": {"name", "gift", "count", "value", "fish"},
                   "fishing_item_caught": {"name", "fish", "value"}, "fishing_item_community": {"fish", "value"},
                   **{key: {"name", "gift"} for key in ("gift_tea", "gift_video", "gift_flowers", "gift_lollipop", "gift_sunglasses", "gift_special")},
                   **{"offline_" + key: {"name"} for key in OFFLINE_DEFAULTS}}
SAMPLE_PARAMETERS = {"a": "小鱼", "name": "小鱼", "names": "小鱼、星光", "count": 3,
                     "gift": "小心心", "action": "比心", "costume": "墨镜派对", "fish": "锦绣红鲤", "value": 420}


def render_line(key: str, **values) -> str:
    from .settings_store import DEFAULTS, store

    pool = store.lines(key) or DEFAULTS["lines"].get(key, [])
    return random.choice(pool).format(**values) if pool else ""

