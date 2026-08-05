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


@dataclass
class VideoMetadata:
    title: str
    webpage_url: str
    uploader: str | None
    duration: int | None
    description: str | None
    source: str | None

    def prompt_text(self) -> str:
        parts = [
            f"标题：{self.title}",
            f"链接：{self.webpage_url}",
        ]
        if self.uploader:
            parts.append(f"作者/频道：{self.uploader}")
        if self.duration:
            minutes, seconds = divmod(int(self.duration), 60)
            parts.append(f"时长：{minutes}:{seconds:02d}")
        if self.source:
            parts.append(f"来源：{self.source}")
        if self.description:
            parts.append(f"简介：{self.description[:600]}")
        return "\n".join(parts)


class Resolver:
    def __init__(self) -> None:
        self.cookies = os.getenv("YTDLP_COOKIES", "/app/data/cookies.txt")
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self._spotify_token: str | None = None

    async def resolve(self, query: str, requester: str) -> Track:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._resolve_sync, query, requester)

    async def resolve_playlist(self, query: str, requester: str, limit: int = 50) -> list[Track]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._resolve_playlist_sync, query, requester, limit)

    async def video_metadata(self, url: str) -> VideoMetadata:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._video_metadata_sync, url)

    def _video_metadata_sync(self, url: str) -> VideoMetadata:
        target = self._normalize_query(url)
        options: dict[str, Any] = {
            "skip_download": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "socket_timeout": 12,
        }
        if self.cookies and os.path.exists(self.cookies):
            options["cookiefile"] = self.cookies

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(target, download=False)

        if data is None:
            raise RuntimeError("No video metadata found.")
        if "entries" in data:
            entries = [entry for entry in data["entries"] if entry]
            if not entries:
                raise RuntimeError("No video metadata entries found.")
            data = entries[0]

        return VideoMetadata(
            title=data.get("title") or "Untitled",
            webpage_url=data.get("webpage_url") or target,
            uploader=data.get("uploader") or data.get("channel"),
            duration=data.get("duration"),
            description=self._clean_description(data.get("description")),
            source=data.get("extractor_key") or data.get("extractor"),
        )

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

    def _resolve_playlist_sync(self, query: str, requester: str, limit: int) -> list[Track]:
        original_query = query.strip()
        if self._is_spotify_album_or_playlist_url(original_query):
            return self._resolve_spotify_collection_sync(original_query, requester, limit)

        target = self._normalize_query(original_query)
        if not self._looks_like_url(target):
            raise RuntimeError("Playlist playback needs a playlist, album, or collection URL.")

        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
        }
        if self.cookies and os.path.exists(self.cookies):
            options["cookiefile"] = self.cookies

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(target, download=False)

        if data is None:
            raise RuntimeError("No playlist entries found.")

        entries = data.get("entries") if isinstance(data, dict) else None
        if not entries:
            return [self._resolve_sync(original_query, requester)]

        tracks: list[Track] = []
        for entry in entries:
            if not entry or len(tracks) >= limit:
                continue
            entry_url = entry.get("webpage_url") or entry.get("url")
            if not entry_url:
                continue
            if not self._looks_like_url(str(entry_url)):
                ie_key = str(entry.get("ie_key") or data.get("extractor_key") or "").lower()
                if "youtube" in ie_key:
                    entry_url = f"https://www.youtube.com/watch?v={entry_url}"
                elif "bilibili" in ie_key:
                    entry_url = f"https://www.bilibili.com/video/{entry_url}"
                elif "nico" in ie_key:
                    entry_url = f"https://www.nicovideo.jp/watch/{entry_url}"
            try:
                tracks.append(self._resolve_sync(str(entry_url), requester))
            except Exception as exc:
                print(f"playlist entry skipped: {entry_url}: {exc}", flush=True)

        if not tracks:
            raise RuntimeError("No playable playlist entries found.")
        return tracks

    def _resolve_spotify_collection_sync(self, url: str, requester: str, limit: int) -> list[Track]:
        if not self.spotify_client_id or not self.spotify_client_secret:
            raise RuntimeError(
                "Spotify album/playlist playback needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET. "
                "Spotify track links still work."
            )

        parsed = urlparse(url)
        match = re.match(r"^/(album|playlist)/([^/?#]+)", parsed.path)
        if not match:
            raise RuntimeError("Unsupported Spotify URL.")
        kind, spotify_id = match.groups()
        tracks = self._spotify_collection_tracks(kind, spotify_id, limit)
        resolved: list[Track] = []
        for title, artists in tracks:
            if len(resolved) >= limit:
                break
            query = f"{title} {' '.join(artists)} audio"
            try:
                resolved.append(self._resolve_sync(query, requester))
            except Exception as exc:
                print(f"spotify playlist entry skipped: {query}: {exc}", flush=True)
        if not resolved:
            raise RuntimeError("No playable Spotify entries found.")
        return resolved

    def _spotify_collection_tracks(self, kind: str, spotify_id: str, limit: int) -> list[tuple[str, list[str]]]:
        token = self._spotify_access_token()
        if kind == "album":
            next_url = f"https://api.spotify.com/v1/albums/{spotify_id}/tracks?limit={min(limit, 50)}"
        else:
            next_url = f"https://api.spotify.com/v1/playlists/{spotify_id}/tracks?limit={min(limit, 50)}"

        tracks: list[tuple[str, list[str]]] = []
        while next_url and len(tracks) < limit:
            payload = self._spotify_get(next_url, token)
            for item in payload.get("items", []):
                track = item.get("track") if kind == "playlist" else item
                if not isinstance(track, dict) or track.get("is_local"):
                    continue
                title = track.get("name")
                artists = [artist.get("name") for artist in track.get("artists", []) if artist.get("name")]
                if title and artists:
                    tracks.append((str(title), [str(artist) for artist in artists]))
                if len(tracks) >= limit:
                    break
            next_url = payload.get("next")
        return tracks

    def _spotify_access_token(self) -> str:
        if self._spotify_token:
            return self._spotify_token
        import base64

        credentials = f"{self.spotify_client_id}:{self.spotify_client_secret}".encode("utf-8")
        request = Request(
            "https://accounts.spotify.com/api/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Could not get Spotify access token.")
        self._spotify_token = str(token)
        return self._spotify_token

    @staticmethod
    def _spotify_get(url: str, token: str) -> dict[str, Any]:
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Spotify returned an unexpected response.")
        return payload

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
    def _is_spotify_album_or_playlist_url(value: str) -> bool:
        if not Resolver._looks_like_url(value):
            return False
        parsed = urlparse(value)
        return parsed.netloc.endswith("spotify.com") and re.match(r"^/(album|playlist)/[^/]+", parsed.path) is not None

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
    def _clean_description(description: Any) -> str | None:
        if not description:
            return None
        return re.sub(r"\s+", " ", str(description)).strip()

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        return value.startswith("http://") or value.startswith("https://")
