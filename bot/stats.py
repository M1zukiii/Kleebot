import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any


class ProfileStatsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record_play(self, guild_id: int | None, user_id: int) -> None:
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        profile["plays"] = int(profile.get("plays", 0)) + 1
        self._save(data)

    def record_fortune(self, guild_id: int | None, user_id: int, label: str, luck_delta: float) -> None:
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        profile["fortunes"] = int(profile.get("fortunes", 0)) + 1
        best = profile.get("best_fortune")
        if not isinstance(best, dict) or luck_delta > float(best.get("luck_delta", -101)):
            profile["best_fortune"] = {"label": label, "luck_delta": luck_delta}
        self._save(data)

    def record_bombed(self, guild_id: int | None, user_id: int) -> None:
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        profile["bombed"] = int(profile.get("bombed", 0)) + 1
        self._save(data)

    def claim_daily_bombs(self, guild_id: int | None, user_id: int) -> tuple[bool, int]:
        period = self._daily_period_key()
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        if profile.get("last_daily") == period:
            return False, int(profile.get("bombs", 0))
        profile["last_daily"] = period
        profile["bombs"] = int(profile.get("bombs", 0)) + 3
        self._save(data)
        return True, int(profile["bombs"])

    def spend_bomb(self, guild_id: int | None, user_id: int) -> tuple[bool, int]:
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        bombs = int(profile.get("bombs", 0))
        if bombs <= 0:
            return False, 0
        bombs -= 1
        profile["bombs"] = bombs
        self._save(data)
        return True, bombs

    def set_nickname(self, guild_id: int | None, user_id: int, nickname: str) -> str:
        nickname = nickname.strip()
        if len(nickname) > 32:
            raise ValueError("Nickname must be 32 characters or less.")
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        if nickname:
            profile["nickname"] = nickname
        else:
            profile.pop("nickname", None)
        self._save(data)
        return nickname

    def display_name(self, guild_id: int | None, user_id: int, fallback: str) -> str:
        data = self._load()
        profile = self._profile(data, guild_id, user_id, create=False)
        nickname = profile.get("nickname")
        return str(nickname) if nickname else fallback

    def profile_text(self, guild_id: int | None, user_id: int, display_name: str) -> str:
        data = self._load()
        profile = self._profile(data, guild_id, user_id, create=False)
        nickname = profile.get("nickname")
        shown_name = str(nickname) if nickname else display_name
        plays = int(profile.get("plays", 0))
        fortunes = int(profile.get("fortunes", 0))
        bombed = int(profile.get("bombed", 0))
        bombs = int(profile.get("bombs", 0))
        best = profile.get("best_fortune")
        if isinstance(best, dict):
            best_text = f"{best.get('label', '未知')}（{float(best.get('luck_delta', 0)):.3f}%）"
        else:
            best_text = "还没有记录"
        return (
            f"{shown_name} 的 Kleebot 档案\n"
            f"称呼：{shown_name}\n"
            f"点歌次数：{plays}\n"
            f"求签次数：{fortunes}\n"
            f"炸弹数量：{bombs}\n"
            f"被炸次数：{bombed}\n"
            f"历史最好签：{best_text}"
        )

    def leaderboard_text(self, guild_id: int | None, category: str = "plays", limit: int = 10) -> str:
        data = self._load()
        users = self._guild_users(data, guild_id)
        category = category.lower().strip()
        if category in {"play", "plays", "点歌"}:
            title = "点歌排行榜"
            entries = self._rank_numeric(users, "plays", limit)
            return self._format_numeric_leaderboard(title, entries, "次")
        if category in {"fortune", "fortunes", "求签"}:
            title = "求签排行榜"
            entries = self._rank_numeric(users, "fortunes", limit)
            return self._format_numeric_leaderboard(title, entries, "次")
        if category in {"luck", "幸运", "best"}:
            title = "历史最幸运排行榜"
            entries = self._rank_luck(users, limit)
            if not entries:
                return f"{title}\n还没有记录"
            lines = [title]
            for index, (user_id, label, luck_delta) in enumerate(entries, start=1):
                lines.append(f"{index}. <@{user_id}> - {label}（{luck_delta:.3f}%）")
            return "\n".join(lines)
        if category in {"bombed", "bomb", "被炸"}:
            title = "被炸排行榜"
            entries = self._rank_numeric(users, "bombed", limit)
            return self._format_numeric_leaderboard(title, entries, "次")
        raise ValueError("Unknown leaderboard. Choose one of: plays, fortunes, luck, bombed")

    def _profile(
        self,
        data: dict[str, Any],
        guild_id: int | None,
        user_id: int,
        create: bool = True,
    ) -> dict[str, Any]:
        guild_key = str(guild_id or "dm")
        user_key = str(user_id)
        guilds = data.setdefault("guilds", {}) if create else data.get("guilds", {})
        guild = guilds.setdefault(guild_key, {}) if create else guilds.get(guild_key, {})
        users = guild.setdefault("users", {}) if create else guild.get("users", {})
        if create:
            return users.setdefault(user_key, {})
        profile = users.get(user_key, {})
        return profile if isinstance(profile, dict) else {}

    def _guild_users(self, data: dict[str, Any], guild_id: int | None) -> dict[str, Any]:
        guild_key = str(guild_id or "dm")
        guilds = data.get("guilds", {})
        guild = guilds.get(guild_key, {}) if isinstance(guilds, dict) else {}
        users = guild.get("users", {}) if isinstance(guild, dict) else {}
        return users if isinstance(users, dict) else {}

    @staticmethod
    def _daily_period_key() -> str:
        now = datetime.now(UTC)
        reset_at = datetime.combine(now.date(), time(hour=9, tzinfo=UTC))
        period_date = now.date() if now >= reset_at else now.date() - timedelta(days=1)
        return period_date.isoformat()

    def _rank_numeric(self, users: dict[str, Any], field: str, limit: int) -> list[tuple[str, int]]:
        entries: list[tuple[str, int]] = []
        for user_id, profile in users.items():
            if not isinstance(profile, dict):
                continue
            value = int(profile.get(field, 0))
            if value > 0:
                entries.append((user_id, value))
        entries.sort(key=lambda item: item[1], reverse=True)
        return entries[:limit]

    def _rank_luck(self, users: dict[str, Any], limit: int) -> list[tuple[str, str, float]]:
        entries: list[tuple[str, str, float]] = []
        for user_id, profile in users.items():
            if not isinstance(profile, dict):
                continue
            best = profile.get("best_fortune")
            if not isinstance(best, dict):
                continue
            entries.append((user_id, str(best.get("label", "未知")), float(best.get("luck_delta", 0))))
        entries.sort(key=lambda item: item[2], reverse=True)
        return entries[:limit]

    @staticmethod
    def _format_numeric_leaderboard(title: str, entries: list[tuple[str, int]], unit: str) -> str:
        if not entries:
            return f"{title}\n还没有记录"
        lines = [title]
        for index, (user_id, value) in enumerate(entries, start=1):
            lines.append(f"{index}. <@{user_id}> - {value}{unit}")
        return "\n".join(lines)

    def _load(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"guilds": {}}
        return data if isinstance(data, dict) else {"guilds": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        temp_path.replace(self.path)
