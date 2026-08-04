import asyncio
import os
import random
from collections import deque
from dataclasses import dataclass, field

import discord

from .resolver import Resolver, Track


FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"
FFMPEG_FILTERS = {
    "off": "",
    "bassboost": "bass=g=8:f=110:w=0.6",
    "nightcore": "asetrate=48000*1.25,aresample=48000,atempo=1.0",
    "vaporwave": "asetrate=48000*0.8,aresample=48000,atempo=1.0",
    "karaoke": "pan=stereo|c0=c0-c1|c1=c1-c0",
}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass
class GuildPlayer:
    bot: discord.Client
    guild_id: int
    resolver: Resolver
    queue: deque[Track] = field(default_factory=deque)
    current: Track | None = None
    volume: float = 0.35
    audio_filter: str = "off"
    repeat_mode: str = "off"
    skip_requested: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def enqueue(self, interaction: discord.Interaction, query: str) -> Track:
        if interaction.user is None:
            raise RuntimeError("Missing Discord user.")
        track = await self.resolver.resolve(query, interaction.user.display_name)
        self.queue.append(track)
        await self.ensure_playing(interaction)
        return track

    async def ensure_voice(self, interaction: discord.Interaction) -> discord.VoiceClient:
        if interaction.guild is None:
            raise RuntimeError("This command can only be used in a server.")

        user = interaction.user
        voice_state = getattr(user, "voice", None)
        if voice_state is None or voice_state.channel is None:
            raise RuntimeError("Join a voice channel first.")

        voice_client = interaction.guild.voice_client
        if voice_client is None:
            return await voice_state.channel.connect()
        if voice_client.channel != voice_state.channel:
            await voice_client.move_to(voice_state.channel)
        return voice_client

    async def ensure_playing(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            voice_client = await self.ensure_voice(interaction)
            if not voice_client.is_playing() and not voice_client.is_paused():
                await self._play_next(voice_client)

    async def _play_next(self, voice_client: discord.VoiceClient) -> None:
        if not self.queue:
            self.current = None
            return

        self.current = self.queue.popleft()
        played_track = self.current
        before_options = self._ffmpeg_before_options(played_track)
        source = discord.FFmpegPCMAudio(
            played_track.stream_url,
            before_options=before_options,
            options=self._ffmpeg_options(),
        )
        audio = discord.PCMVolumeTransformer(source, volume=self.volume)

        def after(error: Exception | None) -> None:
            if error:
                print(f"playback error: {error}", flush=True)
            should_requeue = False
            if not self.skip_requested:
                if self.repeat_mode == "one":
                    self.queue.appendleft(played_track)
                    should_requeue = True
                elif self.repeat_mode == "queue":
                    self.queue.append(played_track)
                    should_requeue = True
            self.skip_requested = False
            if not should_requeue:
                self._cleanup_track(played_track)
            fut = asyncio.run_coroutine_threadsafe(self._play_next(voice_client), self.bot.loop)
            try:
                fut.result()
            except Exception as exc:
                print(f"failed to play next track: {exc}", flush=True)

        voice_client.play(audio, after=after)

    def _ffmpeg_before_options(self, track: Track) -> str:
        if track.local_path:
            return ""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": track.webpage_url,
            **track.http_headers,
        }
        if track.cookies:
            headers["Cookie"] = track.cookies
        header_lines = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if value)
        return f'{FFMPEG_BEFORE_OPTIONS} -headers "{header_lines}"'

    def _ffmpeg_options(self) -> str:
        filter_graph = FFMPEG_FILTERS.get(self.audio_filter, "")
        if not filter_graph:
            return FFMPEG_OPTIONS
        return f'{FFMPEG_OPTIONS} -af "{filter_graph}"'

    def set_filter(self, name: str) -> str:
        normalized = name.lower().strip()
        if normalized in {"none", "clear", "reset", "关闭", "清除"}:
            normalized = "off"
        if normalized not in FFMPEG_FILTERS:
            choices = ", ".join(FFMPEG_FILTERS)
            raise ValueError(f"Unknown filter. Choose one of: {choices}")
        self.audio_filter = normalized
        return normalized

    @staticmethod
    def _cleanup_track(track: Track) -> None:
        if not track.local_path:
            return
        try:
            os.remove(track.local_path)
            print(f"removed temp audio file: {track.local_path}", flush=True)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"failed to remove temp audio file {track.local_path}: {exc}", flush=True)

    def skip(self, guild: discord.Guild) -> bool:
        voice_client = guild.voice_client
        if voice_client and voice_client.is_playing():
            self.skip_requested = True
            voice_client.stop()
            return True
        return False

    def clear(self) -> int:
        count = len(self.queue)
        self.queue.clear()
        self.current = None
        self.skip_requested = True
        return count

    def shuffle(self) -> int:
        tracks = list(self.queue)
        random.shuffle(tracks)
        self.queue = deque(tracks)
        return len(self.queue)

    def set_repeat(self, mode: str) -> str:
        normalized = mode.lower().strip()
        if normalized in {"none", "clear", "reset", "关闭", "关"}:
            normalized = "off"
        if normalized not in {"off", "one", "queue"}:
            raise ValueError("Unknown repeat mode. Choose one of: off, one, queue")
        self.repeat_mode = normalized
        return normalized

    def now_playing_text(self) -> str:
        lines: list[str] = []
        if self.current:
            duration = self._format_duration(self.current.duration)
            lines.append(f"Now: {self.current.title}")
            lines.append(f"Requested by: {self.current.requester}")
            if duration:
                lines.append(f"Duration: {duration}")
        else:
            lines.append("Nothing is playing.")
        lines.append(f"Queue: {len(self.queue)} track(s)")
        lines.append(f"Volume: {round(self.volume * 100)}%")
        lines.append(f"Filter: {self.audio_filter}")
        lines.append(f"Repeat: {self.repeat_mode}")
        return "\n".join(lines)

    def queue_text(self) -> str:
        lines: list[str] = []
        if self.current:
            lines.append(f"Now: {self.current.title}")
        if self.queue:
            for index, track in enumerate(list(self.queue)[:10], start=1):
                lines.append(f"{index}. {track.title}")
            if len(self.queue) > 10:
                lines.append(f"...and {len(self.queue) - 10} more")
        return "\n".join(lines) or "Queue is empty."

    @staticmethod
    def _format_duration(seconds: int | None) -> str:
        if not seconds:
            return ""
        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"
