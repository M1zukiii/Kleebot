import hashlib
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Fortune:
    rank: str
    title: str
    text: str
    advice: str
    color: int


FORTUNES = [
    Fortune("大吉", "风起云开", "看起来今天很走运呀。机会会自己敲门，但你也要记得开门。", "把最想推进的一件事先做掉。", 0xF5B642),
    Fortune("中吉", "稳步向前", "今天的运气不吵闹，但很可靠，适合补进度和整理计划。", "少开新坑，多收尾。", 0x74C69D),
    Fortune("小吉", "微光可循", "会有一点小惊喜，也可能是别人一句刚好的提醒。", "留意消息，不要错过顺手的机会。", 0x80C7FF),
    Fortune("吉", "清水见月", "状态清醒，适合做需要判断力的选择。", "相信第一轮认真思考后的答案。", 0x9BD48B),
    Fortune("末吉", "慢热之日", "事情会慢一点，但不是坏事，只是需要耐心。", "别急着判定失败，再等一等。", 0xC8B6FF),
    Fortune("半吉", "云边有路", "今天适合试探，不适合一次压太多筹码。", "先做一个小版本。", 0x95D5B2),
    Fortune("平", "风平浪静", "没有强烈波动，适合把生活调回舒服的节奏。", "给自己留一点空白时间。", 0xADB5BD),
    Fortune("小凶", "细雨沾衣", "容易被小事打断，情绪和注意力都要省着用。", "重要决定放到晚一点。", 0xFFD166),
    Fortune("凶", "雾里看花", "信息可能不完整，越急越容易判断偏。", "先确认事实，再行动。", 0xEF476F),
    Fortune("大凶", "逆风行舟", "看起来今天很不走运呀。但至少你有一个好记忆力，Klee 甚至连这个都没有...", "少争输赢，多保状态。", 0x6C757D),
]


@dataclass(frozen=True)
class FortuneDraw:
    fortune: Fortune
    luck_percent: float


def draw_daily_fortune(user_id: int, guild_id: int | None = None, today: date | None = None) -> FortuneDraw:
    current_date = today or date.today()
    seed = f"{current_date.isoformat()}:{guild_id or 'dm'}:{user_id}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    index = int.from_bytes(digest[:4], "big") % len(FORTUNES)
    luck_percent = round(int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF * 100, 3)
    return FortuneDraw(fortune=FORTUNES[index], luck_percent=luck_percent)


def fortune_message(user_name: str, user_id: int, guild_id: int | None = None) -> str:
    draw = draw_daily_fortune(user_id=user_id, guild_id=guild_id)
    fortune = draw.fortune
    return (
        f"{user_name} 今日求签：{fortune.rank} - {fortune.title}\n"
        f"{fortune.text}\n"
        f"运势：{draw.luck_percent:.3f}%\n"
        f"建议：{fortune.advice}"
    )


def luck_summary(luck_percent: float) -> str:
    delta = abs(luck_percent - 50)
    if luck_percent >= 85:
        label = "非常幸运"
    elif luck_percent >= 65:
        label = "比较幸运"
    elif luck_percent >= 50:
        label = "稍微幸运"
    elif luck_percent >= 35:
        label = "稍微不走运"
    elif luck_percent >= 15:
        label = "比较不走运"
    else:
        label = "非常不走运"

    direction = "幸运" if luck_percent >= 50 else "不走运"
    return f"{label}（比平均值{direction}了 {delta:.3f}%）"
