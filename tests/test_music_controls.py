import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord

from bot.music_controls import MusicControls
from bot.player import GuildPlayer
from bot.resolver import Track


class MusicControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = NS(id=5, members=[NS(bot=False)], permissions_for=lambda _: NS(view_channel=True, connect=True))
        self.vc = NS(channel=self.channel, source=None, is_playing=Mock(return_value=False), is_paused=Mock(return_value=False),
                     disconnect=AsyncMock(), stop=Mock(), pause=Mock(), resume=Mock(), move_to=AsyncMock())
        self.guild = NS(id=1, voice_client=self.vc, me=NS(voice=NS(self_deaf=True)), change_voice_state=AsyncMock())
        self.member = NS(id=2, voice=NS(channel=self.channel), display_name='user')
        self.guild.get_member = lambda uid: self.member if uid == 2 else None
        self.bot = NS(get_guild=lambda _: self.guild)
        self.player = GuildPlayer(self.bot, 1, NS())
        self.service = MusicControls(self.bot, lambda _: self.player, {1:self.player})

    def interaction(self):
        return NS(guild=self.guild, user=self.member, response=NS(defer=AsyncMock(), send_message=AsyncMock()), followup=NS(send=AsyncMock()))

    def track(self, name='song'):
        return Track(name,'https://example.org/video','audio',60,'user',{})

    async def test_idle_timeout_but_active_playback_survives(self):
        await self.service.check_voice(self.guild, self.player, 0)
        await self.service.check_voice(self.guild, self.player, 299)
        self.vc.disconnect.assert_not_awaited()
        await self.service.check_voice(self.guild, self.player, 300)
        self.vc.disconnect.assert_awaited_once()
        self.vc.disconnect.reset_mock()
        self.vc.is_playing.return_value = True
        self.player.idle_since = 0
        await self.service.check_voice(self.guild, self.player, 10000)
        self.vc.disconnect.assert_not_awaited()
        self.assertIsNone(self.player.idle_since)

    async def test_paused_with_queue_exits_and_cleans_queue(self):
        self.vc.is_paused.return_value = True
        self.player.queue.append(self.track())
        await self.service.check_voice(self.guild, self.player, 0)
        await self.service.check_voice(self.guild, self.player, 300)
        self.vc.disconnect.assert_awaited_once()
        self.assertFalse(self.player.queue)

    async def test_alone_timeout_resets_when_human_returns(self):
        self.vc.is_playing.return_value = True
        self.channel.members = [NS(bot=True)]
        await self.service.check_voice(self.guild, self.player, 0)
        self.channel.members.append(NS(bot=False))
        await self.service.check_voice(self.guild, self.player, 59)
        self.assertIsNone(self.player.alone_since)
        self.channel.members = [NS(bot=True)]
        await self.service.check_voice(self.guild, self.player, 60)
        await self.service.check_voice(self.guild, self.player, 119)
        self.vc.disconnect.assert_not_awaited()
        await self.service.check_voice(self.guild, self.player, 120)
        self.vc.disconnect.assert_awaited_once()

    async def test_join_move_and_existing_voice_are_self_deafened(self):
        interaction = self.interaction()
        self.guild.voice_client = None
        self.channel.connect = AsyncMock(return_value=self.vc)
        await self.player.ensure_voice(interaction)
        self.channel.connect.assert_awaited_once_with(self_deaf=True)
        self.guild.change_voice_state.assert_awaited_with(channel=self.channel,self_deaf=True)
        self.guild.voice_client = self.vc
        self.member.voice.channel = NS(id=6)
        await self.player.ensure_voice(interaction)
        self.vc.move_to.assert_awaited_once_with(self.member.voice.channel)
        self.guild.me.voice.self_deaf = False
        await self.service.check_voice(self.guild, self.player, 0)
        self.guild.change_voice_state.assert_awaited_with(channel=self.vc.channel,self_deaf=True)

    async def test_controls_reject_other_channels_and_moved_users(self):
        interaction = self.interaction()
        self.member.voice.channel = NS(id=99)
        self.assertFalse(await self.service.view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()
        await self.service.view.act(interaction, 'stop')
        self.vc.stop.assert_not_called()
        self.member.voice.channel = self.channel
        self.assertTrue(await self.service.view.interaction_check(interaction))

    async def test_controls_pause_resume_volume_and_leave_without_staff(self):
        interaction = self.interaction()
        self.vc.is_playing.return_value = True
        await self.service.view.act(interaction, 'pause')
        self.vc.pause.assert_called_once()
        self.vc.is_paused.return_value = True
        await self.service.view.act(interaction, 'pause')
        self.vc.resume.assert_called_once()
        self.player.volume = 1
        await self.service.view.act(interaction, 'up')
        self.assertEqual(self.player.volume, 1)
        self.player.volume = 0
        await self.service.view.act(interaction, 'down')
        self.assertEqual(self.player.volume, 0)
        self.player.queue.append(self.track())
        await self.service.view.act(interaction, 'leave')
        self.assertFalse(self.player.queue)
        self.vc.disconnect.assert_awaited_once()

    async def test_clear_invalidates_old_callback_and_paused_skip_works(self):
        track = self.track()
        self.player.current = track
        self.player.repeat_mode = 'queue'
        old_generation = self.player.generation
        self.player.clear()
        self.player.queue.append(self.track('new'))
        with patch.object(GuildPlayer, '_play_next', new_callable=AsyncMock) as play:
            await self.player._finished(self.vc, track, old_generation)
            play.assert_not_awaited()
        self.assertEqual(self.player.queue[0].title, 'new')
        self.vc.is_paused.return_value = True
        self.assertTrue(self.player.skip(self.guild))
        self.vc.stop.assert_called_once()

    async def test_after_callback_runs_on_loop_and_advances_queue(self):
        self.bot.loop = asyncio.get_running_loop()
        self.player.queue.append(self.track())
        self.vc.play = Mock()
        with patch('bot.player.discord.FFmpegPCMAudio'), patch('bot.player.discord.PCMVolumeTransformer'), patch('bot.player.ffmpeg_executable',return_value='ffmpeg'):
            await self.player._play_next(self.vc)
        callback = self.vc.play.call_args.kwargs['after']
        callback(None)
        await asyncio.sleep(0.02)
        self.assertIsNone(self.player.current)
        self.assertIsNotNone(self.player.idle_since)

    async def test_failed_playback_does_not_repeat_failed_track(self):
        self.player.repeat_mode = 'one'
        with patch.object(GuildPlayer, '_play_next', new_callable=AsyncMock):
            await self.player._finished(self.vc, self.track(), 0, RuntimeError())
        self.assertFalse(self.player.queue)

    async def test_panel_refresh_is_bounded_but_new_request_replaces_message(self):
        channel = NS(id=50,guild=self.guild,send=AsyncMock())
        message = NS(channel=channel,edit=AsyncMock(),delete=AsyncMock())
        channel.send.return_value = message
        await self.service.publish(self.player, channel)
        await self.service.refresh(self.player)
        message.edit.assert_not_awaited()
        self.player.volume = 0.5
        await self.service.refresh(self.player)
        message.edit.assert_awaited_once()
        await self.service.publish(self.player, channel)
        message.delete.assert_awaited_once()
        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        self.assertTrue(channel.send.call_args.kwargs['silent'])
        self.assertTrue(self.service.view.is_persistent())

    async def test_stay_survives_restart_and_bypasses_idle_and_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            service = MusicControls(self.bot, lambda _:self.player, {1:self.player}, Path(directory))
            service.set_stay(1,5)
            restarted = MusicControls(self.bot, lambda _:self.player, {1:self.player}, Path(directory))
            self.assertEqual(restarted.stay, {'1':5})
            self.channel.members = []
            self.player.idle_since = self.player.alone_since = 0
            await restarted.check_voice(self.guild,self.player,10000)
            self.vc.disconnect.assert_not_awaited()
            self.assertIn('常驻已开启',restarted.embed(self.player).footer.text)
            restarted.set_stay(1,None)
            await restarted.check_voice(self.guild,self.player,10000)
            await restarted.check_voice(self.guild,self.player,10060)
            self.vc.disconnect.assert_awaited_once()
            loaded = MusicControls(self.bot, lambda _:self.player, {}, Path(directory))
            self.assertEqual(loaded.stay,{})

    async def test_stay_reconnects_with_self_deaf_and_rate_limits_failures(self):
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.connect = AsyncMock()
        channel.permissions_for.return_value = NS(view_channel=True,connect=True)
        self.guild.get_channel = Mock(return_value=channel)
        self.guild.voice_client = None
        self.service.set_stay(1,5)
        await self.service.check_voice(self.guild,self.player,0)
        channel.connect.assert_awaited_once_with(self_deaf=True,timeout=15)
        await self.service.check_voice(self.guild,self.player,59)
        self.assertEqual(channel.connect.await_count,1)
        await self.service.check_voice(self.guild,self.player,60)
        self.assertEqual(channel.connect.await_count,2)
        channel.permissions_for.return_value.connect = False
        await self.service.check_voice(self.guild,self.player,120)
        self.assertEqual(channel.connect.await_count,2)

    async def test_stay_command_requires_same_channel_and_leave_cancels_it(self):
        interaction = self.interaction()
        self.member.voice.channel = NS(id=99)
        await self.service.stay_voice.callback(self.service,interaction,True)
        self.assertFalse(self.service.stay)
        self.member.voice.channel = self.channel
        await self.service.stay_voice.callback(self.service,interaction,True)
        self.assertEqual(self.service.stay,{'1':5})
        await self.service.view.act(interaction,'leave')
        self.assertFalse(self.service.stay)
        self.vc.disconnect.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
