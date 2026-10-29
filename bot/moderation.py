"""Manual member timeouts with real permission checks and public receipts."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import time

import discord
from discord import app_commands
from discord.ext import commands


class ModerationError(ValueError):
    pass


def invalid_timeout_timestamp(error):
    return (isinstance(error, discord.HTTPException) and error.status == 400
            and error.code == 50035 and 'communication_disabled_until' in error.text
            and 'invalid communication disabled timestamp' in error.text.lower())


def timeout_deadline(now, minutes):
    # Discord enforces 28 days against its own clock; avoid the exact boundary.
    duration = timedelta(minutes=minutes)
    if minutes == 40320:
        duration -= timedelta(seconds=5)
    return now + duration


def rejected_timeout_clock(error):
    """Read Discord's own rejection response, without another network request."""
    headers = getattr(error.response, 'headers', {})
    value = headers.get('Date')
    try:
        now = parsedate_to_datetime(value)
        if now.tzinfo is None:
            raise ValueError('Missing timezone')
        return now.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ModerationError('Discord 拒绝了禁言截止时间，且无法取得服务器时间。请在运行 Klee 的电脑开启自动设置时间并立即同步，然后重试。') from error


def can_moderate(member):
    return member.guild_permissions.moderate_members or member.guild_permissions.administrator


def check_members(guild, actor, target, bot_member):
    if not can_moderate(actor):
        raise ModerationError('你需要服务器的“禁言成员 / Timeout Members”权限。')
    if bot_member is None or not can_moderate(bot_member):
        raise ModerationError('Klee 缺少“禁言成员 / Timeout Members”权限。')
    if target.guild.id != guild.id or actor.guild.id != guild.id:
        raise ModerationError('只能操作当前服务器的成员。')
    if target.id == actor.id:
        raise ModerationError('不能用此指令禁言自己。')
    if target.bot or target.id == guild.owner_id or target.guild_permissions.administrator:
        raise ModerationError('不能禁言机器人、服务器所有者或管理员。')
    if actor.id != guild.owner_id and actor.top_role <= target.top_role:
        raise ModerationError('你只能管理角色层级低于自己的成员。')
    if bot_member.top_role <= target.top_role:
        raise ModerationError('请把 Klee 的角色放到被管理成员的角色上方。')


def duration_text(minutes):
    days, remainder = divmod(minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    return ''.join(f'{count}{unit}' for count, unit in ((days, '天'), (hours, '小时'), (minutes, '分钟')) if count)


@dataclass
class ModerationReceipt:
    target_id: int
    actor_id: int
    minutes: int | None
    reason: str
    until: datetime | None
    duration_label: str | None = None
    automated: bool = False

    def embed(self):
        embed = discord.Embed(title='🔇 禁言公告' if self.minutes is not None else '🔊 解除禁言公告',
                              color=0xE7A33E if self.minutes is not None else 0x4AAE83,
                              timestamp=datetime.now(timezone.utc))
        embed.add_field(name='成员', value=f'<@{self.target_id}> (`{self.target_id}`)', inline=False)
        embed.add_field(name='执行者', value=f'<@{self.actor_id}>', inline=False)
        if self.automated:
            embed.title = '🤖 自治禁言公告'
            embed.set_field_at(1, name='执行者', value=f'Klee Agent（授权者：<@{self.actor_id}>）', inline=False)
        if self.minutes is not None:
            embed.add_field(name='禁言时长', value=self.duration_label or duration_text(self.minutes))
            embed.add_field(name='解除时间', value=f'<t:{int(self.until.timestamp())}:F>（<t:{int(self.until.timestamp())}:R>）', inline=False)
        else:
            embed.add_field(name='状态', value='已提前解除禁言')
        embed.add_field(name='原因', value=self.reason, inline=False)
        embed.set_footer(text='Klee 群管理 · 操作已执行，Discord 审核日志保留操作记录')
        return embed

    def text(self):
        state = f'禁言 {self.duration_label or duration_text(self.minutes)}，解除时间 <t:{int(self.until.timestamp())}:F>' if self.minutes is not None else '已解除禁言'
        actor = f'Klee Agent（授权者：<@{self.actor_id}>）' if self.automated else f'<@{self.actor_id}>'
        return f'【Klee 管理公告】\n成员：<@{self.target_id}>\n执行者：{actor}\n{state}\n原因：{self.reason}'


async def apply_timeout(guild, actor, target, bot_member, channel, minutes, reason, *, before_apply=None, audit_ref=None):
    if guild is None:
        raise ModerationError('请在服务器内使用此指令。')
    if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 40320):
        raise ModerationError('禁言时长应为 1–40320 分钟（最长 28 天）。')
    reason = reason.strip()
    if not 1 <= len(reason) <= 400:
        raise ModerationError('请填写 1–400 字的原因，这段原因会公开显示。')
    check_members(guild, actor, target, bot_member)
    async def preflight():
        nonlocal actor, target, bot_member
        # Recheck permissions at each attempt, including after a rejected write.
        actor = await guild.fetch_member(actor.id)
        target = await guild.fetch_member(target.id)
        bot_member = await guild.fetch_member(bot_member.id)
        check_members(guild, actor, target, bot_member)
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.send_messages:
            raise ModerationError('Klee 需要此频道的查看和发送消息权限，才能公开发布禁言公告。')
        if isinstance(channel, discord.Thread) and not permissions.send_messages_in_threads:
            raise ModerationError('Klee 缺少在线程中发送公告的权限，请到文字频道执行。')

    await preflight()
    if minutes is None and not target.is_timed_out():
        raise ModerationError('这位成员当前没有被禁言。')
    audit_reason = f'Klee moderation authorized by {actor.id}: {reason}'
    if audit_ref is not None:
        audit_reason += f' ref={audit_ref}'
    if before_apply is not None:
        await before_apply()
    until = timeout_deadline(datetime.now(timezone.utc), minutes) if minutes is not None else None
    try:
        await target.timeout(until, reason=audit_reason)
    except discord.HTTPException as error:
        # Only this explicit validation rejection confirms that no write occurred.
        # Never retry a timeout, 5xx, permission error or an uncertain result.
        if minutes is None or not invalid_timeout_timestamp(error):
            raise
        received = time.monotonic()
        server_now = rejected_timeout_clock(error)
        await preflight()
        if before_apply is not None:
            await before_apply()
        until = timeout_deadline(server_now + timedelta(seconds=time.monotonic()-received), minutes)
        try:
            await target.timeout(until, reason=audit_reason)
        except discord.HTTPException as retry_error:
            if invalid_timeout_timestamp(retry_error):
                raise ModerationError('Discord 再次拒绝了禁言截止时间，已停止重试。请同步运行 Klee 的电脑时间后再试。') from retry_error
            raise
    return ModerationReceipt(target.id, actor.id, minutes, reason, until)


async def publish_receipt(channel, bot_member, receipt):
    mentions = discord.AllowedMentions.none()
    if channel.permissions_for(bot_member).embed_links:
        try:
            await channel.send(embed=receipt.embed(), allowed_mentions=mentions)
            return
        except discord.HTTPException:
            # Timeout has already succeeded: retry the receipt as plain text.
            pass
    await channel.send(receipt.text(), allowed_mentions=mentions)


def friendly_error(error):
    if isinstance(error, ModerationError):
        return str(error)
    if isinstance(error, discord.Forbidden):
        return 'Discord 拒绝了禁言操作，请检查双方权限、Klee 的角色层级，以及服务器的管理安全设置。'
    if isinstance(error, discord.NotFound):
        return '找不到这位成员，请确认对方仍在服务器内。'
    return f'禁言请求未确认成功（{type(error).__name__}），请检查成员状态后再试。'


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def execute(self, guild, actor, target, channel, minutes, reason):
        receipt = await apply_timeout(guild, actor, target, guild.me if guild else None, channel, minutes, reason)
        try:
            await publish_receipt(channel, guild.me, receipt)
        except discord.HTTPException:
            # Do not claim an applied timeout failed just because its receipt did.
            print(f'moderation receipt failed: guild={guild.id} target={target.id} actor={actor.id}', flush=True)
            return '成员状态已修改成功，但公开公告发送失败，请检查频道权限。'
        return '已执行，公开公告已发布。'

    async def slash(self, interaction, member, minutes, reason):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.execute(interaction.guild, interaction.user, member, interaction.channel, minutes, reason)
        except (ModerationError, discord.HTTPException) as exc:
            result = friendly_error(exc)
        await interaction.followup.send(result, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='禁言', description='手动禁言成员，并公开发布时长和原因。')
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.rename(member='成员', minutes='分钟', reason='原因')
    @app_commands.describe(member='选择要禁言的成员', minutes='禁言分钟数：1–40320，最长 28 天', reason='禁言原因，必填，将公开显示；不会触发提及提醒')
    async def timeout_slash(self, interaction: discord.Interaction, member: discord.Member,
                            minutes: app_commands.Range[int, 1, 40320], reason: str):
        await self.slash(interaction, member, minutes, reason)

    @app_commands.command(name='解除禁言', description='提前解除成员禁言，并公开发布原因。')
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.rename(member='成员', reason='原因')
    @app_commands.describe(member='选择要提前解除禁言的成员', reason='解除禁言的原因，必填，将公开显示')
    async def untimeout_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        await self.slash(interaction, member, None, reason)

    async def prefix(self, ctx, member, minutes, reason):
        try:
            result = await self.execute(ctx.guild, ctx.author, member, ctx.channel, minutes, reason)
        except (ModerationError, discord.HTTPException) as exc:
            result = friendly_error(exc)
        # The public receipt already contains all details on success.
        if result != '已执行，公开公告已发布。':
            await ctx.send(result, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name='timeout', aliases=['禁言'])
    @commands.guild_only()
    async def timeout_prefix(self, ctx: commands.Context, member: discord.Member, minutes: int, *, reason: str):
        await self.prefix(ctx, member, minutes, reason)

    @commands.command(name='untimeout', aliases=['解除禁言'])
    @commands.guild_only()
    async def untimeout_prefix(self, ctx: commands.Context, member: discord.Member, *, reason: str):
        await self.prefix(ctx, member, None, reason)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            text = '请在服务器内使用禁言指令。'
        elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            text = '用法：!禁言 @成员 分钟 原因；或 !解除禁言 @成员 原因。分钟填数字，原因必填。'
        else:
            text = friendly_error(getattr(error, 'original', error))
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())
