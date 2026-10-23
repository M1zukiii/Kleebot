"""Private diagnostics and audit-log based reconciliation; never replay writes."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import re

import discord
from discord import app_commands
from discord.ext import commands

from .diagnostics import scrub
from .guild_agent import owner_allowed
from .moderation import ModerationError


class DiagnosticControls(commands.Cog):
    def __init__(self,bot,diagnostics,agent,spam):
        self.bot,self.diagnostics,self.agent,self.spam = bot,diagnostics,agent,spam

    async def authorize(self,guild,user,audit=False):
        if guild is None:
            raise ModerationError('请在服务器内使用诊断。')
        actor = await guild.fetch_member(user.id)
        permissions = actor.guild_permissions
        if not (actor.id == guild.owner_id or permissions.administrator or permissions.manage_guild):
            raise ModerationError('只有服务器管理者可以查看诊断。')
        if audit:
            bot_member = await guild.fetch_member(self.bot.user.id)
            if any(not (getattr(m.guild_permissions,'view_audit_log',False) or m.guild_permissions.administrator) for m in (actor,bot_member)):
                raise ModerationError('核对操作需要你和Klee都有查看审核日志权限。')
        return actor

    def visible(self,guild,actor,row):
        if actor.id == guild.owner_id or actor.guild_permissions.administrator:
            return True
        channel_id = str(row.get('channel') or '')
        channel = self.bot.get_channel(int(channel_id)) if channel_id.isdigit() else None
        return channel is not None and channel.guild.id == guild.id and channel.permissions_for(actor).view_channel

    async def describe(self,guild,user,incident=None):
        actor = await self.authorize(guild,user)
        rows = await asyncio.to_thread(self.diagnostics.recent,guild.id,incident)
        rows = [row for row in rows if self.visible(guild,actor,row)]
        lines = [f"{r['id']} · {r['category']} · {r['kind']}\n{scrub(r['detail'])[:700]}" for r in rows]
        recent = await asyncio.to_thread(self.agent.store.recent,guild.id)
        if not incident:
            lines += [f"操作 {r['id']} · {r['tool']} · {r['state']}" for r in recent
                      if self.visible(guild,actor,json.loads(r['detail']))]
            lines += await asyncio.to_thread(self.uncertain_penalties,guild.id)
        return '\n\n'.join(lines)[:1800] or '当前服务器没有可查看的诊断记录。'

    def uncertain_penalties(self,guild):
        lines = []
        for kind,store,table in [('刷屏',self.spam.store,'penalties'),('骚扰',self.agent.store,'mention_penalties'),('超量请求',self.agent.store,'token_penalties')]:
            with store.connect() as db:
                rows = db.execute(f"SELECT message,state FROM {table} WHERE guild=? AND state IN ('pending','uncertain') ORDER BY stamp DESC LIMIT 3",(str(guild),)).fetchall()
            lines.extend(f'{kind} · 消息编号 {r[0]} · {r[1]}' for r in rows)
        return lines

    async def reconcile(self,guild,user,kind,number,resume=False):
        actor = await self.authorize(guild,user,audit=True)
        if not str(number).isdecimal():
            raise ModerationError('编号需要是操作编号或对应的消息ID。')
        if resume and not owner_allowed(guild,actor):
            raise ModerationError('恢复自动检查仍需要服务器所有者或配置的主人。')
        journal = kind == '管理'
        table = {'骚扰':'mention_penalties','超量请求':'token_penalties','刷屏':'penalties','管理':'agent_actions'}.get(kind)
        if table is None:
            raise ModerationError('未知操作类型。')
        store = self.spam.store if kind == '刷屏' else self.agent.store
        with store.connect() as db:
            if kind == '刷屏':
                db.row_factory = __import__('sqlite3').Row
            row = db.execute(f'SELECT * FROM {table} WHERE {"id" if journal else "message"}=? AND guild=?',(str(number),str(guild.id))).fetchone()
        if row is None:
            raise ModerationError('没有找到当前服务器的这条操作记录。')
        row = dict(row)
        if row['state'] in {'success','applied'}:
            return '这条操作已经确认，不会重复执行。'
        if row['state'] not in {'pending','uncertain','failed_or_unconfirmed'}:
            return '这条记录不是结果未确认的操作，不需要恢复。'
        details = json.loads(row['detail']) if journal else {}
        if journal and not self.visible(guild,actor,details):
            raise ModerationError('你无法查看这项操作所在的频道。')
        mid = details.get('message') if journal else row['message']
        if not mid:
            return '旧记录没有唯一追踪标识，不能自动确认；请人工核对，不会重试。'
        args = details.get('arguments',{})
        tool = row.get('tool','')
        expected = {'manager_create_channel':'channel_create','manager_edit_channel':'channel_update','manager_delete_empty_channel':'channel_delete',
                    'manager_set_welcome_channel':'guild_update','manager_create_event':'scheduled_event_create',
                    'manager_timeout_member':'member_update','manager_untimeout_member':'member_update'}.get(tool,'member_update' if not journal else None)
        if expected is None:
            return '播放等动作没有可唯一对应的Discord审核记录，不能据此自动确认；不会重试。'
        target = (args.get('member_id') or args.get('channel_id')) if journal else row['member']
        if expected == 'guild_update':
            target = guild.id
        matches = []
        after = datetime.fromtimestamp(row['stamp'],timezone.utc)-timedelta(seconds=10)
        async for entry in guild.audit_logs(limit=100,action=getattr(discord.AuditLogAction,expected),after=after):
            if getattr(entry.user,'id',None) != self.bot.user.id:
                continue
            reason = entry.reason or ''
            if not (re.search(r' ref='+re.escape(str(mid))+r'\Z',reason) or
                    re.fullmatch(r'Klee manager task by \d+, request '+re.escape(str(mid)),reason)):
                continue
            if target and str(getattr(entry.target,'id',None)) != str(target):
                continue
            if args.get('name') and getattr(entry.target,'name',None) != args['name']:
                continue
            if expected == 'member_update':
                until = getattr(entry.after,'timed_out_until','missing')
                if until == 'missing' or (tool == 'manager_untimeout_member' and until is not None):
                    continue
                if tool != 'manager_untimeout_member' and until is None:
                    continue
                minutes = args.get('minutes') if journal else row['minutes']
                if until is not None and minutes is not None and abs((until-entry.created_at).total_seconds()-int(minutes)*60) > 20:
                    continue
            matches.append(entry)
        if len(matches) != 1:
            return '没有找到唯一匹配的审核记录，仍保留结果未确认状态；不会自动重复操作。'
        entry = matches[0]
        if journal:
            await asyncio.to_thread(self.agent.store.finish,int(number),'success')
            if details.get('actor') and details.get('channel'):
                task = await asyncio.to_thread(self.agent.store.manager_task,guild.id,details['actor'],details['channel'])
                if task:
                    task.update(state='closed',reply='已通过Discord审核日志确认其中一项操作执行；其他未确认项仍需逐项核对，继续处理请重新交代任务。')
                    await asyncio.to_thread(self.agent.store.save_manager_task,guild.id,details['actor'],details['channel'],task)
        elif kind == '刷屏':
            await asyncio.to_thread(store.finish,number,'applied',entry.created_at.timestamp())
        else:
            await asyncio.to_thread(store.finish_mention,number,'applied','token' if kind == '超量请求' else 'mention',entry.created_at.timestamp())
        text = '已通过Discord审核日志确认这项操作执行过；没有重新执行。'
        if resume:
            if kind == '刷屏':
                text += '\n'+await self.spam.configure(guild,actor,True)
            else:
                goal = await asyncio.to_thread(self.agent.store.goal,guild.id)
                if goal:
                    # Preserve the original announcement channel when restoring policy.
                    channel = self.bot.get_channel(int(goal['channel']))
                    if channel:
                        text += '\n'+await self.agent.control(guild,actor,channel,'start',goal['goal'])
        return text

    @app_commands.command(name='诊断',description='私下查看当前服务器的最近错误或指定诊断编号。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(incident='编号')
    async def diagnosis(self,interaction:discord.Interaction,incident:str|None=None):
        await interaction.response.defer(ephemeral=True)
        try:
            text = await self.describe(interaction.guild,interaction.user,incident)
        except ModerationError as exc:
            text = str(exc)
        await interaction.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='核对操作',description='以审核日志确认结果不明的操作，可选择恢复自动检查；不会重试操作。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(kind=[app_commands.Choice(name=k,value=k) for k in ('管理','骚扰','超量请求','刷屏')])
    @app_commands.rename(kind='类型',number='编号',resume='恢复检查')
    async def reconcile_slash(self,interaction:discord.Interaction,kind:str,number:str,resume:bool=False):
        await interaction.response.defer(ephemeral=True)
        try:
            text = await self.reconcile(interaction.guild,interaction.user,kind,number,resume)
        except (ModerationError,discord.HTTPException) as exc:
            text = scrub(exc)
        await interaction.followup.send(text[:1800],ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
