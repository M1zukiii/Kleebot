import asyncio
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from .fortune import FortuneResult, draw_daily_fortune, draw_second_fortune, fortune_message, fortune_text, luck_color, luck_summary
from .player import GuildPlayer
from .resolver import Resolver


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
resolver = Resolver()
players: dict[int, GuildPlayer] = {}
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
FORTUNE_SLIP_IMAGE = ASSETS_DIR / "fortune-slip.webp"
KLEE_FOOTER_IMAGE = ASSETS_DIR / "klee-footer.jpg"


def get_player(guild: discord.Guild) -> GuildPlayer:
    player = players.get(guild.id)
    if player is None:
        player = GuildPlayer(bot=bot, guild_id=guild.id, resolver=resolver)
        players[guild.id] = player
    return player


async def respond(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message)
    else:
        await interaction.response.send_message(message)


async def respond_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
    files: list[discord.File] | None = None,
    view: discord.ui.View | None = None,
) -> None:
    kwargs = {"embed": embed, "files": files or []}
    if view is not None:
        kwargs["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True)


@bot.event
async def on_ready() -> None:
    print(f"ready: {bot.user} in {len(bot.guilds)} guild(s)", flush=True)
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


async def clear_global_commands() -> None:
    if bot.application_id is None:
        print("global command cleanup skipped: missing application id", flush=True)
        return
    await bot.http.bulk_upsert_global_commands(bot.application_id, [])
    print("cleared global command(s)", flush=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    print(f"slash command error in {interaction.command}: {error}", flush=True)
    try:
        await respond(interaction, f"Command failed: {error}")
    except Exception as exc:
        print(f"failed to report slash error: {exc}", flush=True)


@bot.tree.command(name="play", description="Play a YouTube, Bilibili, NicoNico URL or search query.")
@app_commands.describe(query="URL or search text")
async def slash_play(interaction: discord.Interaction, query: str) -> None:
    await defer(interaction)
    try:
        if interaction.guild is None:
            raise RuntimeError("Use this in a server.")
        print(f"play requested in {interaction.guild.id}: {query}", flush=True)
        track = await get_player(interaction.guild).enqueue(interaction, query)
        await interaction.followup.send(f"Queued: [{track.title}]({track.webpage_url})")
    except Exception as exc:
        await interaction.followup.send(f"Could not play that: {exc}")


@bot.tree.command(name="skip", description="Skip the current track.")
async def slash_skip(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"skip requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild and get_player(interaction.guild).skip(interaction.guild):
        await respond(interaction, "Skipped.")
    else:
        await respond(interaction, "Nothing is playing.")


@bot.tree.command(name="stop", description="Stop playback and clear the queue.")
async def slash_stop(interaction: discord.Interaction) -> None:
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


@bot.tree.command(name="leave", description="Leave the voice channel.")
async def slash_leave(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"leave requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild and interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        players.pop(interaction.guild.id, None)
        await respond(interaction, "Left voice channel.")
    else:
        await respond(interaction, "I am not in a voice channel.")


@bot.tree.command(name="queue", description="Show the music queue.")
async def slash_queue(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"queue requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    if interaction.guild is None:
        await respond(interaction, "Use this in a server.")
        return
    await respond(interaction, get_player(interaction.guild).queue_text())


@bot.tree.command(name="pause", description="Pause playback.")
async def slash_pause(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"pause requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_playing():
        vc.pause()
        await respond(interaction, "Paused.")
    else:
        await respond(interaction, "Nothing is playing.")


@bot.tree.command(name="resume", description="Resume playback.")
async def slash_resume(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"resume requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_paused():
        vc.resume()
        await respond(interaction, "Resumed.")
    else:
        await respond(interaction, "Nothing is paused.")


@bot.tree.command(name="volume", description="Set playback volume from 1 to 100.")
async def slash_volume(interaction: discord.Interaction, percent: app_commands.Range[int, 1, 100]) -> None:
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


@bot.tree.command(name="fortune", description="Draw today's fortune.")
async def slash_fortune(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"fortune requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    result = draw_daily_fortune(user_id=interaction.user.id, guild_id=interaction.guild.id if interaction.guild else None)
    embed = build_fortune_embed(interaction.user, interaction.guild, result)
    view = FortuneRerollView(interaction.user, interaction.guild, result) if result.can_reroll else None
    await respond_embed(interaction, embed, fortune_files(), view)


@bot.tree.command(name="求签", description="抽取今日签文。")
async def slash_qiuqian_cn(interaction: discord.Interaction) -> None:
    await defer(interaction)
    print(f"求签 requested in {interaction.guild.id if interaction.guild else 'dm'}", flush=True)
    result = draw_daily_fortune(user_id=interaction.user.id, guild_id=interaction.guild.id if interaction.guild else None)
    embed = build_fortune_embed(interaction.user, interaction.guild, result)
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
        await message.edit(content=f"Queued: {track.title}\n{track.webpage_url}")
    except Exception as exc:
        await message.edit(content=f"Could not play that: {exc}")


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


@bot.command(name="leave")
async def prefix_leave(ctx: commands.Context) -> None:
    if ctx.guild and ctx.guild.voice_client:
        await ctx.guild.voice_client.disconnect()
        players.pop(ctx.guild.id, None)
        await ctx.send("Left voice channel.")
    else:
        await ctx.send("I am not in a voice channel.")


@bot.command(name="queue", aliases=["q"])
async def prefix_queue(ctx: commands.Context) -> None:
    if ctx.guild:
        await ctx.send(get_player(ctx.guild).queue_text())


@bot.command(name="help")
async def prefix_help(ctx: commands.Context) -> None:
    await ctx.send(
        "Commands: /play, /skip, /stop, /pause, /resume, /queue, /leave, /volume, /fortune, /求签\n"
        f"Text fallback: {PREFIX}play <url or search>, {PREFIX}skip, {PREFIX}stop, {PREFIX}queue, {PREFIX}leave"
    )


@bot.command(name="qiuqian", aliases=["求签", "抽签", "fortune", "luck"])
async def prefix_qiuqian(ctx: commands.Context) -> None:
    result = draw_daily_fortune(user_id=ctx.author.id, guild_id=ctx.guild.id if ctx.guild else None)
    view = FortuneRerollView(ctx.author, ctx.guild, result) if result.can_reroll else None
    await ctx.send(embed=build_fortune_embed(ctx.author, ctx.guild, result), files=fortune_files(), view=view)


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
        embed = build_fortune_embed(interaction.user, self.guild, second)
        button.disabled = True
        button.label = "已重抽"
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()


class _ContextResponse:
    def __init__(self, ctx: commands.Context) -> None:
        self._ctx = ctx

    def is_done(self) -> bool:
        return True


class _ContextFollowup:
    def __init__(self, ctx: commands.Context) -> None:
        self._ctx = ctx

    async def send(self, message: str) -> None:
        await self._ctx.send(message)


class _ContextInteraction:
    def __init__(self, ctx: commands.Context) -> None:
        self.guild = ctx.guild
        self.user = ctx.author
        self.response = _ContextResponse(ctx)
        self.followup = _ContextFollowup(ctx)


if __name__ == "__main__":
    Path("/app/data").mkdir(parents=True, exist_ok=True)
    asyncio.run(bot.start(TOKEN))
