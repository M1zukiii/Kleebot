import asyncio
import html as html_lib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

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


@dataclass
class SpotifyTrackMeta:
    title: str
    artists: list[str]
    duration: int | None = None

    def search_query(self) -> str:
        artist_text = " ".join(self.artists[:3])
        return f"{self.title} {artist_text} official audio".strip()


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
        if self._is_spotify_track_url(original_query.strip()):
            return self._resolve_spotify_track_sync(original_query.strip(), requester)
        query = self._normalize_query(query)
        target = query if self._looks_like_url(query) else f"ytsearch1:{query}"
        is_niconico = self._is_niconico_query(query)
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "ignoreerrors": True,
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
        for spotify_track in tracks:
            if len(resolved) >= limit:
                break
            try:
                resolved.append(self._resolve_spotify_search_sync(spotify_track, requester))
            except Exception as exc:
                print(f"spotify playlist entry skipped: {spotify_track.search_query()}: {exc}", flush=True)
        if not resolved:
            raise RuntimeError("No playable Spotify entries found.")
        return resolved

    def _spotify_collection_tracks(self, kind: str, spotify_id: str, limit: int) -> list[SpotifyTrackMeta]:
        token = self._spotify_access_token()
        if kind == "album":
            next_url = f"https://api.spotify.com/v1/albums/{spotify_id}/tracks?limit={min(limit, 50)}"
        else:
            next_url = f"https://api.spotify.com/v1/playlists/{spotify_id}/tracks?limit={min(limit, 50)}"

        tracks: list[SpotifyTrackMeta] = []
        while next_url and len(tracks) < limit:
            payload = self._spotify_get(next_url, token)
            for item in payload.get("items", []):
                track = item.get("track") if kind == "playlist" else item
                if not isinstance(track, dict) or track.get("is_local"):
                    continue
                title = track.get("name")
                artists = [artist.get("name") for artist in track.get("artists", []) if artist.get("name")]
                if title and artists:
                    duration = track.get("duration_ms")
                    tracks.append(
                        SpotifyTrackMeta(
                            title=str(title),
                            artists=[str(artist) for artist in artists],
                            duration=int(duration / 1000) if isinstance(duration, int) else None,
                        )
                    )
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
        try:
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            if "premium subscription required" in body.lower():
                raise RuntimeError(
                    "Spotify album/playlist playback needs Premium on the Spotify account that owns the developer app. "
                    "After upgrading, Spotify says it can take a few hours before requests work."
                ) from exc
            raise RuntimeError(f"Spotify API request failed: HTTP {exc.code} {body[:240]}") from exc
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

    def _resolve_spotify_track_sync(self, url: str, requester: str) -> Track:
        meta = self._spotify_track_meta(url)
        return self._resolve_spotify_search_sync(meta, requester)

    def _resolve_spotify_search_sync(self, meta: SpotifyTrackMeta, requester: str) -> Track:
        search_query = meta.search_query()
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "auto",
            "source_address": "0.0.0.0",
        }
        if self.cookies and os.path.exists(self.cookies):
            options["cookiefile"] = self.cookies

        with yt_dlp.YoutubeDL(options) as ydl:
            data = ydl.extract_info(f"ytsearch8:{search_query}", download=False)

        entries = [entry for entry in (data or {}).get("entries", []) if entry]
        if not entries:
            return self._resolve_sync(search_query, requester)

        best = max(entries, key=lambda entry: self._spotify_candidate_score(meta, entry))
        best_url = best.get("webpage_url") or best.get("url")
        if not best_url:
            return self._resolve_sync(search_query, requester)

        track = self._resolve_sync(str(best_url), requester)
        track.title = f"Spotify match: {track.title}"
        return track

    def _spotify_track_meta(self, query: str) -> SpotifyTrackMeta:
        if self.spotify_client_id and self.spotify_client_secret:
            parsed = urlparse(query)
            match = re.match(r"^/track/([^/?#]+)", parsed.path)
            if match:
                try:
                    token = self._spotify_access_token()
                    payload = self._spotify_get(f"https://api.spotify.com/v1/tracks/{match.group(1)}", token)
                    title = payload.get("name")
                    artists = [artist.get("name") for artist in payload.get("artists", []) if artist.get("name")]
                    duration = payload.get("duration_ms")
                    if title and artists:
                        return SpotifyTrackMeta(
                            title=str(title),
                            artists=[str(artist) for artist in artists],
                            duration=int(duration / 1000) if isinstance(duration, int) else None,
                        )
                except Exception as exc:
                    print(f"spotify api track metadata failed: {exc}", flush=True)

        page_query = self._spotify_track_page_query(query)
        if page_query:
            return self._spotify_query_meta(page_query)

        oembed_url = f"https://open.spotify.com/oembed?url={quote(query, safe='')}"
        request = Request(oembed_url, headers={"User-Agent": "Kleebot/1.0"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        title = payload.get("title")
        if not title:
            raise RuntimeError("Could not read Spotify track metadata.")

        return self._spotify_query_meta(f"{self._clean_spotify_title(str(title))} audio")

    def _spotify_query_meta(self, query: str) -> SpotifyTrackMeta:
        cleaned = re.sub(r"\bofficial audio\b|\baudio\b", "", query, flags=re.IGNORECASE).strip()
        parts = cleaned.split()
        if len(parts) <= 1:
            return SpotifyTrackMeta(title=cleaned or query, artists=[])
        return SpotifyTrackMeta(title=cleaned, artists=[])

    def _spotify_candidate_score(self, meta: SpotifyTrackMeta, entry: dict[str, Any]) -> float:
        haystack = self._normalize_score_text(
            " ".join(
                str(value)
                for value in [
                    entry.get("title"),
                    entry.get("uploader"),
                    entry.get("channel"),
                    entry.get("description"),
                ]
                if value
            )
        )
        title_tokens = self._score_tokens(meta.title)
        artist_tokens = {token for artist in meta.artists for token in self._score_tokens(artist)}

        score = 0.0
        if title_tokens:
            score += 60.0 * sum(1 for token in title_tokens if token in haystack) / len(title_tokens)
        if artist_tokens:
            score += 30.0 * sum(1 for token in artist_tokens if token in haystack) / len(artist_tokens)

        title = str(entry.get("title") or "").lower()
        if "official" in title:
            score += 6.0
        if "audio" in title or "lyrics" in title or "mv" in title or "video" in title:
            score += 3.0
        if any(bad in title for bad in ["cover", "karaoke", "nightcore", "sped up", "slowed", "remix"]):
            score -= 18.0

        duration = entry.get("duration")
        if meta.duration and isinstance(duration, (int, float)):
            delta = abs(float(duration) - float(meta.duration))
            if delta <= 2:
                score += 25.0
            elif delta <= 5:
                score += 18.0
            elif delta <= 10:
                score += 10.0
            elif delta >= 30:
                score -= min(24.0, delta / 3.0)
        return score

    @staticmethod
    def _score_tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[\w\u3040-\u30ff\u3400-\u9fff]+", value.lower())
            if len(token) > 1
        }

    @staticmethod
    def _normalize_score_text(value: str) -> str:
        return " ".join(Resolver._score_tokens(value))

    def _spotify_track_query(self, query: str) -> str:
        if not self._is_spotify_track_url(query):
            return query

        page_query = self._spotify_track_page_query(query)
        if page_query:
            return page_query

        oembed_url = f"https://open.spotify.com/oembed?url={quote(query, safe='')}"
        request = Request(oembed_url, headers={"User-Agent": "Kleebot/1.0"})
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        title = payload.get("title")
        if not title:
            raise RuntimeError("Could not read Spotify track metadata.")

        return f"{self._clean_spotify_title(title)} audio"

    def _spotify_track_page_query(self, query: str) -> str | None:
        request = Request(query, headers={"User-Agent": "Mozilla/5.0 Kleebot/1.0"})
        try:
            with urlopen(request, timeout=12) as response:
                page = response.read().decode("utf-8", errors="replace")
        except OSError:
            return None

        title = self._meta_content(page, "og:title")
        description = self._meta_content(page, "og:description")
        if not title:
            return None

        artist = None
        if description:
            artist = description.split("·", 1)[0].strip()
        pieces = [title]
        if artist and artist.lower() not in title.lower():
            pieces.append(artist)
        pieces.append("audio")
        return " ".join(pieces)

    @staticmethod
    def _meta_content(page: str, property_name: str) -> str | None:
        pattern = rf'<meta\s+property="{re.escape(property_name)}"\s+content="([^"]*)"'
        match = re.search(pattern, page)
        if not match:
            return None
        return html_lib.unescape(match.group(1)).strip()

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
