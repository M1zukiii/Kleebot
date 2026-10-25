# Kleebot

Self-hosted Discord music bot for YouTube, Bilibili, NicoNico, and Spotify track links. It uses `discord.py`, `yt-dlp`, and `ffmpeg`, and is designed to run with Docker Compose.

## Features

- Slash commands for music playback, queue control, volume, and daily fortune drawing.
- Prefix fallback commands for the basics: `!play`, `!skip`, `!stop`, `!queue`, `!leave`, `!求签`, and `!help`.
- Per-server playback queue.
- Playlist and album queueing with `/playlist`, `/playalbum`, and `/专辑`.
- Daily fortune drawing with `/求签`, `/fortune`, or `!求签`.
- Fortune commands are limited to once per user until the daily UTC/GMT 09:00 reset.
- Bad fortune has a 10% to 39.6% chance to redraw, with the first result crossed out.
- Fortune tiers are based on luck compared with the average, with `98.5%+` as an extra rare top tier.
- URL playback and YouTube search through `yt-dlp`.
- YouTube/Bilibili audio is downloaded to a local cache before playback by default, which avoids FFmpeg 403 errors from expiring stream URLs. Set `YTDLP_DOWNLOAD_STREAMS=false` to stream directly.
- Spotify track links are resolved to song metadata, then played from a YouTube search result.
- Mention replies can use OpenAI GPT Luna with a Klee persona, including image attachments and lightweight YouTube/Bilibili/NicoNico video metadata.
- Image generation with `/imagine`, `/生图`, `!imagine`, or `!生图`; choose OpenAI or NovelAI V5 Full with `IMAGE_PROVIDER`.
- NovelAI image prompts support two modes: plain-language descriptions or raw prompt/tags.
- NovelAI defaults to `nai-diffusion-5-full` with `NOVELAI_AVOID_ANLAS=true`, and supports square, portrait, and landscape normal-size presets.
- Daily AI cost limits: 3 image-recognition mentions. Image generation is unlimited when `KLEE_IMAGE_DAILY_LIMIT=0`.
- Optional cookies file for sites that require login.

## Windows local chat test

Install Python 3.12+, then double-click `start-local.cmd`. The local wizard creates
a separate virtual environment, asks for your Discord token and optional chat key,
and stores local test configuration in `.env.local` and data in `data/local-test/`.
Docker and FFmpeg are not required for chat. See [LOCAL_TEST.md](LOCAL_TEST.md) for
the Chinese walkthrough and test-server setup.

## Setup

1. Copy `.env.example` to `.env`.
2. Put your Discord bot token in `DISCORD_TOKEN`.
3. Optional: set `GUILD_ID` to your Discord server ID for faster slash-command syncing while testing.
4. Optional: set `OPENAI_API_KEY` and `OPENAI_MODEL=gpt-5.6-luna` to enable Klee-style AI replies when the bot is mentioned. Edit `klee_prompt.txt` to customize the persona. Use `KLEE_CONTEXT_MESSAGES` and `KLEE_CONTEXT_CHARS` to limit recent @Klee context.
5. Spotify track/album/playlist links use public Spotify page metadata by default. Optional: set `SPOTIFY_USE_API=true`, `SPOTIFY_CLIENT_ID`, and `SPOTIFY_CLIENT_SECRET` only if you want Spotify API fallback.
6. Optional: set `IMAGE_PROVIDER=novelai` and `NOVELAI_TOKEN` to use NovelAI image generation. The default NovelAI model is `nai-diffusion-5-full`; keep `NOVELAI_AVOID_ANLAS=true` to stay within the normal-size, 28-step settings that Opus can use without Anlas when V5 usage allowance is available.
7. Optional: keep `YTDLP_DOWNLOAD_STREAMS=true` for more reliable YouTube/Bilibili playback. Cached files are removed after playback.
8. Start the bot:

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
- `/playlist query` / `/playalbum query` / `/专辑 query` - 播放 YouTube、B站、NicoNico 列表，或 Spotify 专辑/歌单。
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
- `/imagine prompt mode aspect negative custom_negative` / `/生图 prompt mode aspect negative custom_negative` - 根据描述生成图片。`aspect` 可选方图、竖图、横图；`mode` 选“大白话”会自动整理，选“提示词/标签”会原样发送；`negative` 可选默认、关闭或自定义。
- `/promptgen prompt aspect` / `/提示词 prompt aspect` - 只把大白话整理成 NovelAI 提示词，不生图。
- `/naiusage` / `/额度` - 查看 NovelAI 模型、Anlas 和省 Anlas 设置。
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
!生图 一个在璃月港看烟花的 Klee，开心地抱着嘟嘟可
!生图 --竖图 一个在璃月港看烟花的 Klee，开心地抱着嘟嘟可
!生图 --横图 --tags 1girl, blonde hair, red coat, fireworks, liyue harbor, anime illustration
!生图 --no-negative 一个在璃月港看烟花的 Klee，开心地抱着嘟嘟可
!生图 --negative "lowres, bad anatomy" 一个在璃月港看烟花的 Klee，开心地抱着嘟嘟可
!提示词 --竖图 一个在璃月港看烟花的 Klee，开心地抱着嘟嘟可
!求签
!queue
!skip
```

## Cookies

If a source needs account cookies, export them as Netscape-format cookies and save them to `data/cookies.txt`. The Compose file mounts `./data` to `/app/data`.

NicoNico often needs login cookies. The bot accepts full NicoNico URLs and short IDs such as `sm9`, `so12345`, `nm12345`, and `lv12345`. NicoNico tracks are downloaded to `data/cache` before playback to avoid CDN 403 errors, then removed after playback.


## Chat architecture and provider selection

Discord message handling stays in `bot/main.py`. `bot/conversation.py` owns recent
conversation history and serializes turns within each channel. `bot/klee_ai.py`
builds Klee's persona prompt, and `bot/chat_model.py` owns model configuration and
provider-specific requests. Music, fortune, and image generation keep their
existing modules.

Recent @Klee conversations are now stored in `data/conversations.sqlite3` and
survive restarts with the existing Compose data mount. Only addressed messages
and successful replies are stored, bounded by `KLEE_CONTEXT_MESSAGES` per channel;
`KLEE_CONTEXT_CHARS` bounds what is sent to the model. Group-channel context is
shared within that channel; different channels and DMs are isolated. Author IDs
are included to distinguish users with identical names. This is persisted recent
context, not long-term fact learning or model training. Ordinary channel messages
are not automatically collected. Set `KLEE_CONTEXT_MESSAGES=0` to stop storing new
turns (use the clear command to remove existing stored turns).

Use `!forgetchat` / `!清除聊天` to delete stored context for the current channel.
In servers this requires Manage Messages; in DMs users can clear their own context.
Use your configured command prefix instead of `!` if different.

Existing `.env` files keep using OpenAI without changes. To select DeepSeek:

```dotenv
CHAT_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-deepseek-api-key
CHAT_MODEL=deepseek-flash
```

DeepSeek uses Chat Completions with thinking disabled for short chat replies;
OpenAI retains Responses API. `CHAT_API_KEY`, `CHAT_BASE_URL`, `CHAT_MODEL`, and
`CHAT_TIMEOUT` optionally override the selected chat provider's settings. A missing
DeepSeek key does not silently send chats to OpenAI. Select a vision-capable model
if image mentions are needed (`deepseek-flash` currently supports image input).
NovelAI plain-language prompt rewriting follows the selected chat provider;
`NOVELAI_PROMPT_MODEL` can override its model. OpenAI image generation continues
to use only the existing `OPENAI_*` credentials and endpoint, while NovelAI keeps
its own token. Switching chat providers does not switch image providers.

After editing `.env`, rebuild with `docker compose up -d --build`. Check the
startup log for the chat provider/model and whether its key is configured.

Offline checks: `python -m unittest discover -s tests -v` and
`python -m compileall -q bot tests`. Live Discord/API checks need your own tokens.

## Owner-driven development and updates

`!develop <task>` / `!开发 <需求>` creates a model-generated draft PR via GitHub
Actions. Generated code is checked in a separate job; review and merge the PR to
make it eligible for deployment. `!update` / `!更新` requests a host update check,
and `!updatestatus` / `!更新状态` shows the result. These commands are disabled
until explicit Discord IDs are set in `KLEE_OWNER_IDS`.

A separately installed Linux host timer builds/tests new `main` revisions, backs
up data, deploys versioned images, and attempts image rollback when Discord
readiness fails. The bot itself has no shell or Docker access. A one-time server
and GitHub setup is required; see **DEPLOY.md → Owner development and automatic
deployment**. These features do not run until that setup is completed.

## Identity-bound fact memory

Klee resolves real Discord mentions to account IDs and keeps factual preferences
as separate (guild scope, user ID, key) rows; DMs remain isolated. Food ranks use food.favorite.1,
food.favorite.2, etc. Recent chat and facts are separate; facts survive beyond the
recent-message limit. Other users can query a public preference in the same
server, but only the subject can change/delete it. There is no automatic admin
override for conversational edits. Nicknames must match an unambiguous known
Discord alias; use a real @mention when uncertain.

Complete food disclosures and queries use a deterministic local parser, including
multiple ranks in one message. Other natural-language interpretation uses one bounded chat-model call on
likely preference/memory requests. The model proposes operations; Python resolves
identities, checks permissions and validates exact values before committing them.
Invalid/ambiguous plans do not change data. Preference-query answers come directly
from stored facts. This initial version targets explicit preferences, especially
ranked foods; it is not general autonomous learning or a complete semantic parser.

Deleting a fact removes its value (only a key tombstone may remain). To prevent
old dialogue from reintroducing it, deletion also clears the server's bounded
recent conversation cache. Other users' independent facts and the subject's
other fact keys stay intact. Full deletion removes all the subject's fact rows.
The privileged /清除聊天 command clears channel history and file cache; shared
personal facts require a request from their subject. Old mapped channel facts
are migrated within the same guild. Conflicting values require the subject to
confirm them again. Different servers and DMs never share personal memory.

## Diagnostics and maintenance readiness

Failures include a short diagnostic reference. /诊断 lets server managers inspect
scoped recent errors privately; credentials are redacted, records expire after
30 days and are capped at 500. /核对操作 uses uniquely correlated bot audit-log
entries to confirm uncertain management or timeout results without replaying
Discord operations. It requires native audit-log permission. Music has no such
audit receipt and is never guessed as successful. Automatic checks can resume
only after outstanding uncertain penalties have been reconciled.

The owner-only /自维护检查 reports local updater and dispatch configuration.
Code repair requires KLEE_GITHUB_ACTIONS_TOKEN locally (repository Actions write)
and the GitHub Actions secret KLEE_DEVELOPER_API_KEY. Set repository variable
KLEE_DEVELOPER_PROVIDER to openai (default) or deepseek, and optionally set
KLEE_DEVELOPER_MODEL. Repair selects bounded relevant tracked source files and
creates a draft PR; validation remains in a separate job without model secrets.
The bot cannot verify remote secret configuration or grant itself credentials.

`/开发` assigns a unique receipt ID to the workflow run and persists its origin
channel. A bounded background poll reports validation success or failure there
and in the configured operational log channel; restarts do not lose pending
requests. A successful check still means a draft PR awaiting human review, not
an installed change. API failures back off and are reported separately from job
failures. Manual workflow runs without a bot receipt ID are not guessed as a
user's request.

Use `/日志频道` to explicitly select a text channel in the current guild, or set
its enabled option to false to disable forwarding. Native manage-server and
channel-access checks apply. The management agent can connect a requested log
channel using its dedicated tool; creating a channel alone is insufficient.
Logs contain startup, normal shutdown, update and development receipts plus
diagnostic references, never raw chat, console output or credentials. A forced
kill or power loss cannot send a final shutdown notice; the next startup reports
that the previous run did not record a normal close. Discord delivery is bounded
and retried; operational records expire after 14 days and are capped at 1,000.
### 音乐按钮与语音自动退出

点歌或加入歌单后自动显示音乐面板，也可使用 `/音乐面板` 打开。暂停/继续、跳过、队列、音量（0–100%）、循环、停止、退出按钮仅限 Klee 所在语音频道的成员操作，不要求管理员。面板在状态变化时刷新，按钮使用持久化注册；重启后需重新点歌恢复队列。

加入和移动语音时默认 `self_deaf=True`，关闭听筒但保留音乐输出。每15秒检查一次：空闲或暂停连续5分钟、频道无人连续1分钟时，清空队列并退出。有人正常听歌时不限制在线时长。停止/退出会使旧播放回调失效，避免重新启动循环或下一首。
`/常驻语音 开启:True` 会加入使用者所在的语音频道并保存常驻设置，跳过空闲和无人自动退出；同频道成员可切换，无需管理员。重启后或连接断开时，每60秒最多尝试一次重新加入已保存的频道，并核对机器人连接权限，保持听筒关闭。`开启:False` 恢复自动退出。`/离开` 和面板“退出语音”会取消常驻，防止再次自动加入；正常程序重启则保留设置。常驻配置保存在数据目录的 `music_stay.json`。
### 回复消息上下文

通过 Discord“回复”并提及 Klee 时，会读取当前频道中被回复消息的原文、作者、有限的嵌入内容和图片附件，帮助理解“这句”“为什么”等指代。回复 Klee 自己的消息即使关闭提及也能触发普通聊天；回复其他成员且没有提及 Klee 不会触发聊天。不会跨频道读取引用；缺少查看/读取历史权限、消息已删除或读取超时会如实说明。引用内容有长度限制，只作资料，不会作为新管理操作授权或当前用户的个人记忆声明。引用中的非图片附件仅列出文件名，不会假装读过正文。
### 人设口吻开关

`/邪恶模式 开启:True` 开启更腹黑、调皮、嘴硬和爱吐槽的聊天风格；`开启:False` 恢复原人设；不填参数查看状态。默认关闭，设置存入数据目录的 `persona.sqlite3`，按服务器隔离并在重启后保留。使用时重新核实服务器所有者、管理员或管理服务器权限。开关只改变聊天表达，不改变真实工具权限、处罚规则、事实准确性或既有创造者设定。

当用户在当条聊天消息中直接叫“妈妈”“妈咪”“麻麻”时，用小小大人式、爱操心和会哄人的照顾口吻回应，可与邪恶模式同时生效。这只是称呼触发的轻松角色语气，不建立真实亲子或监护关系；讨论Klee的妈妈艾莉丝、引用历史消息和引用代码中的称呼不触发这一层。
