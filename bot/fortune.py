import hashlib
import secrets
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Fortune:
    title: str
    text: str
    advice: str


@dataclass(frozen=True)
class FortuneResult:
    fortune: Fortune
    luck_delta: float
    label: str
    can_reroll: bool = False
    first_fortune: Fortune | None = None
    first_luck_delta: float | None = None
    first_label: str | None = None

    @property
    def used_second_chance(self) -> bool:
        return self.first_fortune is not None


FORTUNES = [
    Fortune("风起云开", "看起来今天很走运呀。机会会自己敲门，但你也要记得开门。", "把最想推进的一件事先做掉。"),
    Fortune("稳步向前", "今天的运气不吵闹，但很可靠，适合补进度和整理计划。", "少开新坑，多收尾。"),
    Fortune("微光可循", "会有一点小惊喜，也可能是别人一句刚好的提醒。", "留意消息，不要错过顺手的机会。"),
    Fortune("清水见月", "状态清醒，适合做需要判断力的选择。", "相信第一轮认真思考后的答案。"),
    Fortune("慢热之日", "事情会慢一点，但不是坏事，只是需要耐心。", "别急着判定失败，再等一等。"),
    Fortune("云边有路", "今天适合试探，不适合一次压太多筹码。", "先做一个小版本。"),
    Fortune("风平浪静", "没有强烈波动，适合把生活调回舒服的节奏。", "给自己留一点空白时间。"),
    Fortune("细雨沾衣", "容易被小事打断，情绪和注意力都要省着用。", "重要决定放到晚一点。"),
    Fortune("雾里看花", "信息可能不完整，越急越容易判断偏。", "先确认事实，再行动。"),
    Fortune("逆风行舟", "看起来今天很不走运呀。但至少你有一个好记忆力，Klee 甚至连这个都没有...", "少争输赢，多保状态。"),
]

FORTUNE_TEXT_BY_LABEL = {
    "非常不走运": "看起来今天很不走运呀。希望和Klee一起去去炸鱼的时候不要被琴团长抓住啦...",
    "不太走运": "有一点点不幸运。Klee发现了四叶草！把好运分给你呀~",
    "一般般": "嗯...不好不坏。要不要和Klee一起去试试妈妈最新研发的炸弹！",
    "好运": "好运气！ 今天会是个适合炸鱼的好日子~",
    "非常幸运": "哇，你也太幸运了！可以把好运分给Klee一点嘛~",
    "极其幸运": "哇！Klee从未见过有人像你一样幸运！不愧是荣誉骑士呀！",
}


def draw_daily_fortune(user_id: int, guild_id: int | None = None, today: date | None = None) -> FortuneResult:
    seed = _base_seed(user_id=user_id, guild_id=guild_id, today=today)
    first = _draw_from_seed(f"{seed}:first")
    return FortuneResult(
        fortune=first.fortune,
        luck_delta=first.luck_delta,
        label=first.label,
        can_reroll=first.luck_delta <= -10 and _second_chance(seed, first.luck_delta),
    )


def draw_second_fortune(first: FortuneResult, user_id: int, guild_id: int | None = None) -> FortuneResult:
    seed = f"{secrets.token_hex(8)}:{guild_id or 'dm'}:{user_id}:second"
    second = _draw_from_seed(seed)
    return FortuneResult(
        fortune=second.fortune,
        luck_delta=second.luck_delta,
        label=second.label,
        first_fortune=first.fortune,
        first_luck_delta=first.luck_delta,
        first_label=first.label,
    )


def fortune_message(user_name: str, user_id: int, guild_id: int | None = None) -> str:
    result = draw_daily_fortune(user_id=user_id, guild_id=guild_id)
    lines = [f"{user_name} 今日求签：{result.label} - {result.fortune.title}"]
    if result.used_second_chance and result.first_fortune:
        lines.append(f"第一签：~~{result.first_label} - {result.first_fortune.title}~~")
    lines.extend([fortune_text(result), f"运势：{luck_summary(result)}"])
    return "\n".join(lines)


def fortune_text(result: FortuneResult) -> str:
    return FORTUNE_TEXT_BY_LABEL.get(result.label, result.fortune.text)


def luck_summary(result: FortuneResult) -> str:
    return _delta_text(result.luck_delta, result.label)


def _draw_from_seed(seed: str) -> FortuneResult:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    fortune = FORTUNES[int.from_bytes(digest[:4], "big") % len(FORTUNES)]
    luck_delta = _signed_percent(digest[4:8])
    return FortuneResult(fortune=fortune, luck_delta=luck_delta, label=_luck_label(luck_delta))


def _base_seed(user_id: int, guild_id: int | None = None, today: date | None = None) -> str:
    current_date = today or date.today()
    nonce = f"{current_date.isoformat()}:{secrets.token_hex(8)}"
    return f"{nonce}:{guild_id or 'dm'}:{user_id}"


def _signed_percent(raw: bytes) -> float:
    value = int.from_bytes(raw, "big") / 0xFFFFFFFF
    return round((value * 200) - 100, 3)


def _second_chance(seed: str, luck_delta: float) -> bool:
    chance = _second_chance_probability(luck_delta)
    digest = hashlib.sha256(f"{seed}:second-chance".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF < chance


def _second_chance_probability(luck_delta: float) -> float:
    if luck_delta > -10:
        return 0.0
    severity = min(1.0, max(0.0, (abs(luck_delta) - 10) / 90))
    return 0.10 + (0.396 - 0.10) * severity


def _luck_label(luck_delta: float) -> str:
    if luck_delta >= 98.5:
        return "极其幸运"
    if luck_delta >= 60:
        return "非常幸运"
    if luck_delta >= 10:
        return "好运"
    if luck_delta > -10:
        return "一般般"
    if luck_delta > -60:
        return "不太走运"
    return "非常不走运"


def luck_color(label: str) -> int:
    colors = {
        "非常不走运": 0xADB5BD,
        "不太走运": 0xC7F9CC,
        "一般般": 0x57CC99,
        "好运": 0x4D96FF,
        "非常幸运": 0xF5B642,
        "极其幸运": 0xEF476F,
    }
    return colors.get(label, 0x57CC99)


def _delta_text(luck_delta: float, label: str) -> str:
    if -10 < luck_delta < 10:
        return f"{label}（和平均值差不多）"
    direction = "幸运" if luck_delta >= 10 else "不走运"
    return f"{label}（比平均值{direction}了 {abs(luck_delta):.3f}%）"
