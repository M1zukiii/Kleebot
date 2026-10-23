"""Bounded local spam detection and escalating timeouts without model calls."""
import asyncio
from collections import Counter, OrderedDict, deque
from contextlib import contextmanager
import hashlib
import logging
import sqlite3
import time

import discord
from discord import app_commands
from discord.ext import commands

from .guild_agent import owner_allowed
from .moderation import ModerationError, apply_timeout, can_moderate, check_members, publish_receipt

LEVELS = (1, 5, 15, 60)
RULE = '同一成员同一频道10秒内8条，或20秒内同样内容5条；24小时内禁言依次60秒、5分钟、15分钟、60分钟，上限60分钟。'


def fingerprint(message):
    content = ' '.join(message.content.split()).casefold()
    attachments = tuple((a.filename, a.size) for a in getattr(message, 'attachments', []))
    return hashlib.sha256(repr((content, attachments)).encode()).hexdigest()


def violation(rows):
    if not rows:
        return ''
    end = rows[-1]['stamp']
    if sum(end-10 <= row['stamp'] <= end for row in rows) >= 8:
        return '10秒内连续发送至少8条消息'
    counts = Counter(row['hash'] for row in rows if end-20 <= row['stamp'] <= end)
    return '20秒内重复发送同样内容至少5次' if counts and max(counts.values()) >= 5 else ''


class SpamStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS policy (guild TEXT PRIMARY KEY, owner TEXT, enabled INTEGER, version INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS penalties (message TEXT PRIMARY KEY, guild TEXT, member TEXT, minutes INTEGER, state TEXT, stamp REAL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def policy(self, guild):
        with self.connect() as db:
            row = db.execute('SELECT owner,enabled,version FROM policy WHERE guild=?', (str(guild),)).fetchone()
        return dict(owner=row[0], enabled=bool(row[1]), version=row[2]) if row else None

    def configure(self, guild, owner, enabled):
        with self.connect() as db:
            db.execute('INSERT INTO policy VALUES(?,?,?,1) ON CONFLICT(guild) DO UPDATE SET owner=excluded.owner,enabled=excluded.enabled,version=policy.version+1',
                       (str(guild), str(owner), int(enabled)))

    def strikes(self, guild, member):
        with self.connect() as db:
            return db.execute("SELECT minutes,stamp FROM penalties WHERE guild=? AND member=? AND state='applied' AND stamp>? ORDER BY stamp",
                              (str(guild), str(member), time.time()-86400)).fetchall()

    def reserve(self, message, minutes):
        with self.connect() as db:
            db.execute('DELETE FROM penalties WHERE stamp<?', (time.time()-7*86400,))
            cursor = db.execute('INSERT OR IGNORE INTO penalties VALUES(?,?,?,?,?,?)',
                (str(message.id), str(message.guild.id), str(message.author.id), minutes, 'pending', time.time()))
            return bool(cursor.rowcount)

    def finish(self, mid, state, stamp=None):
        with self.connect() as db:
            db.execute('UPDATE penalties SET state=?,stamp=? WHERE message=?', (state, time.time() if stamp is None else stamp, str(mid)))

    def interrupted(self, guild):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM penalties WHERE guild=? AND state IN ('pending','uncertain') LIMIT 1", (str(guild),)).fetchone())


class SpamGuard(commands.Cog):
    def __init__(self, bot, data_dir):
        self.bot = bot
        self.store = SpamStore(data_dir / 'spam_guard.sqlite3')
        self.buffers = OrderedDict()
        self.locks = {}

    def collect(self, message, version):
        key = (message.guild.id, message.channel.id, message.author.id, version)
        stamp = message.created_at.timestamp()
        if not 0 <= time.time()-stamp <= 30:
            return []
        rows = self.buffers.pop(key, deque(maxlen=32))
        if any(row['id'] == message.id for row in rows):
            self.buffers[key] = rows
            return []
        rows = deque((row for row in rows if stamp-20 <= row['stamp'] <= stamp), maxlen=32)
        rows.append({'id': message.id, 'stamp': stamp, 'hash': fingerprint(message)})
        self.buffers[key] = rows
        while len(self.buffers) > 2000:
            self.buffers.popitem(last=False)
        if violation(rows):
            del self.buffers[key]
            return list(rows)
        return []

    async def authority(self, guild, version):
        policy = await asyncio.to_thread(self.store.policy, guild.id)
        if not policy or not policy['enabled'] or policy['version'] != version:
            raise ModerationError('防刷屏已暂停或设置已改变。')
        owner = await guild.fetch_member(int(policy['owner']))
        bot_member = await guild.fetch_member(self.bot.user.id)
        if not owner_allowed(guild, owner) or not can_moderate(owner) or not can_moderate(bot_member):
            await asyncio.to_thread(self.store.configure, guild.id, policy['owner'], False)
            raise ModerationError('管理权限已失效，防刷屏已暂停。')
        return owner, bot_member

    async def enforce(self, message, policy, evidence):
        owner, bot_member = await self.authority(message.guild, policy['version'])
        target = await message.guild.fetch_member(message.author.id)
        check_members(message.guild, owner, target, bot_member)
        if target.is_timed_out():
            return False
        for member in (owner, bot_member):
            perms = message.channel.permissions_for(member)
            if not (perms.view_channel and perms.read_message_history):
                raise ModerationError('无法核对刷屏来源。')
        strikes = await asyncio.to_thread(self.store.strikes, message.guild.id, target.id)
        verified = []
        for row in evidence:
            actual = await message.channel.fetch_message(row['id'])
            if (actual.author.id != target.id or actual.channel.id != message.channel.id
                    or actual.guild.id != message.guild.id or fingerprint(actual) != row['hash']
                    or actual.created_at.timestamp() != row['stamp']
                    or not 0 <= time.time()-row['stamp'] <= 60):
                raise ModerationError('刷屏依据已变化，本轮不处理。')
            if strikes and row['stamp'] <= strikes[-1][1]:
                raise ModerationError('不重复处罚上次禁言前的消息。')
            verified.append(row)
        if not verified or verified[-1]['id'] != message.id or len({r['id'] for r in verified}) != len(verified):
            raise ModerationError('刷屏依据不完整。')
        reason = violation(verified)
        if not reason:
            raise ModerationError('没有达到刷屏阈值。')
        minutes = LEVELS[min(len(strikes), len(LEVELS)-1)]
        if not await asyncio.to_thread(self.store.reserve, message, minutes):
            return False
        try:
            async def guard():
                await self.authority(message.guild, policy['version'])
            receipt = await apply_timeout(message.guild, owner, target, bot_member, message.channel, minutes,
                f'刷屏：{reason}；24小时内第{len(strikes)+1}次，首次60秒作为警告。', before_apply=guard, audit_ref=message.id)
        except Exception as exc:
            known = isinstance(exc, (ModerationError, discord.Forbidden, discord.NotFound))
            await asyncio.to_thread(self.store.finish, message.id, 'rejected' if known else 'uncertain')
            if not known:
                await asyncio.to_thread(self.store.configure, message.guild.id, policy['owner'], False)
            raise
        await asyncio.to_thread(self.store.finish, message.id, 'applied')
        receipt.duration_label = '60秒' if minutes == 1 else f'{minutes}分钟'
        receipt.automated = True
        try:
            await publish_receipt(message.channel, bot_member, receipt)
        except discord.HTTPException:
            logging.getLogger(__name__).exception('Spam timeout applied but announcement failed: guild=%s message=%s', message.guild.id, message.id)
        return True

    async def observe(self, message):
        if message.guild is None or message.author.bot:
            return False
        if message.author.id == message.guild.owner_id or message.author.guild_permissions.administrator:
            return False
        policy = await asyncio.to_thread(self.store.policy, message.guild.id)
        if not policy or not policy['enabled']:
            return False
        # Keep IDs, timestamps and hashes only. No ordinary message is sent to a model.
        async with self.locks.setdefault(message.guild.id, asyncio.Lock()):
            if await asyncio.to_thread(self.store.interrupted, message.guild.id):
                await asyncio.to_thread(self.store.configure, message.guild.id, policy['owner'], False)
                return False
            evidence = self.collect(message, policy['version'])
            if not evidence:
                return False
            try:
                return await self.enforce(message, policy, evidence)
            except Exception:
                logging.getLogger(__name__).exception('Spam enforcement failed: guild=%s message=%s', message.guild.id, message.id)
                return False

    async def configure(self, guild, actor, enabled):
        if guild is None:
            raise ModerationError('请在服务器内设置。')
        actor = await guild.fetch_member(actor.id)
        bot_member = await guild.fetch_member(self.bot.user.id)
        if not owner_allowed(guild, actor) or not can_moderate(actor) or not can_moderate(bot_member):
            raise ModerationError('需要服务器所有者或配置的主人，且本人和Klee都有禁言权限。')
        if enabled and await asyncio.to_thread(self.store.interrupted, guild.id):
            raise ModerationError('存在结果未确认的刷屏禁言，请先检查本机日志和Discord实际状态。')
        await asyncio.to_thread(self.store.configure, guild.id, actor.id, enabled)
        return ('防刷屏已开启，覆盖普通群聊。' if enabled else '防刷屏已关闭，现有禁言按时解除。')+'\n'+RULE

    @commands.hybrid_command(name='防刷屏设置', description='开启或关闭普通群聊防刷屏，首次禁言60秒，再犯逐级增加。')
    @commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(enabled='开启')
    async def settings(self, ctx: commands.Context, enabled: bool):
        await ctx.defer(ephemeral=True)
        try:
            text = await self.configure(ctx.guild, ctx.author, enabled)
        except ModerationError as exc:
            text = str(exc)
        await ctx.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_command(name='防刷屏状态', description='查看防刷屏开关、阈值和递增禁言时长。')
    @commands.guild_only()
    async def status(self, ctx: commands.Context):
        policy = await asyncio.to_thread(self.store.policy, ctx.guild.id)
        await ctx.send(('防刷屏：已开启' if policy and policy['enabled'] else '防刷屏：已关闭')+'\n'+RULE,
                       ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
