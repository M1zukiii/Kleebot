# Kleebot

Self-hosted Discord music bot for YouTube, Bilibili, NicoNico, and Spotify track links. It uses `discord.py`, `yt-dlp`, and `ffmpeg`, and is designed to run with Docker Compose.

## Features

- Slash commands for music playback, queue control, volume, and daily fortune drawing.
- Prefix fallback commands for the basics: `!play`, `!skip`, `!stop`, `!queue`, `!leave`, `!求签`, and `!help`.
- Per-server playback queue.
- Daily fortune drawing with `/求签`, `/fortune`, or `!求签`.
- Fortune commands are limited to once per user until the daily UTC/GMT 09:00 reset.
- Bad fortune has a 10% to 39.6% chance to redraw, with the first result crossed out.
- Fortune tiers are based on luck compared with the average, with `98.5%+` as an extra rare top tier.
- URL playback and YouTube search through `yt-dlp`.
- Spotify track links are resolved to song metadata, then played from a YouTube search result.
- Mention replies can use OpenAI GPT Luna with a Klee persona, including image attachments and lightweight YouTube/Bilibili/NicoNico video metadata.
- Optional cookies file for sites that require login.

## Setup

1. Copy `.env.example` to `.env`.
2. Put your Discord bot token in `DISCORD_TOKEN`.
3. Optional: set `GUILD_ID` to your Discord server ID for faster slash-command syncing while testing.
4. Optional: set `OPENAI_API_KEY` and `OPENAI_MODEL=gpt-5.6-luna` to enable Klee-style AI replies when the bot is mentioned. Edit `klee_prompt.txt` to customize the persona. Use `KLEE_CONTEXT_MESSAGES` and `KLEE_CONTEXT_CHARS` to limit recent @Klee context.
5. Start the bot:

```powershell
docker compose up -d --build
```

Follow logs:

```powershell
docker compose logs -f kleebot
```

## Commands

- `/play query` - 播放 YouTube / B站 / NicoNico / Spotify 链接，或直接搜索歌曲。
- `/播放 query` - `/play` 的中文版本。
- `/join` / `/加入` - 让 Kleebot 加入你所在的语音频道。
- `/queue` - 查看当前播放队列。
- `/队列` - `/queue` 的中文版本。
- `/nowplaying` / `/正在播放` - 查看当前歌曲、队列数量、音量、滤镜和循环状态。
- `/shuffle` / `/打乱` - 打乱当前等待队列。
- `/repeat mode` / `/循环 mode` - 设置循环模式：`off`, `one`, `queue`。
- `/skip` - 跳过当前正在播放的歌曲。
- `/跳过` - `/skip` 的中文版本。
- `/pause` - 暂停当前播放。
- `/暂停` - `/pause` 的中文版本。
- `/resume` - 继续播放暂停中的歌曲。
- `/继续` - `/resume` 的中文版本。
- `/stop` - 停止播放，并清空当前队列。
- `/停止` - `/stop` 的中文版本。
- `/leave` - 让 Kleebot 离开语音频道。
- `/离开` - `/leave` 的中文版本。
- `/volume percent` - 设置音量，范围 1 到 100。
- `/音量 percent` - `/volume` 的中文版本。
- `/filter mode` / `/滤镜 mode` - 设置音频滤镜：`off`, `bassboost`, `nightcore`, `vaporwave`, `karaoke`。
- `/profile` / `/档案` - 查看你的点歌次数、求签次数和历史最好签。
- `/nickname name` / `/称呼 name` - 设置或清除 Klee 对你的称呼。
- `/afk reason` / `/离开状态 reason` - 设置 AFK 状态，别人 @ 你时 Kleebot 会提醒。
- `/leaderboard category` / `/排行榜 category` - 查看排行榜：`plays`, `fortunes`, `luck`, `bombed`。
- `/bomb target` / `/炸弹 target` - 消耗 1 个炸弹，让 Klee 炸一个目标。
- `/help` - 查看 Kleebot 的指令说明。
- `/帮助` - `/help` 的中文版本。
- `/fortune` - 抽取今日幸运签，并领取 3 个炸弹。
- `/求签` - 抽取今日幸运签，并领取 3 个炸弹。

Prefix examples:

```text
!play https://www.youtube.com/watch?v=...
!play https://open.spotify.com/track/...
!play sm9
!play never gonna give you up
!求签
!queue
!skip
```

## Cookies

If a source needs account cookies, export them as Netscape-format cookies and save them to `data/cookies.txt`. The Compose file mounts `./data` to `/app/data`.

NicoNico often needs login cookies. The bot accepts full NicoNico URLs and short IDs such as `sm9`, `so12345`, `nm12345`, and `lv12345`. NicoNico tracks are downloaded to `data/cache` before playback to avoid CDN 403 errors, then removed after playback.
