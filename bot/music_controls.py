"""Native music buttons and voice housekeeping; no model calls."""
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks


class MusicPanel(discord.ui.View):
    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service

    async def interaction_check(self, interaction):
        guild = interaction.guild
        vc = guild.voice_client if guild else None
        member = guild.get_member(interaction.user.id) if guild else None
        voice = getattr(member, 'voice', None)
        channel = getattr(voice, 'channel', None)
        if not vc or not channel or channel.id != vc.channel.id:
            await interaction.response.send_message('请先加入 Klee 所在的语音频道再操作音乐面板。', ephemeral=True)
            return False
        permissions = vc.channel.permissions_for(member)
        if not permissions.view_channel or not permissions.connect:
            await interaction.response.send_message('你当前无法使用这个语音频道。', ephemeral=True)
            return False
        return True

    async def act(self, interaction, action):
        await interaction.response.defer(ephemeral=True)
        player = self.service.player_factory(interaction.guild)
        async with player.lock:
            vc = interaction.guild.voice_client
            channel = getattr(getattr(interaction.user, 'voice', None), 'channel', None)
            if not vc or not channel or channel.id != vc.channel.id:
                await interaction.followup.send('语音状态已变化，请重新加入 Klee 所在频道。', ephemeral=True)
                return
            if action == 'queue':
                text = player.queue_text()[:1800]
            elif action == 'pause':
                if vc.is_paused():
                    vc.resume()
                    player.idle_since = None
                    text = '已继续播放。'
                elif vc.is_playing():
                    vc.pause()
                    player.idle_since = time.monotonic()
                    text = '已暂停，暂停满 5 分钟会自动退出。'
                else:
                    text = '当前没有播放音乐。'
            elif action == 'skip':
                text = '已跳过。' if player.skip(interaction.guild) else '当前没有播放音乐。'
            elif action in {'down', 'up'}:
                player.volume = round(max(0, min(1, player.volume + (-0.1 if action == 'down' else 0.1))), 2)
                if isinstance(vc.source, discord.PCMVolumeTransformer):
                    vc.source.volume = player.volume
                text = f'音量：{round(player.volume * 100)}%。'
            elif action == 'repeat':
                modes = ['off', 'one', 'queue']
                player.set_repeat(modes[(modes.index(player.repeat_mode) + 1) % len(modes)])
                text = '循环模式：'+{'off':'关闭','one':'单曲','queue':'队列'}[player.repeat_mode]
            elif action == 'stop':
                player.clear()
                vc.stop()
                text = '已停止并清空队列，空闲 5 分钟后退出。'
            else:
                await player._disconnect(interaction.guild)
                text = '已清空队列并退出语音。'
        await self.service.refresh(player)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label='暂停／继续', style=discord.ButtonStyle.primary, custom_id='klee:music:pause')
    async def pause(self, interaction, button):
        await self.act(interaction, 'pause')

    @discord.ui.button(label='跳过', custom_id='klee:music:skip')
    async def skip(self, interaction, button):
        await self.act(interaction, 'skip')

    @discord.ui.button(label='队列', custom_id='klee:music:queue')
    async def queue(self, interaction, button):
        await self.act(interaction, 'queue')

    @discord.ui.button(label='音量 −', custom_id='klee:music:down')
    async def down(self, interaction, button):
        await self.act(interaction, 'down')

    @discord.ui.button(label='音量 ＋', custom_id='klee:music:up')
    async def up(self, interaction, button):
        await self.act(interaction, 'up')

    @discord.ui.button(label='循环', row=1, custom_id='klee:music:repeat')
    async def repeat(self, interaction, button):
        await self.act(interaction, 'repeat')

    @discord.ui.button(label='停止', row=1, style=discord.ButtonStyle.danger, custom_id='klee:music:stop')
    async def stop_music(self, interaction, button):
        await self.act(interaction, 'stop')

    @discord.ui.button(label='退出语音', row=1, style=discord.ButtonStyle.danger, custom_id='klee:music:leave')
    async def leave(self, interaction, button):
        await self.act(interaction, 'leave')

    async def on_error(self, interaction, error, item):
        print('music control failed: '+type(error).__name__, flush=True)
        if interaction.response.is_done():
            await interaction.followup.send('音乐操作未确认完成，请查看当前播放状态。', ephemeral=True)
        else:
            await interaction.response.send_message('音乐操作未确认完成，请重试。', ephemeral=True)


class MusicControls(commands.Cog):
    def __init__(self, bot, player_factory, players):
        self.bot, self.player_factory, self.players = bot, player_factory, players
        self.view = MusicPanel(self)

    async def cog_load(self):
        self.bot.add_view(self.view)
        self.housekeeping.start()

    async def cog_unload(self):
        self.housekeeping.cancel()
        self.view.stop()
        for guild in self.bot.guilds:
            player = self.players.get(guild.id)
            if player is not None:
                await player.disconnect(guild)

    def embed(self, player):
        guild = self.bot.get_guild(player.guild_id)
        vc = guild.voice_client if guild else None
        state = '未连接语音' if not vc else '已暂停' if vc.is_paused() else '正在播放' if vc.is_playing() else '等待点歌'
        embed = discord.Embed(title='🎵 Klee 音乐面板', description=state, colour=0xf29b45)
        if player.current:
            embed.add_field(name='歌曲', value=discord.utils.escape_markdown(player.current.title)[:256] or '未命名', inline=False)
            embed.add_field(name='点歌人', value=discord.utils.escape_markdown(player.current.requester)[:128] or '未知')
        embed.add_field(name='队列', value=str(len(player.queue)))
        embed.add_field(name='音量', value=f'{round(player.volume * 100)}%')
        embed.add_field(name='循环', value={'off':'关闭','one':'单曲','queue':'队列'}[player.repeat_mode])
        embed.set_footer(text='同语音频道成员可操作 · 空闲/暂停5分钟退出 · 无人1分钟退出 · 听筒关闭')
        return embed

    async def publish(self, player, channel):
        if getattr(getattr(channel, 'guild', None), 'id', None) != player.guild_id:
            return
        if player.panel_message is not None and player.panel_message.channel.id == channel.id:
            await self.refresh(player, force=True)
            if player.panel_message is not None:
                return
        # A new request channel replaces the old panel, avoiding multiple live panels.
        if player.panel_message is not None:
            try:
                await player.panel_message.edit(view=None)
            except discord.HTTPException:
                pass
        embed = self.embed(player)
        player.panel_message = await channel.send(embed=embed, view=self.view, allowed_mentions=discord.AllowedMentions.none())
        player.panel_state = str(embed.to_dict())

    async def refresh(self, player, force=False):
        if player.panel_message is None:
            return
        embed = self.embed(player)
        state = str(embed.to_dict())
        if not force and state == player.panel_state:
            return
        try:
            await player.panel_message.edit(embed=embed, view=self.view, allowed_mentions=discord.AllowedMentions.none())
            player.panel_state = state
        except discord.HTTPException:
            player.panel_message = None

    async def check_voice(self, guild, player, now=None):
        now = time.monotonic() if now is None else now
        async with player.lock:
            vc = guild.voice_client
            if vc is None:
                player.idle_since = player.alone_since = None
                return
            voice = getattr(getattr(guild, 'me', None), 'voice', None)
            if voice is not None and not voice.self_deaf:
                await guild.change_voice_state(channel=vc.channel, self_deaf=True)
            if any(not member.bot for member in vc.channel.members):
                player.alone_since = None
            elif player.alone_since is None:
                player.alone_since = now
            inactive = vc.is_paused() or (not vc.is_playing() and not player.queue)
            if not inactive:
                player.idle_since = None
            elif player.idle_since is None:
                player.idle_since = now
            alone = player.alone_since is not None and now - player.alone_since >= 60
            idle = player.idle_since is not None and now - player.idle_since >= 300
            if alone or idle:
                await player._disconnect(guild)

    @tasks.loop(seconds=15)
    async def housekeeping(self):
        for guild in self.bot.guilds:
            # Also adopt existing voice connections after an extension reload.
            player = self.players.get(guild.id)
            if player is None and guild.voice_client:
                player = self.player_factory(guild)
            if player is None:
                continue
            try:
                await self.check_voice(guild, player)
                await self.refresh(player)
            except Exception as exc:
                print('voice housekeeping failed: '+type(exc).__name__, flush=True)

    @housekeeping.before_loop
    async def before_housekeeping(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name='音乐面板', description='打开音乐按钮面板；加入同一语音频道即可控制，不需要管理员。')
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.publish(self.player_factory(interaction.guild), interaction.channel)
        await interaction.followup.send('音乐面板已打开。', ephemeral=True)
