import asyncio
import os
from dataclasses import dataclass
from typing import Any

import yt_dlp


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    duration: int | None
    requester: str


class Resolver:
    def __init__(self) -> None:
        self.cookies = os.getenv("YTDLP_COOKIES", "/app/data/cookies.txt")

    async def resolve(self, query: str, requester: str) -> Track:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._resolve_sync, query, requester)

    def _resolve_sync(self, query: str, requester: str) -> Track:
        target = query if self._looks_like_url(query) else f"ytsearch1:{query}"
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "auto",
            "source_address": "0.0.0.0",
        }
        if self.cookies and os.path.exists(self.cookies):
            options["cookiefile"] = self.cookies

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(target, download=False)

        if data is None:
            raise RuntimeError("No result found.")
        if "entries" in data:
            entries = [entry for entry in data["entries"] if entry]
            if not entries:
                raise RuntimeError("No playable entries found.")
            data = entries[0]

        stream_url = data.get("url")
        if not stream_url:
            raise RuntimeError("yt-dlp did not return an audio stream URL.")

        return Track(
            title=data.get("title") or "Untitled",
            webpage_url=data.get("webpage_url") or query,
            stream_url=stream_url,
            duration=data.get("duration"),
            requester=requester,
        )

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        return value.startswith("http://") or value.startswith("https://")

