"""Mention classification and rule execution; staff tasks use a separate agent."""
import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from .agent_store import AgentStore
from .maintenance import is_owner
from .moderation import ModerationError, apply_timeout, can_moderate, check_members, publish_receipt
from .mention_router import MentionRouter, is_staff
from .management_agent import ManagementAgent


DEFAULT_GOAL = '只检查真正 @Klee 的发言，识别明确定向辱骂（包括对Klee的辱骂）、骚扰、仇恨攻击和刷屏。普通聊天、玩笑和不同意见不处罚。先警告，三次警告后仍继续违规才禁言60秒。'


def owner_allowed(guild, member):
    trusted = member.id == guild.owner_id or is_owner(member.id)
    return trusted and (member.guild_permissions.manage_guild or member.guild_permissions.administrator)


class GuildAgent(commands.Cog):
    def __init__(self, bot, chat_model, data_dir, player_factory=None):
        self.bot = bot
        self.chat = chat_model
        self.store = AgentStore(data_dir / 'guild_agent.sqlite3')
        self.router = MentionRouter(chat_model)
        self.management = ManagementAgent(bot, chat_model, self.store, player_factory)
        self.locks = {}
        self.failures = {}

    async def observe(self, message):
        if message.guild is None or message.author.bot or self.bot.user not in message.mentions:
            return False
        if not self.chat.enabled:
            return False
        goal = await asyncio.to_thread(self.store.goal, message.guild.id)
        staff = is_staff(message.author)
        if (not goal or not goal['enabled']) and not staff:
            return False
        # Commands are control messages, not moderation evidence.
        if message.content.startswith(str(self.bot.command_prefix)):
            return False
        try:
            route = await self.router.classify(message.content, staff, goal=goal['goal'] if goal and goal['enabled'] else '')
            if route['route'] == 'agent' and staff:
                try:
                    return await self.management.run(message)
                except Exception as exc:
                    await message.reply(f'管理任务中断（{type(exc).__name__}），请先检查实际状态；不会声称操作已完成。',
                                        allowed_mentions=discord.AllowedMentions.none())
                    return True
            if route['route'] != 'warn' or not goal or not goal['enabled']:
                return False
            lock = self.locks.setdefault(message.guild.id, asyncio.Lock())
            async with lock:
                await asyncio.to_thread(self.store.observe, message.guild.id, message.channel.id,
                                        message.id, message.author.id, message.content,
                                        message.created_at.timestamp())
                warnings = await asyncio.to_thread(self.store.warnings, message.guild.id, message.author.id)
                protected = message.author.id == message.guild.owner_id or message.author.guild_permissions.administrator
                action = 'timeout_member' if len(warnings) >= 3 and not protected else 'warn_member'
                args = {'member_id': str(message.author.id), 'channel_id': str(message.channel.id),
                        'message_id': str(message.id), 'reason': route['reason']}
                result = await self.execute(message.guild, goal['version'], action, args,
                                            {str(message.id): {'content': message.content[:700]}})
                return bool(result.get('ok'))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            gid = message.guild.id
            self.failures[gid] = self.failures.get(gid, 0) + 1
            await asyncio.to_thread(self.store.action, gid, 'cycle', {'error': type(exc).__name__}, 'failed')
            print(f'mention moderation failed: guild={gid} type={type(exc).__name__}', flush=True)
            if self.failures[gid] >= 3:
                await asyncio.to_thread(self.store.pause, gid)
            return False

    async def current(self, guild, version):
        goal = await asyncio.to_thread(self.store.goal, guild.id)
        if not goal or not goal['enabled'] or goal['version'] != version:
            raise ModerationError('自治已暂停或目标已修改，本次操作取消。')
        owner = await guild.fetch_member(int(goal['owner']))
        if not owner_allowed(guild, owner) or not can_moderate(owner):
            await asyncio.to_thread(self.store.pause, guild.id)
            raise ModerationError('授权者已失去服务器管理权限，自治已暂停。')
        return goal, owner

    async def execute(self, guild, version, name, arguments, evidence_ids):
        if name not in {'warn_member', 'timeout_member'}:
            raise ModerationError('未知工具。')
        expected = {'member_id', 'channel_id', 'message_id', 'reason'}
        if not isinstance(arguments, dict) or set(arguments) != expected:
            raise ModerationError('工具参数不完整。')
        if any(not isinstance(arguments[key], str) or not arguments[key].isdecimal() or not 1 <= len(arguments[key]) <= 22
               for key in ('member_id', 'channel_id', 'message_id')):
            raise ModerationError('成员、频道和消息必须使用真实 ID。')
        reason = arguments['reason'].strip()
        if not 1 <= len(reason) <= 300:
            raise ModerationError('需要简短明确的违规原因。')
        if arguments['message_id'] not in evidence_ids:
            raise ModerationError('只能使用本轮实际观察到的消息作为依据。')
        goal, owner = await self.current(guild, version)
        channel = self.bot.get_channel(int(arguments['channel_id']))
        if channel is None or channel.guild.id != guild.id:
            raise ModerationError('频道不在当前服务器。')
        for member in (owner, guild.me):
            perms = channel.permissions_for(member)
            if not (perms.view_channel and perms.read_message_history):
                raise ModerationError('无法读取这条消息的来源频道。')
        if not channel.permissions_for(guild.me).send_messages:
            raise ModerationError('无法在来源频道公开发布公告。')
        message = await channel.fetch_message(int(arguments['message_id']))
        if self.bot.user not in message.mentions:
            raise ModerationError('只有真正 @Klee 的消息才能触发检查。')
        if isinstance(evidence_ids, dict) and message.content[:700] != evidence_ids[arguments['message_id']]['content']:
            raise ModerationError('消息内容已修改，本轮不再处理这条证据。')
        if str(message.author.id) != arguments['member_id'] or message.author.bot:
            raise ModerationError('消息作者与目标成员不符。')
        if not 0 <= time.time() - message.created_at.timestamp() <= 600:
            raise ModerationError('只处理十分钟以内的新违规消息。')
        target = await guild.fetch_member(message.author.id)
        bot_member = await guild.fetch_member(guild.me.id)
        if name == 'timeout_member':
            check_members(guild, owner, target, bot_member)
        warnings = await asyncio.to_thread(self.store.warnings, guild.id, target.id)
        if warnings and message.created_at.timestamp() <= warnings[-1]['stamp']:
            raise ModerationError('这条消息发生在上次警告之前，不能作为继续违规的依据。')
        if target.is_timed_out():
            raise ModerationError('成员已在禁言中，不重复处罚。')
        if name == 'timeout_member' and len(warnings) < 3:
            raise ModerationError('还没有三次有效警告，不能自主禁言。')
        await self.current(guild, version)
        action_id = await asyncio.to_thread(self.store.action, guild.id, name, arguments, 'pending')
        try:
            if name == 'warn_member':
                count = len(warnings) + 1
                link = f'https://discord.com/channels/{guild.id}/{channel.id}/{message.id}'
                await channel.send(f'⚠️ Klee 管理提醒（第 {count} 次）\n成员：<@{target.id}>\n原因：{reason}\n'
                                   f'请停止上述行为。累计三次警告后仍继续违规，将禁言 60 秒。\n依据：{link}',
                                   allowed_mentions=discord.AllowedMentions.none())
                await asyncio.to_thread(self.store.add_warning, guild.id, target.id, channel.id, message.id)
                result = {'ok': True, 'warning_count': count, 'member_id': str(target.id)}
            else:
                async def guard():
                    await self.current(guild, version)
                receipt = await apply_timeout(guild, owner, target, bot_member, channel, 1,
                                              '自治：三次警告后仍继续违规；' + reason, before_apply=guard)
                receipt.duration_label = '60秒'
                receipt.automated = True
                await asyncio.to_thread(self.store.consume, guild.id, target.id)
                await asyncio.to_thread(self.store.finish, action_id, 'applied')
                try:
                    await publish_receipt(channel, bot_member, receipt)
                    announced = True
                except discord.HTTPException:
                    announced = False
                result = {'ok': True, 'timeout_seconds': 60, 'member_id': str(target.id), 'announced': announced}
            await asyncio.to_thread(self.store.finish, action_id, 'success')
            return result
        except Exception as exc:
            await asyncio.to_thread(self.store.finish, action_id, 'failed_or_unconfirmed')
            if not isinstance(exc, (ModerationError, discord.Forbidden, discord.NotFound)):
                # A lost response might follow an applied effect. Do not
                # automatically repeat uncertain writes in another cycle.
                await asyncio.to_thread(self.store.pause, guild.id)
            raise

    async def control(self, guild, actor, channel, operation, goal=''):
        if guild is None or not owner_allowed(guild, actor):
            return '只有配置的主人或服务器所有者，且拥有管理服务器权限，才能设置自治。'
        if operation == 'pause':
            await asyncio.to_thread(self.store.pause, guild.id)
            return '自动警告已暂停；管理者明确交代的任务和手动指令仍可用。现有禁言仍由 Discord 按时解除。'
        if operation == 'start':
            if not self.chat.enabled:
                return '请先配置聊天 API Key，agent 会沿用当前聊天模型。'
            if not 1 <= len(goal.strip()) <= 1500:
                return '自治目标应为 1–1500 字。'
            # Goal changes are permissions changes: refresh the human first.
            actor = await guild.fetch_member(actor.id)
            if not owner_allowed(guild, actor):
                return '你当前没有管理服务器权限。'
            if not can_moderate(actor) or not can_moderate(guild.me):
                return '开启前，你和 Klee 都需要禁言成员权限。'
            await asyncio.to_thread(self.store.set_goal, guild.id, actor.id, channel.id, goal.strip())
            self.failures[guild.id] = 0
            return '提及检查已开启：只判断真正 @Klee 的发言，不观察其他群聊。三次有效警告后仍继续违规，禁言 60 秒。所有公告关闭提及提醒。'
        saved = await asyncio.to_thread(self.store.goal, guild.id)
        if not saved:
            return '尚未开启自治。使用 /自治开启 设置目标。'
        recent = await asyncio.to_thread(self.store.recent, guild.id)
        state = '提及检查已开启（无后台观察）' if saved['enabled'] else '已暂停'
        tools = {'cycle': '发言检查', 'warn_member': '警告', 'timeout_member': '禁言',
                 'manager_inspect_server': '读取服务器', 'manager_create_channel': '创建频道',
                 'manager_edit_channel': '调整频道', 'manager_delete_empty_channel': '删除空频道',
                 'manager_set_welcome_channel': '设置欢迎频道', 'manager_create_event': '创建活动',
                 'manager_timeout_member': '管理禁言', 'manager_untimeout_member': '管理解除禁言',
                 'manager_play_music': '放歌', 'manager_stop_music': '停止放歌'}
        states = {'complete': '完成', 'success': '成功', 'rejected': '未执行', 'failed': '失败',
                  'applied': '已执行', 'pending': '执行中', 'failed_or_unconfirmed': '结果未确认', 'step_limit': '本轮已结束'}
        entries = [f"{tools.get(item['tool'], '管理动作')}：{states.get(item['state'], '已记录')}" for item in recent]
        return f"自治：{state}\n目标：{saved['goal']}\n规则：三次警告后继续违规，禁言 60 秒。\n近期执行：" + ('；'.join(entries) or '暂无')

    @app_commands.command(name='自治开启', description='开启被 @Klee 时的发言检查，不观察其他群聊。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(goal='长期目标：先警告，三次警告后仍继续违规才禁言60秒')
    @app_commands.rename(goal='目标')
    async def start_slash(self, interaction: discord.Interaction, goal: str = DEFAULT_GOAL):
        await interaction.response.defer(ephemeral=True)
        text = await self.control(interaction.guild, interaction.user, interaction.channel, 'start', goal)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='自治暂停', description='暂停被 @Klee 时的自动警告和禁言。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def pause_slash(self, interaction: discord.Interaction):
        text = await self.control(interaction.guild, interaction.user, interaction.channel, 'pause')
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='自治状态', description='查看自治目标、运行状态和近期执行结果。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def status_slash(self, interaction: discord.Interaction):
        text = await self.control(interaction.guild, interaction.user, interaction.channel, 'status')
        await interaction.response.send_message(text[:1900], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name='自治开启')
    @commands.guild_only()
    async def start_prefix(self, ctx: commands.Context, *, goal: str = DEFAULT_GOAL):
        await ctx.send(await self.control(ctx.guild, ctx.author, ctx.channel, 'start', goal), allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name='自治暂停')
    @commands.guild_only()
    async def pause_prefix(self, ctx: commands.Context):
        await ctx.send(await self.control(ctx.guild, ctx.author, ctx.channel, 'pause'), allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name='自治状态')
    @commands.guild_only()
    async def status_prefix(self, ctx: commands.Context):
        await ctx.send((await self.control(ctx.guild, ctx.author, ctx.channel, 'status'))[:1900], allowed_mentions=discord.AllowedMentions.none())
