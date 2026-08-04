import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_AFK_REASON = "被关禁闭了"


class AfkStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def set_afk(self, guild_id: int | None, user_id: int, reason: str) -> None:
        data = self._load()
        guild = self._guild(data, guild_id)
        guild[str(user_id)] = {
            "reason": reason.strip() or DEFAULT_AFK_REASON,
            "since": datetime.now(timezone.utc).isoformat(),
        }
        self._save(data)

    def clear_afk(self, guild_id: int | None, user_id: int) -> bool:
        data = self._load()
        guild = self._guild(data, guild_id, create=False)
        if str(user_id) not in guild:
            return False
        del guild[str(user_id)]
        self._save(data)
        return True

    def get_afk(self, guild_id: int | None, user_id: int) -> dict[str, str] | None:
        data = self._load()
        guild = self._guild(data, guild_id, create=False)
        entry = guild.get(str(user_id))
        return entry if isinstance(entry, dict) else None

    def _guild(self, data: dict[str, Any], guild_id: int | None, create: bool = True) -> dict[str, Any]:
        guild_key = str(guild_id or "dm")
        guilds = data.setdefault("guilds", {}) if create else data.get("guilds", {})
        guild = guilds.setdefault(guild_key, {}) if create else guilds.get(guild_key, {})
        return guild if isinstance(guild, dict) else {}

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
