import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any


class AiUsageStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def try_use(self, guild_id: int | None, user_id: int, kind: str, limit: int) -> tuple[bool, int]:
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        period = self._period_key()
        if profile.get("period") != period:
            profile.clear()
            profile["period"] = period
        used = int(profile.get(kind, 0))
        if used >= limit:
            return False, 0
        used += 1
        profile[kind] = used
        self._save(data)
        return True, limit - used

    def refund(self, guild_id: int | None, user_id: int, kind: str) -> None:
        data = self._load()
        profile = self._profile(data, guild_id, user_id)
        period = self._period_key()
        if profile.get("period") != period:
            return
        used = int(profile.get(kind, 0))
        if used <= 0:
            return
        profile[kind] = used - 1
        self._save(data)

    def _profile(self, data: dict[str, Any], guild_id: int | None, user_id: int) -> dict[str, Any]:
        guild_key = str(guild_id or "dm")
        user_key = str(user_id)
        guilds = data.setdefault("guilds", {})
        guild = guilds.setdefault(guild_key, {})
        users = guild.setdefault("users", {})
        return users.setdefault(user_key, {})

    @staticmethod
    def _period_key() -> str:
        now = datetime.now(UTC)
        reset_at = datetime.combine(now.date(), time(hour=9, tzinfo=UTC))
        period_date = now.date() if now >= reset_at else now.date() - timedelta(days=1)
        return period_date.isoformat()

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
