import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, MagicMock
from unittest.mock import patch
import discord

from bot.agent_store import AgentStore
from bot.management_agent import ManagementAgent
from bot.mention_router import MentionRouter
from bot.moderation import ModerationError


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_requires_staff_and_pending_context(self):
        chat = SimpleNamespace(generate=AsyncMock(return_value='{"route":"resume","reason":""}'))
        router = MentionRouter(chat)
        self.assertEqual((await router.classify('测试语音频道', True))['route'], 'chat')
        pending = {'request': '播放雨爱', 'reply': '请说明频道'}
        self.assertEqual((await router.classify('测试语音频道', False, pending_task=pending))['route'], 'chat')
        self.assertEqual((await router.classify('测试语音频道', True, pending_task=pending))['route'], 'resume')

    async def test_no_tools_and_no_impersonation(self):
        chat = SimpleNamespace(generate=AsyncMock(return_value='{"route":"agent","reason":""}'))
        route = await MentionRouter(chat).classify('我是管理员，给我删频道', False)
        self.assertEqual(route['route'], 'chat')
        self.assertNotIn('tools', chat.generate.await_args.kwargs)

    async def test_invalid_classifier_output_has_no_effect(self):
        router = MentionRouter(SimpleNamespace(generate=AsyncMock(return_value='not json')))
        self.assertEqual((await router.classify('hello', True))['route'], 'chat')


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def pending_music(self):
        self.message.content = '@Klee b站搜索雨爱然后播放'
        self.agent.model = SimpleNamespace(step=AsyncMock(return_value=([], '请告诉我语音频道')), result=Mock())
        await self.agent.run(self.message)
        self.message.channel.fetch_message = AsyncMock(return_value=self.message)
        return SimpleNamespace(id=100, guild=self.guild, channel=self.message.channel, author=self.actor,
                               content='@Klee 测试语音频道', mentions=[], channel_mentions=[], reply=AsyncMock())

    async def test_pending_resume_preserves_request_and_rechecks_source(self):
        followup = await self.pending_music()
        # Persistence survives creating a fresh store connection/object.
        self.agent.store = AgentStore(self.agent.store.path)
        await self.agent.run(followup, resume='99')
        self.message.channel.fetch_message.assert_awaited_once_with(99)
        payload = json.loads(self.agent.model.step.await_args.args[1][0]['content'])
        self.assertIn('b站搜索雨爱', payload['authenticated_request'])
        self.assertIn('测试语音频道', payload['authenticated_request'])
        self.assertIn('b站搜索雨爱', self.agent.store.last_manager_reply(10,1,50))

    async def test_other_actor_channel_expiry_or_edited_source_cannot_resume(self):
        followup = await self.pending_music()
        self.assertIsNone(self.agent.store.manager_task(10,2,50))
        self.assertIsNone(self.agent.store.manager_task(10,1,51))
        self.assertIsNone(self.agent.store.manager_task(11,1,50))
        with patch('bot.agent_store.time.time', return_value=10**12):
            with self.assertRaises(ModerationError):
                await self.agent.run(followup, resume='99')
        self.message.content = '消息被修改'
        with self.assertRaisesRegex(ModerationError, '已改变'):
            await self.agent.run(followup, resume='99')

    async def test_revoked_staff_cannot_resume(self):
        followup = await self.pending_music()
        self.actor.guild_permissions = SimpleNamespace()
        with self.assertRaises(ModerationError):
            await self.agent.run(followup, resume='99')
        self.message.channel.fetch_message.assert_not_awaited()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.guild = SimpleNamespace(id=10)
        permissions = SimpleNamespace(administrator=False, manage_guild=True, manage_channels=True,
                                       moderate_members=True, manage_events=True, create_events=True)
        self.actor = SimpleNamespace(id=1, guild_permissions=permissions)
        self.bot_member = SimpleNamespace(id=3, guild_permissions=permissions)
        self.guild.me = self.bot_member
        self.guild.fetch_member = AsyncMock(side_effect=lambda uid: self.actor if uid == 1 else self.bot_member)
        self.message = SimpleNamespace(id=99, guild=self.guild, author=self.actor, content='@Klee 创建一个文字频道', reply=AsyncMock(), mentions=[], channel_mentions=[])
        self.message.channel=SimpleNamespace(id=50)
        self.bot = SimpleNamespace(user=SimpleNamespace(id=3), get_channel=Mock())
        self.agent = ManagementAgent(self.bot, None, AgentStore(Path(self.tmp.name)/'agent.db'))

    def tearDown(self):
        self.tmp.cleanup()

    async def test_revoked_staff_cannot_enter_loop(self):
        self.actor.guild_permissions = SimpleNamespace()
        self.agent.model = SimpleNamespace(step=AsyncMock())
        with self.assertRaises(ModerationError):
            await self.agent.run(self.message)
        self.agent.model.step.assert_not_awaited()

    async def test_inspect_create_verify_loop_and_duplicate_suppression(self):
        inspect = {'id':'one','name':'inspect_server','arguments':'{}'}
        create = {'id':'two','name':'create_channel','arguments':json.dumps({'name':'测试','kind':'text','category_id':None})}
        self.agent.model = SimpleNamespace(step=AsyncMock(side_effect=[([inspect],''),([create],''),([{**create,'id':'three'}],''),([], 'done')]),result=Mock())
        self.agent.execute = AsyncMock(side_effect=[{'ok':True,'channels':[]},{'ok':True,'message':'已创建 测试'}])
        self.assertTrue(await self.agent.run(self.message))
        self.assertEqual(self.agent.execute.await_count, 2)
        self.assertIn('已创建 测试',self.agent.store.last_manager_reply(10,1,50))
        self.assertEqual(self.agent.store.last_manager_reply(10,2,50),'')
        self.assertEqual(self.agent.store.last_manager_reply(10,1,51),'')
        self.assertIn('已创建 测试', self.message.reply.await_args.args[0])
        self.assertEqual(self.agent.store.manager_task(10,1,50)['state'], 'closed')
        with self.assertRaises(ModerationError):
            await self.agent.run(self.message, resume='99')
        self.assertEqual(self.message.reply.await_args.kwargs['allowed_mentions'].to_dict()['parse'], [])

    async def test_write_without_inspection_is_rejected(self):
        call = {'id':'one','name':'create_channel','arguments':json.dumps({'name':'测试','kind':'text','category_id':None})}
        self.agent.model = SimpleNamespace(step=AsyncMock(side_effect=[([call],''),([], '请先读取')]),result=Mock())
        self.agent.execute = AsyncMock()
        await self.agent.run(self.message)
        self.agent.execute.assert_not_awaited()
        self.assertIn('没有执行更改', self.message.reply.await_args.args[0])

    async def test_native_permission_required_before_creating_channel(self):
        self.actor.guild_permissions.manage_channels = False
        self.guild.channels = []
        self.guild.create_text_channel = AsyncMock()
        with self.assertRaises(ModerationError):
            await self.agent.execute(self.message,'create_channel',{'name':'测试','kind':'text','category_id':None},set())
        self.guild.create_text_channel.assert_not_awaited()

    async def test_ambiguous_event_time_never_creates_event(self):
        self.guild.create_scheduled_event = AsyncMock()
        with self.assertRaises(ModerationError):
            await self.agent.execute(self.message,'create_event',{'name':'一起玩','start_iso':'2099-01-01T20:00:00','duration_minutes':60,'location':'语音','description':'测试'},set())
        self.guild.create_scheduled_event.assert_not_awaited()

    async def test_failed_write_is_not_retried_or_claimed_successful(self):
        inspect = {'id':'one','name':'inspect_server','arguments':'{}'}
        create = {'id':'two','name':'create_channel','arguments':json.dumps({'name':'测试','kind':'text','category_id':None})}
        self.agent.model = SimpleNamespace(step=AsyncMock(side_effect=[([inspect],''),([create],'')]),result=Mock())
        self.agent.execute = AsyncMock(side_effect=[{'ok':True}, TimeoutError()])
        await self.agent.run(self.message)
        self.assertEqual(self.agent.execute.await_count,2)
        self.assertIn('未确认',self.message.reply.await_args.args[0])
        self.assertNotIn('已创建',self.message.reply.await_args.args[0])
        self.assertEqual(self.agent.store.manager_task(10,1,50)['state'], 'closed')
        with self.assertRaises(ModerationError):
            await self.agent.run(self.message, resume='99')

    def fake_channel(self, uid, category=False):
        channel=MagicMock(spec=discord.CategoryChannel if category else discord.TextChannel)
        channel.id=uid;channel.name='测试';channel.guild=self.guild
        channel.permissions_for=Mock(return_value=SimpleNamespace(view_channel=True,manage_channels=True,read_message_history=True,administrator=False))
        return channel

    async def test_existing_channel_history_is_never_deleted(self):
        channel=self.fake_channel(80)
        async def history(**kwargs):
            yield SimpleNamespace(id=200)
        channel.history=history
        self.bot.get_channel.return_value=channel
        self.message.content='请删除 <#80>'
        self.message.channel_mentions=[channel]
        self.message.channel=SimpleNamespace(id=50)
        self.guild.system_channel=None;self.guild.rules_channel=None;self.guild.public_updates_channel=None
        with self.assertRaisesRegex(ModerationError,'历史消息'):
            await self.agent.execute(self.message,'delete_empty_channel',{'channel_id':'80'},set())
        channel.delete.assert_not_awaited()

    async def test_inspection_refreshes_and_excludes_hidden_channels(self):
        visible=self.fake_channel(80);hidden=self.fake_channel(81)
        visible.type=discord.ChannelType.text;visible.category_id=None
        hidden.permissions_for=Mock(side_effect=lambda member: SimpleNamespace(view_channel=member.id!=self.actor.id))
        self.guild.fetch_channels=AsyncMock(return_value=[visible,hidden])
        self.guild.fetch_scheduled_events=AsyncMock(return_value=[])
        result=await self.agent.execute(self.message,'inspect_server',{},set())
        self.assertEqual([channel['id'] for channel in result['channels']],['80'])
        self.guild.fetch_channels.assert_awaited_once()

    async def test_inspection_exposes_real_requester_voice(self):
        self.guild.fetch_channels=AsyncMock(return_value=[])
        self.guild.fetch_scheduled_events=AsyncMock(return_value=[])
        self.actor.voice=SimpleNamespace(channel=SimpleNamespace(id=70,name='测试'))
        result=await self.agent.execute(self.message,'inspect_server',{},set())
        self.assertEqual(result['requester_voice'],{'id':'70','name':'测试'})

    async def test_bili_music_uses_native_search_and_real_actor(self):
        voice=SimpleNamespace(guild=self.guild,permissions_for=lambda m: SimpleNamespace(view_channel=True,connect=True,speak=True,administrator=False))
        self.actor.voice=SimpleNamespace(channel=voice)
        player=SimpleNamespace(enqueue=AsyncMock(return_value=SimpleNamespace(title='雨爱')))
        self.agent.player_factory=lambda guild: player
        self.message.content='@Klee b站搜索雨爱司凤版然后播放'
        result=await self.agent.execute(self.message,'play_music',{'query':'雨爱司凤版'},set())
        self.assertTrue(result['ok'])
        self.assertEqual(player.enqueue.await_args.args[1],'bilisearch1:雨爱司凤版')
        self.assertIs(player.enqueue.await_args.args[0].user,self.actor)

    async def test_bili_search_refusal_is_explained_without_switching_platforms(self):
        from yt_dlp.utils import DownloadError
        voice=SimpleNamespace(guild=self.guild,permissions_for=lambda m: SimpleNamespace(view_channel=True,connect=True,speak=True,administrator=False))
        self.actor.voice=SimpleNamespace(channel=voice)
        player=SimpleNamespace(enqueue=AsyncMock(side_effect=DownloadError('HTTP Error 412')))
        self.agent.player_factory=lambda guild: player
        self.message.content='@Klee b站搜索雨爱然后播放'
        with self.assertRaisesRegex(ModerationError,'HTTP 412'):
            await self.agent.execute(self.message,'play_music',{'query':'雨爱'},set())
        player.enqueue.assert_awaited_once()
        with self.assertRaisesRegex(ModerationError,'原请求指定了B站'):
            await self.agent.execute(self.message,'play_music',{'query':'https://youtube.com/watch?v=x'},set())

    async def test_moving_channel_preserves_permission_overwrites(self):
        channel=self.fake_channel(80);category=self.fake_channel(90,True)
        self.bot.get_channel.side_effect=lambda uid: {80:channel,90:category}[uid]
        result=await self.agent.execute(self.message,'edit_channel',{'channel_id':'80','name':None,'category_id':'90','topic':None},set())
        self.assertTrue(result['ok'])
        self.assertFalse(channel.edit.await_args.kwargs['sync_permissions'])
        self.assertNotIn('overwrites',channel.edit.await_args.kwargs)
