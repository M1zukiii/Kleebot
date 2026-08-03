# MusicBot

Self-hosted Discord music bot for YouTube, Bilibili, and NicoNico. It uses `discord.py`, `yt-dlp`, and `ffmpeg`, and is designed to run with Docker Compose.

## Features

- Slash commands for play, queue, pause, resume, skip, stop, leave, and volume.
- Prefix fallback commands for the basics: `!play`, `!skip`, `!stop`, `!queue`, `!leave`, and `!help`.
- Per-server playback queue.
- URL playback and YouTube search through `yt-dlp`.
- Optional cookies file for sites that require login.

## Setup

1. Copy `.env.example` to `.env`.
2. Put your Discord bot token in `DISCORD_TOKEN`.
3. Optional: set `GUILD_ID` to your Discord server ID for faster slash-command syncing while testing.
4. Start the bot:

```powershell
docker compose up -d --build
```

## Commands

- `/play query`
- `/queue`
- `/skip`
- `/pause`
- `/resume`
- `/stop`
- `/leave`
- `/volume percent`

Prefix examples:

```text
!play https://www.youtube.com/watch?v=...
!play never gonna give you up
!queue
!skip
```

## Cookies

If a source needs account cookies, export them as Netscape-format cookies and save them to `data/cookies.txt`. The Compose file mounts `./data` to `/app/data`.

