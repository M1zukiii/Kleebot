"""Durable operational receipts; no raw console, chat, secrets or model calls."""
import asyncio
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands, tasks
import httpx

from .diagnostics import scrub
from .maintenance import request_development, is_owner
from .moderation import ModerationError


class OperationStore:
    def __init__(self,path):
        self.path = path
        path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS channels(guild TEXT PRIMARY KEY,channel TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,guild TEXT,channel TEXT,actor TEXT,stamp REAL,run TEXT,state TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS events(key TEXT PRIMARY KEY,guild TEXT,origin TEXT,text TEXT,stamp REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS deliveries(key TEXT,channel TEXT,PRIMARY KEY(key,channel))')
            db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def settings(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM channels')]

    def configure(self,guild,channel):
        with self.connect() as db:
            if channel is None:
                db.execute('DELETE FROM channels WHERE guild=?',(str(guild),))
            else:
                db.execute('INSERT INTO channels VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET channel=excluded.channel',(str(guild),str(channel)))

    def meta(self,key,value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
            row=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            return row[0] if row else None

    def pending(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM requests WHERE state='pending' ORDER BY stamp LIMIT 20")]

    def track(self,rid,guild,channel,actor,run=''):
        with self.connect() as db:
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',(rid,str(guild),str(channel),str(actor),time.time(),str(run),'pending'))

    def update_request(self,rid,run=None,state=None):
        with self.connect() as db:
            if run is not None: db.execute('UPDATE requests SET run=? WHERE id=?',(str(run),rid))
            if state is not None: db.execute('UPDATE requests SET state=? WHERE id=?',(state,rid))

    def event(self,key,guild,text,origin=''):
        key=str(guild)+':'+key
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',(key,str(guild),str(origin),scrub(text)[:1700],time.time()))
            db.execute('DELETE FROM requests WHERE state!=? AND stamp<?',('pending',time.time()-14*86400))
            db.execute('DELETE FROM events WHERE stamp<?',(time.time()-14*86400,))
            db.execute('DELETE FROM events WHERE key NOT IN (SELECT key FROM events ORDER BY stamp DESC LIMIT 1000)')
            db.execute('DELETE FROM deliveries WHERE key NOT IN (SELECT key FROM events)')

    def deliverable(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT e.* FROM events e LEFT JOIN channels c ON c.guild=e.guild
                WHERE (e.origin!='' AND NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.key=e.key AND d.channel=e.origin))
                   OR (c.channel IS NOT NULL AND NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.key=e.key AND d.channel=c.channel))
                ORDER BY e.stamp LIMIT 100""")]

    def delivered(self,key,channel,mark=False):
        with self.connect() as db:
            if mark: db.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?)',(key,str(channel)))
            return db.execute('SELECT 1 FROM deliveries WHERE key=? AND channel=?',(key,str(channel))).fetchone() is not None


class Operations(commands.Cog):
    def __init__(self,bot,data_dir):
        self.bot,self.data_dir=bot,data_dir
        self.store=OperationStore(data_dir/'operations.sqlite3')
        self.instance=uuid.uuid4().hex
        self.started=False
        self.stopping=False
        self.lock=asyncio.Lock()
        self.api_failures=0
        self.next_poll=0
        self.repo=os.getenv('KLEE_GITHUB_REPO','M1zukiii/Kleebot').strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',self.repo):
            raise ValueError('Invalid repository')
        if self.store.meta('diagnostic_cursor') is None:
            self.store.meta('diagnostic_cursor',time.time())

    async def cog_load(self):
        self.monitor.start()

    def cog_unload(self):
        self.monitor.cancel()

    async def configure(self,guild,user,channel):
        if guild is None: raise ModerationError('请在服务器内设置日志频道。')
        actor=await guild.fetch_member(user.id)
        p=actor.guild_permissions
        if not (actor.id==guild.owner_id or p.administrator or p.manage_guild):
            raise ModerationError('设置日志频道需要管理服务器权限。')
        if channel is not None:
            me=await guild.fetch_member(self.bot.user.id)
            if not isinstance(channel,discord.TextChannel) or channel.guild.id!=guild.id:
                raise ModerationError('请选择本服务器的文字频道。')
            if not channel.permissions_for(actor).view_channel:
                raise ModerationError('你无法查看这个频道。')
            permissions=channel.permissions_for(me)
            if not permissions.view_channel or not permissions.send_messages:
                raise ModerationError('Klee需要查看和发送日志的权限。')
        await asyncio.to_thread(self.store.configure,guild.id,channel.id if channel else None)
        if channel:
            self.store.event('configured:'+uuid.uuid4().hex,guild.id,'📋 已接通运行日志：启停、开发结果、更新状态和错误编号；不会转发原始聊天或密钥。')
        return '运行日志已关闭。' if channel is None else f'已接通日志频道：<#{channel.id}>。'

    async def submit(self,ctx,task):
        if not is_owner(ctx.author.id):
            raise ValueError('这个指令只开放给配置的主人。')
        if len(self.store.pending())>=20:
            raise ValueError('待回报的开发任务过多，请等待现有任务完成。')
        rid=uuid.uuid4().hex
        self.store.track(rid,ctx.guild.id if ctx.guild else 'dm',ctx.channel.id,ctx.author.id)
        try:
            result=await request_development(task,rid)
        except (ValueError,RuntimeError):
            self.store.update_request(rid,state='rejected')
            raise
        return result+'\n执行完成或失败后会回报此频道；任务编号：'+rid[:8]

    @commands.Cog.listener()
    async def on_ready(self):
        if self.started: return
        self.started=True
        interrupted=self.store.meta('running')=='1'
        self.store.meta('running','1')
        for item in self.store.settings():
            text='🟢 Klee已启动并连接Discord。'
            if interrupted: text+=' 上次运行未记录正常关闭，可能曾被强制结束。'
            self.store.event('start:'+self.instance,item['guild'],text)

    async def shutdown(self):
        if self.stopping: return
        self.stopping=True
        self.monitor.cancel()
        if not self.started: return
        async with self.lock:
            for item in self.store.settings():
                self.store.event('stop:'+self.instance,item['guild'],'🔴 Klee正在正常关闭或重启。')
            await self.flush()
            self.store.meta('running','0')

    async def api(self,path):
        token=os.getenv('KLEE_GITHUB_ACTIONS_TOKEN','').strip()
        async with httpx.AsyncClient(timeout=15,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'}) as client:
            r=await client.get('https://api.github.com/repos/'+self.repo+path)
            r.raise_for_status()
            return r.json()

    async def pull_link(self,run):
        branch=f"klee/develop-{run['id']}-{run.get('run_attempt',1)}"
        try:
            # Public repository lookup needs no additional token permissions.
            async with httpx.AsyncClient(timeout=10) as client:
                r=await client.get(f'https://api.github.com/repos/{self.repo}/pulls',params={'head':self.repo.split('/')[0]+':'+branch,'state':'all'})
                r.raise_for_status()
                values=r.json()
                if values: return f"https://github.com/{self.repo}/pull/{int(values[0]['number'])}"
        except (httpx.HTTPError,ValueError,TypeError): pass
        return ''

    async def poll_development(self):
        pending=self.store.pending()
        if not pending: return
        listing=None
        for item in pending:
            if item['run']:
                run=await self.api('/actions/runs/'+item['run'])
            else:
                if listing is None:
                    listing=(await self.api('/actions/workflows/klee-develop.yml/runs?event=workflow_dispatch&per_page=100'))['workflow_runs']
                matches=[r for r in listing if r.get('display_title')=='Klee development '+item['id']]
                if not matches:
                    if time.time()-item['stamp']>3600:
                        self.store.event('missing:'+item['id'],item['guild'],'⚠️ 开发任务在1小时内未关联到GitHub执行记录，请检查Actions页面。',item['channel'])
                        self.store.update_request(item['id'],state='unconfirmed')
                    continue
                if len(matches)!=1:
                    self.store.event('ambiguous:'+item['id'],item['guild'],'⚠️ 开发任务关联到多个执行记录，无法确认结果；请管理者核对Actions页面。',item['channel'])
                    self.store.update_request(item['id'],state='unconfirmed')
                    continue
                run=matches[0]
                self.store.update_request(item['id'],run=run['id'])
            if run.get('status')!='completed': continue
            url=f"https://github.com/{self.repo}/actions/runs/{int(run['id'])}"
            conclusion=run.get('conclusion')
            text='✅ 开发验证通过，草稿PR等待审核；尚未合并或安装。' if conclusion=='success' else '❌ 开发任务失败，未安装到线上。'
            if conclusion!='success':
                jobs=(await self.api(f"/actions/runs/{int(run['id'])}/jobs?filter=latest&per_page=20")).get('jobs',[])
                failed=[j['name'] for j in jobs if j.get('conclusion') in {'failure','cancelled','timed_out'}]
                if 'validate' in failed: text+=' 候选修改未通过自动测试。'
                elif 'propose' in failed: text+=' 生成修改或创建草稿PR阶段失败。'
                elif conclusion in {'cancelled','timed_out'}: text+=' 执行已取消或超时。'
            pr=await self.pull_link(run)
            text+=f"\n任务编号：{item['id'][:8]}\n执行记录：{url}"+(f'\n草稿PR：{pr}' if pr else '')
            self.store.event('development:'+item['id'],item['guild'],text,item['channel'])
            self.store.update_request(item['id'],state='completed')

    def capture_local(self):
        settings=self.store.settings()
        if not settings: return
        try:
            state=json.loads((self.data_dir/'update_status.json').read_text(encoding='utf-8'))
            fingerprint=str(state.get('updated_at'))+':'+str(state.get('status'))+':'+str(state.get('revision'))
            if self.store.meta('update')!=fingerprint:
                labels={'building':'正在准备并测试新版','updated':'新版已上线','current':'当前已是最新版','failed':'更新失败，需检查状态','restarting':'正在重启','ready':'本机守护已就绪','rollback_failed':'新版与回退均未确认上线','stopped':'本机守护已停止'}
                text='🔧 更新状态：'+labels.get(state.get('status'),'状态已变化')+'；版本：'+str(state.get('revision','未知'))[:12]
                for item in settings: self.store.event('update:'+fingerprint,item['guild'],text)
                self.store.meta('update',fingerprint)
        except (OSError,ValueError,TypeError): pass
        path=self.data_dir/'diagnostics.sqlite3'
        if not path.exists(): return
        db=sqlite3.connect(path)
        try:
            rows=db.execute('SELECT id,guild,category,kind,stamp FROM incidents WHERE stamp>? ORDER BY stamp LIMIT 100',(float(self.store.meta('diagnostic_cursor')),)).fetchall()
        finally: db.close()
        guilds={item['guild'] for item in settings}
        for rid,guild,category,kind,stamp in rows:
            if guild in guilds:
                self.store.event('diagnostic:'+rid,guild,f'⚠️ {category}失败（{kind}）；诊断编号：{rid}。管理者可用 /诊断 私下查看详情。')
        if rows: self.store.meta('diagnostic_cursor',rows[-1][-1])

    async def flush(self):
        settings={x['guild']:x['channel'] for x in self.store.settings()}
        delivered=0
        for event in self.store.deliverable():
            targets={event['origin'],settings.get(event['guild'],'')}-{''}
            for cid in targets:
                if self.store.delivered(event['key'],cid): continue
                channel=self.bot.get_channel(int(cid))
                if channel is None: continue
                guild=getattr(channel,'guild',None)
                if str(guild.id if guild else 'dm')!=event['guild']: continue
                if guild:
                    permissions=channel.permissions_for(guild.me)
                    if not permissions.view_channel or not permissions.send_messages: continue
                try:
                    await channel.send(event['text'],allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException: continue
                self.store.delivered(event['key'],cid,True)
                delivered+=1
                if delivered>=10: return

    @tasks.loop(seconds=20)
    async def monitor(self):
        async with self.lock:
            if not self.stopping:
                if time.time()>=self.next_poll:
                    try:
                        await asyncio.wait_for(self.poll_development(),timeout=12)
                        self.api_failures=0
                    except Exception as exc:
                        self.api_failures+=1
                        self.next_poll=time.time()+min(300,20*2**min(self.api_failures,4))
                        print('development receipt check failed: '+type(exc).__name__,flush=True)
                        if self.api_failures>=3:
                            for item in self.store.pending():
                                self.store.event('receipt_unavailable:'+item['id'],item['guild'],'⚠️ 暂时无法读取开发任务状态，请管理者检查网络和GitHub令牌权限；不会把查询失败当成开发成功。',item['channel'])
                try:
                    await asyncio.to_thread(self.capture_local)
                    await self.flush()
                except Exception as exc:
                    # Do not publish raw API responses or recursively log delivery errors.
                    print('operational receipt check failed: '+type(exc).__name__,flush=True)

    @monitor.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name='日志频道',description='设置运行摘要日志频道，记录启停、开发结果、更新和错误编号。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(channel='频道',enabled='开启')
    async def log_channel(self,interaction:discord.Interaction,channel:discord.TextChannel|None=None,enabled:bool=True):
        await interaction.response.defer(ephemeral=True)
        try:
            result=await self.configure(interaction.guild,interaction.user,(channel or interaction.channel) if enabled else None)
        except (ModerationError,discord.HTTPException) as exc: result=scrub(exc)
        await interaction.followup.send(result,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
