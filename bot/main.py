import asyncio
import os
import re
import time
import tempfile
import signal
import secrets
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from .maintenance import is_owner, request_development, request_update, update_status, maintenance_check
from .moderation import Moderation
from .guild_agent import GuildAgent
from .spam_guard import SpamGuard
from .diagnostic_controls import DiagnosticControls
from .diagnostics import explanation
from .operations import Operations
from .music_controls import MusicControls
from .reply_context import read_reply_context, ReplyContext
from .persona_modes import PersonaStore, PersonaControls
from .model_controls import ModelSelection, ModelControls
from .fact_memory import FactMemory
from .conversation import ConversationService, ConversationStore
from .ai_usage import AiUsageStore
from .fortune import (
    FortuneResult,
    draw_daily_fortune,
    draw_second_fortune,
    fortune_message,
    fortune_text,
    luck_color,
    luck_summary,
)
from .fortune_cooldown import FortuneCooldownStore
from .klee_ai import FALLBACK_REPLY, KleeAI
from .player import GuildPlayer
from .math_reply import MathCommands, send_math_reply
from .file_service import FileCommands, FileService, requested_format
from .budget_controls import BudgetControls
from .resolver import Resolver
from .afk import DEFAULT_AFK_REASON, AfkStore
from .stats import ProfileStatsStore


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "!")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
autonomy = None
spam_guard = None
operations = None
resolver = Resolver()
klee_ai = KleeAI()
file_service = FileService(klee_ai.chat_model)
players: dict[int, GuildPlayer] = {}
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
DATA_DIR = Path(os.getenv("KLEE_DATA_DIR") or ("/app/data" if Path("/app").exists() else str(BASE_DIR / "data")))
klee_ai.persona_modes = PersonaStore(DATA_DIR / 'persona.sqlite3')
model_selection = ModelSelection(klee_ai.chat_model, DATA_DIR)
HELP_TEXT_FILE = Path(os.getenv("HELP_TEXT_FILE", str(BASE_DIR / "help.txt")))
FORTUNE_SLIP_IMAGE = ASSETS_DIR / "fortune-slip.webp"
KLEE_FOOTER_IMAGE = ASSETS_DIR / "klee-footer.jpg"
fortune_cooldowns = FortuneCooldownStore(DATA_DIR / "fortune_cooldowns.json")
profile_stats = ProfileStatsStore(DATA_DIR / "profile_stats.json")
afk_store = AfkStore(DATA_DIR / "afk.json")
ai_usage = AiUsageStore(DATA_DIR / "ai_usage.json")
KLEE_REEE_EMOTE_ID = 1534067915089903727
KLEE_MENTION_REPLY = FALLBACK_REPLY
NO_BOMBS_MESSAGE = "荣誉骑士你现在没有炸弹哦~等Klee做好了炸弹分给你吧~"
FILTER_CHOICES = [
    app_commands.Choice(name="off", value="off"),
    app_commands.Choice(name="bassboost", value="bassboost"),
    app_commands.Choice(name="nightcore", value="nightcore"),
    app_commands.Choice(name="vaporwave", value="vaporwave"),
    app_commands.Choice(name="karaoke", value="karaoke"),
]
REPEAT_CHOICES = [
    app_commands.Choice(name="off", value="off"),
    app_commands.Choice(name="one", value="one"),
    app_commands.Choice(name="queue", value="queue"),
]
LEADERBOARD_CHOICES = [
    app_commands.Choice(name="plays", value="plays"),
    app_commands.Choice(name="fortunes", value="fortunes"),
    app_commands.Choice(name="luck", value="luck"),
    app_commands.Choice(name="bombed", value="bombed"),
]
IMAGE_ASPECT_CHOICES = [
    app_commands.Choice(name="Square 1:1", value="square"),
    app_commands.Choice(name="Portrait 2:3", value="portrait"),
    app_commands.Choice(name="Landscape 3:2", value="landscape"),
]
NEGATIVE_PROMPT_CHOICES = [
    app_commands.Choice(name="Default", value="default"),
    app_commands.Choice(name="Off", value="off"),
    app_commands.Choice(name="Custom", value="custom"),
]
conversations = ConversationService(klee_ai, ConversationStore(
    DATA_DIR / "conversations.sqlite3",
    max_messages=int(os.getenv("KLEE_CONTEXT_MESSAGES", "6")),
    max_chars=int(os.getenv("KLEE_CONTEXT_CHARS", "900")),
), memory=FactMemory(DATA_DIR / "conversations.sqlite3", klee_ai.chat_model))
KLEE_VIDEO_METADATA_LIMIT = int(os.getenv("KLEE_VIDEO_METADATA_LIMIT", "2"))
KLEE_VISION_DAILY_LIMIT = int(os.getenv("KLEE_VISION_DAILY_LIMIT", "3"))
KLEE_IMAGE_DAILY_LIMIT = int(os.getenv("KLEE_IMAGE_DAILY_LIMIT", "0"))
PLAYLIST_LIMIT = int(os.getenv("PLAYLIST_LIMIT", "50"))
VISION_LIMIT_MESSAGE = "荣誉骑士今天让 Klee 看太多图片啦，Klee 的眼睛要变成蹦蹦炸弹了... 明天再来吧！"
IMAGE_LIMIT_MESSAGE = "荣誉骑士今天已经让 Klee 画过图啦，画笔要休息到明天哦~"

DEFAULT_HELP_TEXT = """Kleebot 指令：
`/禁言 成员 分钟 原因` 或 `!禁言 @成员 分钟 原因` — 公开显示禁言时长、解除时间和原因。
`/解除禁言 成员 原因` 或 `!解除禁言 @成员 原因` — 提前解除禁言并公开原因。
`/play` / `/播放` 播放 YouTube / B站 / NicoNico / Spotify 链接，或直接搜索歌曲
`/join` / `/加入` 加入你所在的语音频道
`/queue` / `/队列` 查看当前播放队列
`/nowplaying` / `/正在播放` 查看当前歌曲、队列数量、音量、滤镜和循环状态
`/shuffle` / `/打乱` 打乱当前等待队列
`/repeat` / `/循环` 设置循环模式：off、one、queue
`/filter` / `/滤镜` 设置音频滤镜：off、bassboost、nightcore、vaporwave、karaoke
`/profile` / `/档案` 查看你的点歌次数、求签次数和历史最好签
`/nickname` / `/称呼` 设置 Klee 对你的称呼
`/afk` / `/离开状态` 设置 AFK 状态，别人 @ 你时 Klee 会提醒
`/leaderboard` / `/排行榜` 查看排行榜：plays、fortunes、luck、bombed
`/fortune` / `/求签` 抽取今日幸运签
文字指令：`{PREFIX}play <链接或歌名>`、`{PREFIX}nowplaying`、`{PREFIX}shuffle`、`{PREFIX}repeat <mode>`、`{PREFIX}filter <mode>`、`{PREFIX}profile`、`{PREFIX}nickname <称呼>`、`{PREFIX}afk <原因>`、`{PREFIX}leaderboard <category>`、`{PREFIX}求签`、`{PREFIX}help`"""


def get_player(guild: discord.Guild) -> GuildPlayer:
    player = players.get(guild.id)
    if player is None:
        player = GuildPlayer(bot=bot, guild_id=guild.id, resolver=resolver)
        players[guild.id] = player
    return player


def load_help_text() -> str:
    try:
        text = HELP_TEXT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        text = DEFAULT_HELP_TEXT
    return text.replace("{PREFIX}", PREFIX)


def split_discord_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit].rstrip())
                line = line[limit:]
        current += line
    if current:
        chunks.append(current.rstrip())
    return chunks or [text[:limit]]


def extract_video_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://\S+", text)
    video_urls: list[str] = []
    for url in urls:
        clean_url = url.rstrip(">)].,，。!?！？")
        host = urlparse(clean_url).netloc.lower()
        if any(
            domain in host
            for domain in (
                "youtube.com",
                "youtu.be",
                "bilibili.com",
                "b23.tv",
                "nicovideo.jp",
                "nico.ms",
            )
        ):
            video_urls.append(clean_url)
    return video_urls[:KLEE_VIDEO_METADATA_LIMIT]


def is_spotify_track_url(text: str) -> bool:
    return "open.spotify.com/track/" in text.lower()


async def video_metadata_for_prompt(urls: list[str]) -> list[str]:
    metadata: list[str] = []
    for url in urls:
        try:
            item = await asyncio.wait_for(resolver.video_metadata(url), timeout=16)
        except Exception as exc:
            print(f"video metadata failed for {url}: {exc}", flush=True)
            continue
        metadata.append(item.prompt_text())
    return metadata


async def respond(interaction: discord.Interaction, message: str, ephemeral: bool = False) -> bool:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(message, ephemeral=ephemeral)
    except discord.NotFound:
        print("interaction response skipped: interaction is no longer available", flush=True)
        return False
    return True


async def respond_help(interaction: discord.Interaction) -> None:
    chunks = split_discord_message(load_help_text())
    if not await respond(interaction, chunks[0]):
        return
    for chunk in chunks[1:]:
        try:
            await interaction.followup.send(chunk)
        except discord.NotFound:
            print("help followup skipped: interaction is no longer available", flush=True)
            return


async def respond_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
    files: list[discord.File] | None = None,
    view: discord.ui.View | None = None,
) -> None:
    kwargs = {"embed": embed, "files": files or []}
    if view is not None:
        kwargs["view"] = view
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.NotFound:
        print("embed response skipped: interaction is no longer available", flush=True)


async def defer(interaction: discord.Interaction, ephemeral: bool = False) -> bool:
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        except discord.NotFound:
            print("defer skipped: interaction is no longer available", flush=True)
            return False
    return True


@tasks.loop(seconds=15)
async def health_heartbeat() -> None:
    ready_file = Path(os.getenv("KLEE_READY_FILE") or (Path(tempfile.gettempdir()) / "kleebot-ready"))
    if bot.is_ready():
        ready_file.write_text(str(time.time()), encoding="utf-8")
    else:
        ready_file.unlink(missing_ok=True)


@bot.event
async def on_ready() -> None:
    pairs = [(c.id,g.id) for g in bot.guilds for c in [*g.channels,*g.threads]]
    if conversations.memory:
        await asyncio.to_thread(conversations.memory.map_channels,pairs)
    Path(os.getenv("KLEE_READY_FILE") or (Path(tempfile.gettempdir()) / "kleebot-ready")).write_text(str(time.time()), encoding="utf-8")
    if not health_heartbeat.is_running():
        health_heartbeat.start()
    print(f"ready: {bot.user} in {len(bot.guilds)} guild(s)", flush=True)
    print(klee_ai.config_summary(), flush=True)
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"synced {len(synced)} command(s) to guild {GUILD_ID}", flush=True)
            await clear_global_commands()
        elif bot.guilds:
            for guild in bot.guilds:
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                print(f"synced {len(synced)} command(s) to guild {guild.id}", flush=True)
            await clear_global_commands()
        else:
            synced = await bot.tree.sync()
            print(f"synced {len(synced)} global command(s)", flush=True)
    except Exception as exc:
        print(f"slash sync failed: {exc}", flush=True)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if autonomy is not None and await autonomy.guard_tokens(message):
        return
    moderation_handled = False
    if spam_guard is not None:
        try:
            moderation_handled = await spam_guard.observe(message)
        except Exception as exc:
            print(f'spam guard failed: {type(exc).__name__}', flush=True)
    persona_hint = None
    if bot.user and bot.user in message.mentions and not message.content.startswith(PREFIX) and not moderation_handled:
        plain = re.sub(r'<@!?'+str(bot.user.id)+r'>', '', message.content).strip()
        persona_hint = await asyncio.to_thread(klee_ai.persona_modes.switch_hint, message.guild.id if message.guild else None, plain)
    if autonomy is not None and not moderation_handled and not persona_hint:
        try:
            moderation_handled = await autonomy.observe(message)
        except Exception as exc:
            print(f'agent observation failed: {type(exc).__name__}', flush=True)

    if afk_store.clear_afk(message.guild.id if message.guild else None, message.author.id):
        await message.reply("欢迎回来，Klee已经帮你取消 AFK 啦。")

    for mentioned_user in message.mentions:
        if bot.user and mentioned_user.id == bot.user.id:
            continue
        afk = afk_store.get_afk(message.guild.id if message.guild else None, mentioned_user.id)
        if afk:
            reason = afk.get("reason", DEFAULT_AFK_REASON)
            await message.reply(
                f"{message.author.mention}，{mentioned_user.mention} 现在 AFK：{reason}",
                delete_after=20,
            )
            break

    if persona_hint and not moderation_handled:
        await message.reply(persona_hint, allowed_mentions=discord.AllowedMentions.none())
        await bot.process_commands(message)
        return
    quoted = ReplyContext()
    if bot.user and not message.content.startswith(PREFIX) and not moderation_handled:
        quoted = await read_reply_context(message, bot.user.id)
    if bot.user and (bot.user in message.mentions or quoted.to_bot) and not message.content.startswith(PREFIX) and not moderation_handled:
        mention_text = message.content
        for mention in (bot.user.mention, f"<@!{bot.user.id}>"):
            mention_text = mention_text.replace(mention, "")
        image_urls = [
            attachment.url
            for attachment in message.attachments
            if (attachment.content_type or "").lower().startswith("image/")
        ]
        image_urls = list(dict.fromkeys(image_urls + quoted.images))[:4]
        if image_urls:
            allowed, _ = ai_usage.try_use(
                message.guild.id if message.guild else None,
                message.author.id,
                "vision",
                KLEE_VISION_DAILY_LIMIT,
            )
            if not allowed:
                await message.reply(f"{message.author.mention} {VISION_LIMIT_MESSAGE}")
                return
        video_metadata = await video_metadata_for_prompt(extract_video_urls(mention_text))
        document_context = await file_service.read_attachments(message.attachments)
        document_context = file_service.document_context((message.guild.id if message.guild else None, message.channel.id, message.author.id), mention_text, document_context)
        if await file_service.basic_extract(message, document_context):
            await bot.process_commands(message)
            return
        generation_context = document_context + ('\n'+quoted.text if quoted.text else '')
        if requested_format(mention_text):
            recent_chat = await asyncio.to_thread(conversations.store.context, message.channel.id)
            if recent_chat:
                generation_context += '\n最近频道对话（仅供本次生成参考）：\n' + '\n'.join(recent_chat)
        if await file_service.send_generated(message, mention_text, generation_context):
            await bot.process_commands(message)
            return
        reply = await conversations.reply(
            guild_id=message.guild.id if message.guild else None,
            document_context=document_context,
            reply_context=quoted.text,
            task_receipt=await asyncio.to_thread(autonomy.store.last_manager_reply, message.guild.id, message.author.id, message.channel.id) if autonomy is not None and message.guild else '',
            channel_id=message.channel.id,
            user_id=message.author.id,
            user_name=message.author.display_name,
            user_aliases=[message.author.name, getattr(message.author, "global_name", None)],
            mentioned_users=[
                {"id": str(person.id), "name": person.display_name,
                 "aliases": [person.name, getattr(person, "global_name", None)]}
                for person in message.mentions if person.id != bot.user.id
            ],
            message=mention_text,
            image_urls=image_urls,
            video_metadata=video_metadata,
        )
        await send_math_reply(message, reply)
    await bot.process_commands(message)


async def clear_global_commands() -> None:
    if bot.application_id is None:
        print("global command cleanup skipped: missing application id", flush=True)
        return
    await bot.http.bulk_upsert_global_commands(bot.application_id, [])
    print("cleared global command(s)", flush=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if not await respond(interaction, failure_receipt('指令',interaction,getattr(error,'original',error))):
        print("failed to report slash error: interaction is no longer available", flush=True)


def failure_receipt(category,context,error):
    actor = getattr(context,'user',None) or getattr(context,'author',None)
    incident = klee_ai.diagnostics.record(category,error,guild=getattr(context.guild,'id',None),
        channel=getattr(context.channel,'id',None),actor=getattr(actor,'id',None))
    return explanation(error)+f'（诊断编号：{incident}）'


async def handle_play(interaction: discord.Interaction, query: str) -> None:
    await defer(interaction)
    try:
        if interaction.guild is None:
            raise RuntimeError("Use this in a server.")
        print(f"play requested in {interaction.guild.id}: {query}", flush=True)
        track = await get_player(interaction.guild).enqueue(interaction, query)
        profile_stats.record_play(interaction.guild.id, interaction.user.id)
        prefix = "Queued Spotify match via YouTube audio" if is_spotify_track_url(query) else "Queued"
        await respond(interaction, f"{prefix}: [{track.title}]({track.webpage_url})")
    except Exception as exc:
        incident = klee_ai.diagnostics.record('点播',exc,guild=getattr(interaction.guild,'id',None),channel=getattr(interaction.channel,'id',None),actor=interaction.user.id)
        await respond(interaction, explanation(exc)+f'（诊断编号：{incident}）')


async def handle_playlist(interaction: discord.Interaction, query: str) -> None:
    await defer(interaction)
    try:
        if interaction.guild is None:
            raise RuntimeError("Use this in a server.")
        print(f"playlist requested in {interaction.guild.id}: {query}", flush=True)
        tracks = await get_player(interaction.guild).enqueue_playlist(interaction, query, PLAYLIST_LIMIT)
        profile_stats.record_play(interaction.guild.id, interaction.user.id)
        first = tracks[0]
        await respond(
            interaction,
            f"Queued {len(tracks)} track(s) from playlist. First: [{first.title}]({first.webpage_url})",
        )
    except Exception as exc:
        await respond(interaction, failure_receipt('播放列表',interaction,exc))


async def handle_skip(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"skip requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild and get_player(interaction.guild).skip(interaction.guild):
        await respond(interaction, "Skipped.")
    else:
        await respond(interaction, "Nothing is playing.")


async def handle_stop(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"stop requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    player = get_player(interaction.guild)
    player.clear()
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
    await respond(interaction, "Stopped and cleared queue.")


async def handle_join(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"join requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    try:
        voice_client = await get_player(interaction.guild).ensure_voice(interaction)
    except Exception as exc:
        await respond(interaction, failure_receipt('加入语音',interaction,exc))
        return
    await respond(interaction, f"Joined {voice_client.channel.mention}.")


async def handle_leave(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"leave requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild:
        bot.get_cog('MusicControls').set_stay(interaction.guild.id, None)
    if interaction.guild and interaction.guild.voice_client:
        await get_player(interaction.guild).disconnect(interaction.guild)
        await respond(interaction, "Left voice channel.")
    else:
        await respond(interaction, "I am not in a voice channel.")


async def handle_queue(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"queue requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    await respond(interaction, get_player(interaction.guild).queue_text())


async def handle_pause(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"pause requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_playing():
        vc.pause()
        await respond(interaction, "Paused.")
    else:
        await respond(interaction, "Nothing is playing.")


async def handle_resume(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"resume requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_paused():
        vc.resume()
        await respond(interaction, "Resumed.")
    else:
        await respond(interaction, "Nothing is paused.")


async def handle_volume(interaction: discord.Interaction, percent: int) -> None:
    await defer(interaction)
    print(f"volume requested in {interaction.guild.id if interaction.guild else 'dm'}: {percent}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    player = get_player(interaction.guild)
    player.volume = percent / 100
    vc = interaction.guild.voice_client
    if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = player.volume
    await respond(interaction, f"Volume set to {percent}%.")


async def handle_filter(interaction: discord.Interaction, mode: str) -> None:
    await defer(interaction)
    print(f"filter requested in {interaction.guild.id if interaction.guild else 'dm'}: {mode}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    try:
        selected = get_player(interaction.guild).set_filter(mode)
    except ValueError as exc:
        await respond(interaction, str(exc))
        return
    note = "It will apply from the next track. Use /skip to restart with the new filter."
    await respond(interaction, f"Filter set to `{selected}`. {note}")


async def handle_nowplaying(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"nowplaying requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    await respond(interaction, get_player(interaction.guild).now_playing_text())


async def handle_shuffle(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"shuffle requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    count = get_player(interaction.guild).shuffle()
    await respond(interaction, f"Shuffled {count} queued track(s).")


async def handle_repeat(interaction: discord.Interaction, mode: str) -> None:
    await defer(interaction)
    print(f"repeat requested in {interaction.guild.id if interaction.guild else 'dm'}: {mode}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    try:
        selected = get_player(interaction.guild).set_repeat(mode)
    except ValueError as exc:
        await respond(interaction, str(exc))
        return
    await respond(interaction, f"Repeat set to `{selected}`.")


async def handle_profile(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"profile requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    await respond(
        interaction,
        profile_stats.profile_text(
            interaction.guild.id if interaction.guild else None,
            interaction.user.id,
            interaction.user.display_name,
        ),
    )


async def handle_nickname(interaction: discord.Interaction, name: str | None) -> None:
    await defer(interaction)
    print(f"nickname requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    try:
        nickname = profile_stats.set_nickname(
            interaction.guild.id if interaction.guild else None,
            interaction.user.id,
            name or "",
        )
    except ValueError as exc:
        await respond(interaction, str(exc))
        return
    if nickname:
        await respond(interaction, f"Klee以后会叫你 {nickname}。")
    else:
        await respond(interaction, "Klee已经忘掉你的特殊称呼啦。")


async def handle_afk(interaction: discord.Interaction, reason: str | None) -> None:
    await defer(interaction)
    print(f"afk requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    afk_store.set_afk(
        interaction.guild.id if interaction.guild else None,
        interaction.user.id,
        reason or DEFAULT_AFK_REASON,
    )
    await respond(interaction, f"{interaction.user.mention} 现在 AFK：{reason or DEFAULT_AFK_REASON}", ephemeral=True)


async def handle_leaderboard(interaction: discord.Interaction, category: str) -> None:
    await defer(interaction)
    print(f"leaderboard requested in {interaction.guild.id if interaction.guild else 'dm'}: {category}", flush=True)
    try:
        text = profile_stats.leaderboard_text(interaction.guild.id if interaction.guild else None, category)
    except ValueError as exc:
        await respond(interaction, str(exc))
        return
    await respond(interaction, text)


async def handle_bomb(interaction: discord.Interaction, target: discord.abc.User, count: int = 1) -> None:
    await defer(interaction)
    if count < 1:
        await respond(interaction, '炸弹数量必须是正整数。', ephemeral=True)
        return
    print(f"bomb requested in {interaction.guild.id if interaction.guild else 'dm'}: {target.id}", flush=True)
    spent, bombs_left = profile_stats.spend_bomb(
        interaction.guild.id if interaction.guild else None,
        interaction.user.id,
        count,
    )
    if not spent:
        await respond(interaction, NO_BOMBS_MESSAGE, ephemeral=True)
        return
    if bot.user and target.id == bot.user.id:
        profile_stats.record_bombed(interaction.guild.id if interaction.guild else None, interaction.user.id, spent)
        await respond(interaction, f"{interaction.user.mention} 你的妈妈也是魔女吗敢这么和Klee说话。\n{bomb_text(interaction.user)}\n本次投掷：{spent} 个\n剩余炸弹：{bombs_left}")
        return
    profile_stats.record_bombed(interaction.guild.id if interaction.guild else None, target.id, spent)
    await respond(interaction, f"{bomb_text(target)}\n本次投掷：{spent} 个\n剩余炸弹：{bombs_left}")


def consume_negative_prompt_flag(prompt: str) -> tuple[str, str | None]:
    lowered = prompt.lower()
    off_prefixes = ("--no-negative", "--nonegative", "--关负面", "--无负面")
    for prefix in off_prefixes:
        if lowered == prefix or lowered.startswith(prefix + " "):
            return prompt[len(prefix):].strip(), ""

    for prefix in ("--negative", "--负面"):
        if lowered.startswith(prefix + "="):
            value, _, rest = prompt[len(prefix) + 1 :].partition(" -- ")
            return rest.strip(), value.strip()
        if lowered.startswith(prefix + ' "'):
            end = prompt.find('"', len(prefix) + 2)
            if end != -1:
                return prompt[end + 1 :].strip(), prompt[len(prefix) + 2 : end].strip()
        if lowered.startswith(prefix + " '"):
            end = prompt.find("'", len(prefix) + 2)
            if end != -1:
                return prompt[end + 1 :].strip(), prompt[len(prefix) + 2 : end].strip()
    return prompt, None


def parse_image_prompt_mode(prompt: str) -> tuple[str, bool, str, str | None]:
    prompt = prompt.strip()
    aspect = "square"
    plain_language = True
    negative_prompt: str | None = None
    aspect_prefixes = {
        "--square ": "square",
        "--portrait ": "portrait",
        "--vertical ": "portrait",
        "--landscape ": "landscape",
        "--horizontal ": "landscape",
        "--方图 ": "square",
        "--竖图 ": "portrait",
        "--横图 ": "landscape",
    }
    mode_prefixes = {
        "--tags ": False,
        "--tag ": False,
        "--prompt ": False,
        "--raw ": False,
        "--原始 ": False,
        "--标签 ": False,
        "--plain ": True,
        "--自然语言 ": True,
        "--大白话 ": True,
    }
    while True:
        lowered = prompt.lower()
        matched = False
        next_prompt, parsed_negative = consume_negative_prompt_flag(prompt)
        if parsed_negative is not None:
            prompt = next_prompt
            negative_prompt = parsed_negative
            continue
        for prefix, value in aspect_prefixes.items():
            if lowered.startswith(prefix):
                prompt = prompt[len(prefix):].strip()
                aspect = value
                matched = True
                break
        if matched:
            continue
        for prefix, value in mode_prefixes.items():
            if lowered.startswith(prefix):
                prompt = prompt[len(prefix):].strip()
                plain_language = value
                matched = True
                break
        if not matched:
            break
    return prompt, plain_language, aspect, negative_prompt


def resolve_negative_prompt(mode: str = "default", custom_negative: str | None = None) -> str | None:
    if custom_negative and custom_negative.strip():
        return custom_negative.strip()[:4000]
    if mode == "off":
        return ""
    return None


def has_nsfw_tag(prompt: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])nsfw(?![A-Za-z0-9_])", prompt, re.IGNORECASE) is not None


def image_file(image: bytes, *, spoiler: bool = False) -> discord.File:
    filename = "SPOILER_klee-image.png" if spoiler else "klee-image.png"
    return discord.File(BytesIO(image), filename=filename, spoiler=spoiler)


async def handle_imagine(
    interaction: discord.Interaction,
    prompt: str,
    mode: str = "plain",
    aspect: str = "square",
    negative: str = "default",
    custom_negative: str | None = None,
) -> None:
    if not await defer(interaction):
        return
    prompt = prompt.strip()
    plain_language = mode != "tags"
    print(f"imagine requested in {interaction.guild.id if interaction.guild else 'dm'} aspect={aspect}", flush=True)
    if not prompt:
        await respond(interaction, "告诉 Klee 你想画什么就好啦。")
        return
    if KLEE_IMAGE_DAILY_LIMIT > 0:
        allowed, _ = ai_usage.try_use(
            interaction.guild.id if interaction.guild else None,
            interaction.user.id,
            "image",
            KLEE_IMAGE_DAILY_LIMIT,
        )
        if not allowed:
            await respond(interaction, IMAGE_LIMIT_MESSAGE)
            return
    try:
        result = await klee_ai.generate_image_with_prompt(
            prompt=prompt[:32000],
            plain_language=plain_language,
            aspect=aspect,
            negative_prompt=resolve_negative_prompt(negative, custom_negative),
        )
    except Exception as exc:
        if KLEE_IMAGE_DAILY_LIMIT > 0:
            ai_usage.refund(
                interaction.guild.id if interaction.guild else None,
                interaction.user.id,
                "image",
            )
        print(f"image generation failed: {exc}", flush=True)
        await respond(interaction, f"Klee 画不出来这个... {exc}")
        return
    file = image_file(result.data, spoiler=has_nsfw_tag(result.prompt))
    try:
        await interaction.followup.send(content=f"{interaction.user.mention} Klee 画好啦！", file=file)
    except discord.NotFound:
        print("image response skipped: interaction is no longer available", flush=True)


async def handle_nai_usage(interaction: discord.Interaction) -> None:
    await defer(interaction, ephemeral=True)
    print(f"naiusage requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    await respond(interaction, await klee_ai.novelai_usage_text(), ephemeral=True)


async def handle_promptgen(interaction: discord.Interaction, prompt: str, aspect: str = "square") -> None:
    await defer(interaction, ephemeral=True)
    print(f"promptgen requested in {interaction.guild.id if interaction.guild else 'dm'} aspect={aspect}", flush=True)
    try:
        generated = await klee_ai.novelai_prompt_text(prompt=prompt[:4000], aspect=aspect)
    except Exception as exc:
        await respond(interaction, f"Klee 整理不出来这个提示词... {exc}", ephemeral=True)
        return
    await respond(interaction, f"```text\n{generated[:1800]}\n```", ephemeral=True)


def bomb_text(target: discord.abc.User) -> str:
    emote = bot.get_emoji(KLEE_REEE_EMOTE_ID)
    if emote is None:
        return (
            f"Klee炸死 {target.mention} 你这个王八蛋\n"
            "KleeREEE 表情发不出来：bot 当前看不到这个自定义表情。"
        )
    return f"Klee炸死 {target.mention} 你这个王八蛋{emote}{emote}{emote}"


@bot.tree.command(name="play", description="Play a YouTube, Bilibili, NicoNico, Spotify link, or search query.")
@app_commands.describe(query="Song name, search text, or a YouTube, Bilibili, NicoNico, Spotify link")
async def slash_play(interaction: discord.Interaction, query: str) -> None:
    await handle_play(interaction, query)


@bot.tree.command(name="playlist", description="Queue tracks from a YouTube, Bilibili, or NicoNico playlist URL.")
@app_commands.describe(query="Playlist, album, collection, or series URL")
async def slash_playlist(interaction: discord.Interaction, query: str) -> None:
    await handle_playlist(interaction, query)


@bot.tree.command(name="playalbum", description="Queue tracks from a playlist or album URL.")
@app_commands.describe(query="Playlist, album, collection, or series URL")
async def slash_playalbum(interaction: discord.Interaction, query: str) -> None:
    await handle_playlist(interaction, query)


@bot.tree.command(name="专辑", description="播放专辑、歌单、合集或列表链接。")
@app_commands.describe(query="专辑、歌单、合集或列表链接")
async def slash_playlist_cn(interaction: discord.Interaction, query: str) -> None:
    await handle_playlist(interaction, query)


@bot.tree.command(name="播放", description="播放 YouTube / B站 / NicoNico / Spotify 链接，或直接搜索歌曲。")
@app_commands.describe(query="歌曲名、搜索词，或 YouTube / B站 / NicoNico / Spotify 链接")
async def slash_play_cn(interaction: discord.Interaction, query: str) -> None:
    await handle_play(interaction, query)


@bot.tree.command(name="skip", description="Skip the current track.")
async def slash_skip(interaction: discord.Interaction) -> None:
    await handle_skip(interaction)


@bot.tree.command(name="跳过", description="跳过当前正在播放的歌曲。")
async def slash_skip_cn(interaction: discord.Interaction) -> None:
    await handle_skip(interaction)


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def slash_stop(interaction: discord.Interaction) -> None:
    await handle_stop(interaction)


@bot.tree.command(name="停止", description="停止播放，并清空当前队列。")
async def slash_stop_cn(interaction: discord.Interaction) -> None:
    await handle_stop(interaction)


@bot.tree.command(name="join", description="Join your current voice channel.")
async def slash_join(interaction: discord.Interaction) -> None:
    await handle_join(interaction)


@bot.tree.command(name="加入", description="让 Kleebot 加入你所在的语音频道。")
async def slash_join_cn(interaction: discord.Interaction) -> None:
    await handle_join(interaction)


@bot.tree.command(name="leave", description="Leave the voice channel.")
async def slash_leave(interaction: discord.Interaction) -> None:
    await handle_leave(interaction)


@bot.tree.command(name="离开", description="让 Kleebot 离开语音频道。")
async def slash_leave_cn(interaction: discord.Interaction) -> None:
    await handle_leave(interaction)


@bot.tree.command(name="queue", description="Show the current music queue.")
async def slash_queue(interaction: discord.Interaction) -> None:
    await handle_queue(interaction)


@bot.tree.command(name="队列", description="查看当前播放队列。")
async def slash_queue_cn(interaction: discord.Interaction) -> None:
    await handle_queue(interaction)


@bot.tree.command(name="pause", description="Pause playback.")
async def slash_pause(interaction: discord.Interaction) -> None:
    await handle_pause(interaction)


@bot.tree.command(name="暂停", description="暂停当前播放。")
async def slash_pause_cn(interaction: discord.Interaction) -> None:
    await handle_pause(interaction)


@bot.tree.command(name="resume", description="Resume playback.")
async def slash_resume(interaction: discord.Interaction) -> None:
    await handle_resume(interaction)


@bot.tree.command(name="继续", description="继续播放暂停中的歌曲。")
async def slash_resume_cn(interaction: discord.Interaction) -> None:
    await handle_resume(interaction)


@bot.tree.command(name="volume", description="Set playback volume from 1 to 100.")
async def slash_volume(interaction: discord.Interaction, percent: app_commands.Range[int, 1, 100]) -> None:
    await handle_volume(interaction, percent)


@bot.tree.command(name="音量", description="设置音量，范围 1 到 100。")
async def slash_volume_cn(interaction: discord.Interaction, percent: app_commands.Range[int, 1, 100]) -> None:
    await handle_volume(interaction, percent)


@bot.tree.command(name="filter", description="Set an audio filter for future tracks.")
@app_commands.describe(mode="Audio filter to use")
@app_commands.choices(mode=FILTER_CHOICES)
async def slash_filter(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    await handle_filter(interaction, mode.value)


@bot.tree.command(name="滤镜", description="设置后续歌曲使用的音频滤镜。")
@app_commands.describe(mode="要使用的音频滤镜")
@app_commands.choices(mode=FILTER_CHOICES)
async def slash_filter_cn(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    await handle_filter(interaction, mode.value)


@bot.tree.command(name="nowplaying", description="Show the current track and player status.")
async def slash_nowplaying(interaction: discord.Interaction) -> None:
    await handle_nowplaying(interaction)


@bot.tree.command(name="正在播放", description="查看当前歌曲和播放器状态。")
async def slash_nowplaying_cn(interaction: discord.Interaction) -> None:
    await handle_nowplaying(interaction)


@bot.tree.command(name="shuffle", description="Shuffle the queued tracks.")
async def slash_shuffle(interaction: discord.Interaction) -> None:
    await handle_shuffle(interaction)


@bot.tree.command(name="打乱", description="打乱当前等待队列。")
async def slash_shuffle_cn(interaction: discord.Interaction) -> None:
    await handle_shuffle(interaction)


@bot.tree.command(name="repeat", description="Set repeat mode.")
@app_commands.describe(mode="Repeat mode")
@app_commands.choices(mode=REPEAT_CHOICES)
async def slash_repeat(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    await handle_repeat(interaction, mode.value)


@bot.tree.command(name="循环", description="设置循环模式。")
@app_commands.describe(mode="循环模式")
@app_commands.choices(mode=REPEAT_CHOICES)
async def slash_repeat_cn(interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
    await handle_repeat(interaction, mode.value)


@bot.tree.command(name="profile", description="Show your Kleebot profile.")
async def slash_profile(interaction: discord.Interaction) -> None:
    await handle_profile(interaction)


@bot.tree.command(name="档案", description="查看你的 Kleebot 档案。")
async def slash_profile_cn(interaction: discord.Interaction) -> None:
    await handle_profile(interaction)


@bot.tree.command(name="nickname", description="Set or clear your Kleebot nickname.")
@app_commands.describe(name="Nickname to use; leave empty to clear")
async def slash_nickname(interaction: discord.Interaction, name: str | None = None) -> None:
    await handle_nickname(interaction, name)


@bot.tree.command(name="称呼", description="设置或清除 Kleebot 对你的称呼。")
@app_commands.describe(name="要使用的称呼；留空则清除")
async def slash_nickname_cn(interaction: discord.Interaction, name: str | None = None) -> None:
    await handle_nickname(interaction, name)


@bot.tree.command(name="afk", description="Set your AFK status.")
@app_commands.describe(reason="Why you are AFK")
async def slash_afk(interaction: discord.Interaction, reason: str | None = None) -> None:
    await handle_afk(interaction, reason)


@bot.tree.command(name="离开状态", description="设置你的 AFK 状态。")
@app_commands.describe(reason="AFK 原因")
async def slash_afk_cn(interaction: discord.Interaction, reason: str | None = None) -> None:
    await handle_afk(interaction, reason)


@bot.tree.command(name="leaderboard", description="Show Kleebot server leaderboards.")
@app_commands.describe(category="Leaderboard category")
@app_commands.choices(category=LEADERBOARD_CHOICES)
async def slash_leaderboard(
    interaction: discord.Interaction,
    category: app_commands.Choice[str] | None = None,
) -> None:
    await handle_leaderboard(interaction, category.value if category else "plays")


@bot.tree.command(name="排行榜", description="查看 Kleebot 服务器排行榜。")
@app_commands.describe(category="排行榜分类")
@app_commands.choices(category=LEADERBOARD_CHOICES)
async def slash_leaderboard_cn(
    interaction: discord.Interaction,
    category: app_commands.Choice[str] | None = None,
) -> None:
    await handle_leaderboard(interaction, category.value if category else "plays")


@bot.tree.command(name="bomb", description="Let Klee bomb a target.")
@app_commands.describe(target="Target user", count="Number to throw; exceeding your inventory uses all remaining bombs")
async def slash_bomb(interaction: discord.Interaction, target: discord.Member, count: app_commands.Range[int, 1] = 1) -> None:
    await handle_bomb(interaction, target, count)


@bot.tree.command(name="炸弹", description="让 Klee 炸一个目标。")
@app_commands.describe(target="目标用户", count="投掷数量，超过持有量时全部投出；默认1个。")
@app_commands.rename(count="数量")
async def slash_bomb_cn(interaction: discord.Interaction, target: discord.Member, count: app_commands.Range[int, 1] = 1) -> None:
    await handle_bomb(interaction, target, count)


@bot.tree.command(name="imagine", description="Ask Klee to draw an image.")
@app_commands.describe(prompt="Describe what you want Klee to draw")
@app_commands.describe(mode="Plain language or raw image tags")
@app_commands.describe(aspect="Image aspect ratio")
@app_commands.describe(negative="Negative prompt mode")
@app_commands.describe(custom_negative="Custom negative prompt, used when filled")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="Plain language", value="plain"),
        app_commands.Choice(name="Raw prompt/tags", value="tags"),
    ],
    aspect=IMAGE_ASPECT_CHOICES,
    negative=NEGATIVE_PROMPT_CHOICES,
)
async def slash_imagine(
    interaction: discord.Interaction,
    prompt: str,
    mode: str = "plain",
    aspect: str = "square",
    negative: str = "default",
    custom_negative: str | None = None,
) -> None:
    await handle_imagine(interaction, prompt, mode, aspect, negative, custom_negative)


@bot.tree.command(name="生图", description="让 Klee 根据描述生成图片。")
@app_commands.describe(prompt="想让 Klee 画什么")
@app_commands.describe(mode="大白话，还是自己写好的提示词")
@app_commands.describe(aspect="画幅")
@app_commands.describe(negative="负面提示词模式")
@app_commands.describe(custom_negative="自定义负面提示词，填了就会使用这里")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="大白话", value="plain"),
        app_commands.Choice(name="提示词/标签", value="tags"),
    ],
    aspect=[
        app_commands.Choice(name="方图 1:1", value="square"),
        app_commands.Choice(name="竖图 2:3", value="portrait"),
        app_commands.Choice(name="横图 3:2", value="landscape"),
    ],
    negative=[
        app_commands.Choice(name="默认", value="default"),
        app_commands.Choice(name="关闭", value="off"),
        app_commands.Choice(name="自定义", value="custom"),
    ],
)
async def slash_imagine_cn(
    interaction: discord.Interaction,
    prompt: str,
    mode: str = "plain",
    aspect: str = "square",
    negative: str = "default",
    custom_negative: str | None = None,
) -> None:
    await handle_imagine(interaction, prompt, mode, aspect, negative, custom_negative)


@bot.tree.command(name="naiusage", description="Check NovelAI model, Anlas, and usage settings.")
async def slash_naiusage(interaction: discord.Interaction) -> None:
    await handle_nai_usage(interaction)


@bot.tree.command(name="额度", description="查看 NovelAI 生图额度和设置。")
async def slash_naiusage_cn(interaction: discord.Interaction) -> None:
    await handle_nai_usage(interaction)


@bot.tree.command(name="promptgen", description="Turn plain language into a NovelAI prompt.")
@app_commands.describe(prompt="Plain-language image description")
@app_commands.describe(aspect="Image aspect ratio")
@app_commands.choices(aspect=IMAGE_ASPECT_CHOICES)
async def slash_promptgen(interaction: discord.Interaction, prompt: str, aspect: str = "square") -> None:
    await handle_promptgen(interaction, prompt, aspect)


@bot.tree.command(name="提示词", description="把大白话整理成 NovelAI 提示词。")
@app_commands.describe(prompt="大白话图片描述")
@app_commands.describe(aspect="画幅")
@app_commands.choices(
    aspect=[
        app_commands.Choice(name="方图 1:1", value="square"),
        app_commands.Choice(name="竖图 2:3", value="portrait"),
        app_commands.Choice(name="横图 3:2", value="landscape"),
    ]
)
async def slash_promptgen_cn(interaction: discord.Interaction, prompt: str, aspect: str = "square") -> None:
    await handle_promptgen(interaction, prompt, aspect)


@bot.tree.command(name="help", description="Show Kleebot command help.")
async def slash_help(interaction: discord.Interaction) -> None:
    await respond_help(interaction)


@bot.tree.command(name="帮助", description="查看 Kleebot 的指令说明。")
async def slash_help_cn(interaction: discord.Interaction) -> None:
    await respond_help(interaction)


@bot.tree.command(name="fortune", description="Draw today's fortune.")
async def slash_fortune(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"fortune requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    cooldown = fortune_cooldowns.check(interaction.guild.id if interaction.guild else None, interaction.user.id)
    if cooldown.active:
        await respond(interaction, cooldown.message or "你的求签指令还在冷却中哦 ~")
        return
    result = draw_daily_fortune(user_id=interaction.user.id, guild_id=interaction.guild.id if interaction.guild else None)
    fortune_cooldowns.mark_used(interaction.guild.id if interaction.guild else None, interaction.user.id)
    profile_stats.record_fortune(interaction.guild.id if interaction.guild else None, interaction.user.id, result.label, result.luck_delta)
    _, bombs = profile_stats.claim_daily_bombs(interaction.guild.id if interaction.guild else None, interaction.user.id)
    embed = build_fortune_embed(interaction.user, interaction.guild, result)
    embed.add_field(name="签到", value=f"Klee做好了 3 个炸弹分给你啦！你现在有 {bombs} 个炸弹。", inline=False)
    view = FortuneRerollView(interaction.user, interaction.guild, result) if result.can_reroll else None
    await respond_embed(interaction, embed, fortune_files(), view)


@bot.tree.command(name="求签", description="抽取今日幸运签。")
async def slash_qiuqian_cn(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"求签 requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    cooldown = fortune_cooldowns.check(interaction.guild.id if interaction.guild else None, interaction.user.id)
    if cooldown.active:
        await respond(interaction, cooldown.message or "你的求签指令还在冷却中哦 ~")
        return
    result = draw_daily_fortune(user_id=interaction.user.id, guild_id=interaction.guild.id if interaction.guild else None)
    fortune_cooldowns.mark_used(interaction.guild.id if interaction.guild else None, interaction.user.id)
    profile_stats.record_fortune(interaction.guild.id if interaction.guild else None, interaction.user.id, result.label, result.luck_delta)
    _, bombs = profile_stats.claim_daily_bombs(interaction.guild.id if interaction.guild else None, interaction.user.id)
    embed = build_fortune_embed(interaction.user, interaction.guild, result)
    embed.add_field(name="签到", value=f"Klee做好了 3 个炸弹分给你啦！你现在有 {bombs} 个炸弹。", inline=False)
    view = FortuneRerollView(interaction.user, interaction.guild, result) if result.can_reroll else None
    await respond_embed(interaction, embed, fortune_files(), view)


@bot.command(name="play", aliases=["p"])
async def prefix_play(ctx: commands.Context, *, query: str) -> None:
    if ctx.guild is None:
        return
    interaction = _ContextInteraction(ctx)
    message = await ctx.send("Resolving...")
    try:
        track = await get_player(ctx.guild).enqueue(interaction, query)
        profile_stats.record_play(ctx.guild.id, ctx.author.id)
        prefix = "Queued Spotify match via YouTube audio" if is_spotify_track_url(query) else "Queued"
        await message.edit(content=f"{prefix}: {track.title}\n{track.webpage_url}")
    except Exception as exc:
        await message.edit(content=failure_receipt('点播',ctx,exc))


@bot.command(name="playlist", aliases=["playalbum", "album", "专辑", "歌单", "列表"])
async def prefix_playlist(ctx: commands.Context, *, query: str) -> None:
    if ctx.guild is None:
        return
    interaction = _ContextInteraction(ctx)
    message = await ctx.send("Resolving playlist...")
    try:
        tracks = await get_player(ctx.guild).enqueue_playlist(interaction, query, PLAYLIST_LIMIT)
        profile_stats.record_play(ctx.guild.id, ctx.author.id)
        first = tracks[0]
        await message.edit(content=f"Queued {len(tracks)} track(s). First: {first.title}\n{first.webpage_url}")
    except Exception as exc:
        await message.edit(content=failure_receipt('播放列表',ctx,exc))


@bot.command(name="skip", aliases=["s"])
async def prefix_skip(ctx: commands.Context) -> None:
    if ctx.guild and get_player(ctx.guild).skip(ctx.guild):
        await ctx.send("Skipped.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command(name="stop")
async def prefix_stop(ctx: commands.Context) -> None:
    if ctx.guild is None:
        return
    player = get_player(ctx.guild)
    player.clear()
    if ctx.guild.voice_client:
        ctx.guild.voice_client.stop()
    await ctx.send("Stopped and cleared queue.")


@bot.command(name="join", aliases=["j", "加入"])
async def prefix_join(ctx: commands.Context) -> None:
    if ctx.guild is None:
        return
    try:
        voice_client = await get_player(ctx.guild).ensure_voice(_ContextInteraction(ctx))
    except Exception as exc:
        await ctx.send(failure_receipt('加入语音',ctx,exc))
        return
    await ctx.send(f"Joined {voice_client.channel.mention}.")


@bot.command(name="leave")
async def prefix_leave(ctx: commands.Context) -> None:
    if ctx.guild:
        bot.get_cog('MusicControls').set_stay(ctx.guild.id, None)
    if ctx.guild and ctx.guild.voice_client:
        await get_player(ctx.guild).disconnect(ctx.guild)
        await ctx.send("Left voice channel.")
    else:
        await ctx.send("I am not in a voice channel.")


@bot.command(name="queue", aliases=["q"])
async def prefix_queue(ctx: commands.Context) -> None:
    if ctx.guild:
        await ctx.send(get_player(ctx.guild).queue_text())


@bot.command(name="filter", aliases=["滤镜"])
async def prefix_filter(ctx: commands.Context, mode: str) -> None:
    if ctx.guild is None:
        return
    try:
        selected = get_player(ctx.guild).set_filter(mode)
    except ValueError as exc:
        await ctx.send(str(exc))
        return
    await ctx.send(f"Filter set to `{selected}`. It will apply from the next track.")


@bot.command(name="nowplaying", aliases=["np", "正在播放"])
async def prefix_nowplaying(ctx: commands.Context) -> None:
    if ctx.guild:
        await ctx.send(get_player(ctx.guild).now_playing_text())


@bot.command(name="shuffle", aliases=["打乱"])
async def prefix_shuffle(ctx: commands.Context) -> None:
    if ctx.guild:
        count = get_player(ctx.guild).shuffle()
        await ctx.send(f"Shuffled {count} queued track(s).")


@bot.command(name="repeat", aliases=["循环"])
async def prefix_repeat(ctx: commands.Context, mode: str) -> None:
    if ctx.guild is None:
        return
    try:
        selected = get_player(ctx.guild).set_repeat(mode)
    except ValueError as exc:
        await ctx.send(str(exc))
        return
    await ctx.send(f"Repeat set to `{selected}`.")


@bot.command(name="profile", aliases=["档案"])
async def prefix_profile(ctx: commands.Context) -> None:
    await ctx.send(profile_stats.profile_text(ctx.guild.id if ctx.guild else None, ctx.author.id, ctx.author.display_name))


@bot.command(name="nickname", aliases=["称呼"])
async def prefix_nickname(ctx: commands.Context, *, name: str = "") -> None:
    try:
        nickname = profile_stats.set_nickname(ctx.guild.id if ctx.guild else None, ctx.author.id, name)
    except ValueError as exc:
        await ctx.send(str(exc))
        return
    if nickname:
        await ctx.send(f"Klee以后会叫你 {nickname}。")
    else:
        await ctx.send("Klee已经忘掉你的特殊称呼啦。")


@bot.command(name="afk", aliases=["离开状态"])
async def prefix_afk(ctx: commands.Context, *, reason: str = DEFAULT_AFK_REASON) -> None:
    afk_store.set_afk(ctx.guild.id if ctx.guild else None, ctx.author.id, reason)
    await ctx.send(f"{ctx.author.mention} 现在 AFK：{reason}")


@bot.command(name="leaderboard", aliases=["lb", "排行榜"])
async def prefix_leaderboard(ctx: commands.Context, category: str = "plays") -> None:
    try:
        await ctx.send(profile_stats.leaderboard_text(ctx.guild.id if ctx.guild else None, category))
    except ValueError as exc:
        await ctx.send(str(exc))


@bot.command(name="bomb", aliases=["炸弹"])
async def prefix_bomb(ctx: commands.Context, target: discord.Member, count: int = 1) -> None:
    if count < 1:
        await ctx.send('炸弹数量必须是正整数。')
        return
    await handle_bomb(_ContextInteraction(ctx), target, count)


@bot.command(name="imagine", aliases=["生图", "画图"])
async def prefix_imagine(ctx: commands.Context, *, prompt: str) -> None:
    message = await ctx.send("Klee 正在画图...")
    prompt, plain_language, aspect, negative_prompt = parse_image_prompt_mode(prompt)
    if KLEE_IMAGE_DAILY_LIMIT > 0:
        allowed, _ = ai_usage.try_use(ctx.guild.id if ctx.guild else None, ctx.author.id, "image", KLEE_IMAGE_DAILY_LIMIT)
        if not allowed:
            await message.edit(content=IMAGE_LIMIT_MESSAGE)
            return
    try:
        result = await klee_ai.generate_image_with_prompt(
            prompt=prompt.strip()[:32000],
            plain_language=plain_language,
            aspect=aspect,
            negative_prompt=negative_prompt,
        )
    except Exception as exc:
        if KLEE_IMAGE_DAILY_LIMIT > 0:
            ai_usage.refund(ctx.guild.id if ctx.guild else None, ctx.author.id, "image")
        print(f"image generation failed: {exc}", flush=True)
        await message.edit(content=f"Klee 画不出来这个... {exc}")
        return
    await message.delete()
    await ctx.send(
        f"{ctx.author.mention} Klee 画好啦！",
        file=image_file(result.data, spoiler=has_nsfw_tag(result.prompt)),
    )


@bot.command(name="naiusage", aliases=["额度", "nai额度"])
async def prefix_naiusage(ctx: commands.Context) -> None:
    await ctx.send(await klee_ai.novelai_usage_text())


@bot.command(name="promptgen", aliases=["提示词"])
async def prefix_promptgen(ctx: commands.Context, *, prompt: str) -> None:
    prompt, _, aspect, _ = parse_image_prompt_mode(prompt)
    try:
        generated = await klee_ai.novelai_prompt_text(prompt=prompt[:4000], aspect=aspect)
    except Exception as exc:
        await ctx.send(f"Klee 整理不出来这个提示词... {exc}")
        return
    await ctx.send(f"```text\n{generated[:1800]}\n```")


@bot.hybrid_command(name="抛硬币", description="让 Klee 抛一枚硬币，随机得到正面或反面，不消耗模型额度。")
@commands.cooldown(2, 5, commands.BucketType.user)
async def prefix_coin(ctx: commands.Context) -> None:
    result = secrets.choice(('正面', '反面'))
    await ctx.send(f'🪙 Klee 把硬币抛起来啦——是 **{result}**！',
                   allowed_mentions=discord.AllowedMentions.none())


@bot.hybrid_command(name="开发", aliases=["develop"], description="提交自维护开发需求，生成 GitHub 草稿修改（仅所有者）。")
@app_commands.describe(task="要修复的问题或开发需求，最多4000字。")
async def prefix_develop(ctx: commands.Context, *, task: str) -> None:
    if not is_owner(ctx.author.id):
        await ctx.send("这个指令只开放给配置的主人。")
        return
    async with ctx.typing():
        try:
            result = await operations.submit(ctx,task)
        except (ValueError, RuntimeError) as exc:
            await ctx.send(str(exc))
            return
        except Exception:
            await ctx.send("开发任务暂时无法提交，请检查网络和宿主机日志。")
            return
    await ctx.send(result, allowed_mentions=discord.AllowedMentions.none())


@bot.hybrid_command(name="更新", aliases=["update"], description="请求检查 main 分支、测试并安装新版（仅所有者）。")
async def prefix_update(ctx: commands.Context) -> None:
    if not is_owner(ctx.author.id):
        await ctx.send("这个指令只开放给配置的主人。")
        return
    if os.name == 'nt' and os.getenv('KLEE_LOCAL_SUPERVISED') != '1':
        await ctx.send("当前是普通本机模式，请先停止 Klee，再双击 start-maintained.cmd 启用自维护。")
        return
    request_update(DATA_DIR)
    await ctx.send("已提交更新检查请求。更新程序会检查 main、测试新版并重启；结果请看 /更新状态。")


@bot.hybrid_command(name="更新状态", aliases=["updatestatus"], description="查看当前部署版本和最近更新结果（仅所有者）。")
async def prefix_update_status(ctx: commands.Context) -> None:
    if not is_owner(ctx.author.id):
        await ctx.send("这个指令只开放给配置的主人。")
        return
    await ctx.send(update_status(DATA_DIR), allowed_mentions=discord.AllowedMentions.none())


@bot.hybrid_command(name='自维护检查', description='检查更新守护和开发任务配置（仅所有者）。')
async def prefix_maintenance_check(ctx: commands.Context) -> None:
    if not is_owner(ctx.author.id):
        await ctx.send('这个指令只开放给配置的主人。',ephemeral=True)
        return
    await ctx.send(maintenance_check(DATA_DIR),ephemeral=True,allowed_mentions=discord.AllowedMentions.none())


@bot.hybrid_command(name="清除聊天", aliases=["forgetchat"], description="清除本频道近期聊天与文件缓存；共享个人记忆请本人要求忘掉。")
async def prefix_forgetchat(ctx: commands.Context) -> None:
    if ctx.guild and not ctx.channel.permissions_for(ctx.author).manage_messages:
        await ctx.send("清除频道聊天记录需要管理消息权限。")
        return
    if ctx.interaction:
        await ctx.defer(ephemeral=True)
    await conversations.clear(ctx.channel.id,ctx.guild.id if ctx.guild else None)
    file_service.clear_context(ctx.channel.id)
    await ctx.send("Klee 已清除这个频道的近期聊天和文件缓存。服务器共享的个人记忆保留，请本人明确说要忘掉哪一项。")


@bot.command(name="help")
async def prefix_help(ctx: commands.Context) -> None:
    for chunk in split_discord_message(load_help_text()):
        await ctx.send(chunk)


@bot.command(name="qiuqian", aliases=["求签", "抽签", "fortune", "luck"])
async def prefix_qiuqian(ctx: commands.Context) -> None:
    cooldown = fortune_cooldowns.check(ctx.guild.id if ctx.guild else None, ctx.author.id)
    if cooldown.active:
        await ctx.send(cooldown.message or "你的求签指令还在冷却中哦 ~")
        return
    result = draw_daily_fortune(user_id=ctx.author.id, guild_id=ctx.guild.id if ctx.guild else None)
    fortune_cooldowns.mark_used(ctx.guild.id if ctx.guild else None, ctx.author.id)
    profile_stats.record_fortune(ctx.guild.id if ctx.guild else None, ctx.author.id, result.label, result.luck_delta)
    _, bombs = profile_stats.claim_daily_bombs(ctx.guild.id if ctx.guild else None, ctx.author.id)
    view = FortuneRerollView(ctx.author, ctx.guild, result) if result.can_reroll else None
    embed = build_fortune_embed(ctx.author, ctx.guild, result)
    embed.add_field(name="签到", value=f"Klee做好了 3 个炸弹分给你啦！你现在有 {bombs} 个炸弹。", inline=False)
    await ctx.send(embed=embed, files=fortune_files(), view=view)


def fortune_files() -> list[discord.File]:
    return [
        discord.File(FORTUNE_SLIP_IMAGE, filename="fortune-slip.webp"),
        discord.File(KLEE_FOOTER_IMAGE, filename="klee-footer.jpg"),
    ]


def build_fortune_embed(
    user: discord.abc.User,
    guild: discord.Guild | None,
    draw: FortuneResult,
) -> discord.Embed:
    fortune = draw.fortune
    description = fortune_text(draw)
    embed = discord.Embed(
        title=f"{user.display_name}的幸运签",
        description=description,
        color=luck_color(draw.label),
    )
    embed.add_field(name="运势", value=luck_summary(draw), inline=False)
    if draw.used_second_chance and draw.first_fortune:
        embed.add_field(
            name="上一只签",
            value=f"~~{draw.first_label}（比平均值不走运了 {abs(draw.first_luck_delta or 0):.3f}%）~~",
            inline=False,
        )
    embed.set_thumbnail(url="attachment://fortune-slip.webp")
    embed.set_footer(text="爱一定来自西风骑士团禁闭室！", icon_url="attachment://klee-footer.jpg")
    return embed


class FortuneRerollView(discord.ui.View):
    def __init__(self, user: discord.abc.User, guild: discord.Guild | None, first_result: FortuneResult) -> None:
        super().__init__(timeout=120)
        self.user_id = user.id
        self.guild = guild
        self.first_result = first_result

    @discord.ui.button(label="再来一次！", style=discord.ButtonStyle.primary)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("这支签不是你的，不能替别人重抽。", ephemeral=True)
            return

        second = draw_second_fortune(
            first=self.first_result,
            user_id=interaction.user.id,
            guild_id=self.guild.id if self.guild else None,
        )
        profile_stats.record_fortune(self.guild.id if self.guild else None, interaction.user.id, second.label, second.luck_delta)
        embed = build_fortune_embed(interaction.user, self.guild, second)
        button.disabled = True
        button.label = "已重抽"
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="第二次机会？", style=discord.ButtonStyle.secondary)
    async def explain(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "当求签结果是不幸运的时候，不要气馁！\n"
            "Klee会有10% - 39.6%的概率给予你第二次机会 ~",
            ephemeral=True,
        )


class _ContextResponse:
    def __init__(self, ctx: commands.Context) -> None:
        self._ctx = ctx

    def is_done(self) -> bool:
        return True


class _ContextFollowup:
    def __init__(self, ctx: commands.Context) -> None:
        self._ctx = ctx

    async def send(self, message: str, **kwargs) -> None:
        await self._ctx.send(message, **kwargs)


class _ContextInteraction:
    def __init__(self, ctx: commands.Context) -> None:
        self.guild = ctx.guild
        self.user = ctx.author
        self.channel = getattr(ctx, 'channel', None)
        self.response = _ContextResponse(ctx)
        self.followup = _ContextFollowup(ctx)


async def run_bot():
    global autonomy, spam_guard, operations
    operations = Operations(bot,DATA_DIR)
    await bot.add_cog(operations)
    await bot.add_cog(PersonaControls(klee_ai.persona_modes))
    await bot.add_cog(ModelControls(model_selection))
    await bot.add_cog(MusicControls(bot, get_player, players, DATA_DIR))
    spam_guard = SpamGuard(bot, DATA_DIR)
    await bot.add_cog(spam_guard)
    await bot.add_cog(Moderation(bot))
    await bot.add_cog(BudgetControls(klee_ai.chat_model.budget))
    await bot.add_cog(MathCommands())
    await bot.add_cog(FileCommands(file_service, conversations))
    autonomy = GuildAgent(bot, klee_ai.chat_model, DATA_DIR, player_factory=get_player)
    await bot.add_cog(autonomy)
    await bot.add_cog(DiagnosticControls(bot,klee_ai.diagnostics,autonomy,spam_guard))
    async def close_client():
        try:
            await asyncio.wait_for(operations.shutdown(),timeout=8)
        except asyncio.TimeoutError:
            print('shutdown log delivery timed out',flush=True)
        finally:
            await bot.close()

    loop=asyncio.get_running_loop()
    handlers={}
    def stop_signal(*_):
        loop.call_soon_threadsafe(lambda: asyncio.create_task(close_client()))
    for name in ('SIGTERM','SIGBREAK'):
        if hasattr(signal,name):
            sig=getattr(signal,name)
            handlers[sig]=signal.signal(sig,stop_signal)
    try:
        await bot.start(TOKEN)
    finally:
        await close_client()
        for sig,handler in handlers.items():
            signal.signal(sig,handler)


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(run_bot())
