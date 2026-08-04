import json
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

    def profile_text(self, guild_id: int | None, user_id: int, display_name: str) -> str:
        data = self._load()
        profile = self._profile(data, guild_id, user_id, create=False)
        plays = int(profile.get("plays", 0))
        fortunes = int(profile.get("fortunes", 0))
        best = profile.get("best_fortune")
        if isinstance(best, dict):
            best_text = f"{best.get('label', '未知')}（{float(best.get('luck_delta', 0)):.3f}%）"
        else:
            best_text = "还没有记录"
        return (
            f"{display_name} 的 Kleebot 档案\n"
            f"点歌次数：{plays}\n"
            f"求签次数：{fortunes}\n"
            f"历史最好签：{best_text}"
        )

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
