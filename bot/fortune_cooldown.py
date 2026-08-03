import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any


RESET_TIME_UTC = time(hour=9, tzinfo=UTC)
COOLDOWN_MESSAGE = "你的求签指令还在冷却中哦 ~\n它会自动在每天的UTC（格林威治时区GMT）9点整（24小时制）刷新冷却。"


@dataclass(frozen=True)
class FortuneCooldown:
    active: bool
    message: str | None = None


class FortuneCooldownStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def check(self, guild_id: int | None, user_id: int) -> FortuneCooldown:
        now = datetime.now(UTC)
        records = self._load()
        key = self._key(guild_id, user_id)
        reset_at = self._parse_datetime(records.get(key))
        if reset_at and reset_at > now:
            return FortuneCooldown(active=True, message=COOLDOWN_MESSAGE)
        if key in records:
            records.pop(key, None)
            self._save(records)
        return FortuneCooldown(active=False)

    def mark_used(self, guild_id: int | None, user_id: int) -> None:
        records = self._load()
        records[self._key(guild_id, user_id)] = self._next_reset().isoformat()
        self._save(records)

    def _next_reset(self) -> datetime:
        now = datetime.now(UTC)
        reset_at = datetime.combine(now.date(), RESET_TIME_UTC)
        if now >= reset_at:
            reset_at += timedelta(days=1)
        return reset_at

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _save(self, records: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _key(guild_id: int | None, user_id: int) -> str:
        return f"{guild_id or 'dm'}:{user_id}"

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

