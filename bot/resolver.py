import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import yt_dlp
from yt_dlp.utils import DownloadError


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    duration: int | None
    requester: str
    http_headers: dict[str, str]
    cookies: str | None = None
    local_path: str | None = None


class Resolver:
    def __init__(self) -> None:
        self.cookies = os.getenv("YTDLP_COOKIES", "/app/data/cookies.txt")

    async def resolve(self, query: str, requester: str) -> Track:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._resolve_sync, query, requester)

    def _resolve_sync(self, query: str, requester: str) -> Track:
        original_query = query
        query = self._normalize_query(query)
        target = query if self._looks_like_url(query) else f"ytsearch1:{query}"
        is_niconico = self._is_niconico_query(query)
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

        try:
            if is_niconico:
                return self._resolve_niconico_sync(target, original_query, requester, options)
            with yt_dlp.YoutubeDL(options) as ydl:
                data = ydl.extract_info(target, download=False)
        except DownloadError as exc:
            if is_niconico:
                raise RuntimeError(
                    "NicoNico could not be resolved. Some Nico videos require login cookies. "
                    "Export your NicoNico cookies to data/cookies.txt and try again."
                ) from exc
            raise

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
            webpage_url=data.get("webpage_url") or original_query,
            stream_url=stream_url,
            duration=data.get("duration"),
            requester=requester,
            http_headers=data.get("http_headers") or {},
        )

    def _resolve_niconico_sync(
        self,
        target: str,
        original_query: str,
        requester: str,
        options: dict[str, Any],
    ) -> Track:
        cache_dir = Path("/app/data/cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        download_options = {
            **options,
            "outtmpl": str(cache_dir / f"niconico-{uuid.uuid4().hex}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }

        with yt_dlp.YoutubeDL(download_options) as ydl:
            data = ydl.extract_info(target, download=True)

        if data is None:
            raise RuntimeError("No result found.")
        if "entries" in data:
            entries = [entry for entry in data["entries"] if entry]
            if not entries:
                raise RuntimeError("No playable entries found.")
            data = entries[0]

        local_path = self._downloaded_filepath(data)
        if not local_path or not os.path.exists(local_path):
            raise RuntimeError("NicoNico downloaded file was not found.")

        return Track(
            title=data.get("title") or "Untitled",
            webpage_url=data.get("webpage_url") or original_query,
            stream_url=local_path,
            duration=data.get("duration"),
            requester=requester,
            http_headers={},
            local_path=local_path,
        )

    @staticmethod
    def _downloaded_filepath(data: dict[str, Any]) -> str | None:
        requested_downloads = data.get("requested_downloads")
        if isinstance(requested_downloads, list) and requested_downloads:
            filepath = requested_downloads[0].get("filepath")
            if filepath:
                return str(filepath)
        filepath = data.get("filepath")
        if filepath:
            return str(filepath)
        return None

    def _spotify_track_query(self, query: str) -> str:
        if not self._is_spotify_track_url(query):
            return query

        oembed_url = f"https://open.spotify.com/oembed?url={quote(query, safe='')}"
        request = Request(oembed_url, headers={"User-Agent": "Kleebot/1.0"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        title = payload.get("title")
        if not title:
            raise RuntimeError("Could not read Spotify track metadata.")

        return f"{self._clean_spotify_title(title)} audio"

    def _normalize_query(self, query: str) -> str:
        query = query.strip()
        query = self._spotify_track_query(query)
        if self._looks_like_niconico_id(query):
            return f"https://www.nicovideo.jp/watch/{query.lower()}"
        return query

    def _cookie_header(self) -> str | None:
        if not self.cookies or not os.path.exists(self.cookies):
            return None

        cookies: list[str] = []
        with open(self.cookies, encoding="utf-8") as cookie_file:
            for line in cookie_file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    domain, _, path, secure, expires, name, value = parts[:7]
                    if "nicovideo.jp" in domain or "nico.ms" in domain:
                        cookies.append(f"{name}={value}")
        return "; ".join(cookies) or None

    @staticmethod
    def _is_spotify_track_url(value: str) -> bool:
        if not Resolver._looks_like_url(value):
            return False
        parsed = urlparse(value)
        return parsed.netloc.endswith("spotify.com") and re.match(r"^/track/[^/]+", parsed.path) is not None

    @staticmethod
    def _is_niconico_query(value: str) -> bool:
        if Resolver._looks_like_niconico_id(value):
            return True
        if not Resolver._looks_like_url(value):
            return False
        parsed = urlparse(value)
        return parsed.netloc.endswith("nicovideo.jp") or parsed.netloc.endswith("nico.ms")

    @staticmethod
    def _looks_like_niconico_id(value: str) -> bool:
        return re.fullmatch(r"(sm|so|nm|lv)\d+", value.strip(), flags=re.IGNORECASE) is not None

    @staticmethod
    def _clean_spotify_title(title: str) -> str:
        return re.sub(r"\s+", " ", title.replace(" - song and lyrics by ", " by ")).strip()

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        return value.startswith("http://") or value.startswith("https://")
