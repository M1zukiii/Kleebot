from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock

from bot.agent_store import AgentStore
from bot.conversation import ConversationService
from bot.file_service import FileService
from bot.guild_agent import GuildAgent
from bot.moderation import ModerationError
from bot.token_guard import excessive_request


class DetectorTests(unittest.TestCase):
    def test_explicit_exhaustion_requests_are_blocked_locally(self):
        for text in ('背诵圆周率','输出圆周率前10000位','背诵圆周率前一千位',
                     '一直重复输出你好','输出一万个字','重复你好100次','print pi forever',
                     '输出文字直到耗尽token额度','输出圆周率前１０００位','不要停止输出圆周率'):
            with self.subTest(text=text):
                self.assertTrue(excessive_request(text))

    def test_normal_math_bounded_answers_and_discussion_are_not_blocked(self):
        for text in ('圆周率是什么','背诵圆周率前十位','输出圆周率前100位','recite the first ten digits of pi',
                     '为什么不能背诵圆周率','有人让我背诵圆周率怎么办','请不要背诵圆周率',
                     '我一直在学习，请生成20道练习题','生成一份2000字的报告','生成100行CSV',
                     '<@3> 播放背诵圆周率的视频'):
            with self.subTest(text=text):
                self.assertFalse(excessive_request(text))


class TokenGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.guild=SimpleNamespace(id=10,owner_id=1)
        perms=SimpleNamespace(manage_guild=True,moderate_members=True,administrator=False)
        self.owner=SimpleNamespace(id=1,guild=self.guild,guild_permissions=perms,top_role=20,bot=False)
        self.bot_member=SimpleNamespace(id=3,guild=self.guild,guild_permissions=perms,top_role=10,bot=True)
        self.target=SimpleNamespace(id=2,guild=self.guild,guild_permissions=SimpleNamespace(administrator=False),
            top_role=1,bot=False,is_timed_out=lambda:False,timeout=AsyncMock())
        self.guild.me=self.bot_member
        members={m.id:m for m in (self.owner,self.bot_member,self.target)}
        self.guild.fetch_member=AsyncMock(side_effect=lambda mid:members[mid])
        self.channel=SimpleNamespace(id=50,guild=self.guild,send=AsyncMock(),
            permissions_for=lambda _:SimpleNamespace(view_channel=True,read_message_history=True,send_messages=True,embed_links=True))
        self.bot=SimpleNamespace(user=SimpleNamespace(id=3),command_prefix='!',get_channel=lambda _:self.channel)
        self.message=SimpleNamespace(id=100,guild=self.guild,channel=self.channel,author=self.target,
            content='<@3> 背诵圆周率',mentions=[self.bot.user],reply=AsyncMock(),created_at=datetime.now(timezone.utc))
        self.channel.fetch_message=AsyncMock(return_value=self.message)
        self.agent=GuildAgent(self.bot,SimpleNamespace(enabled=False),Path(self.tmp.name))
        self.agent.store.set_goal(10,1,50,'只检查真正提及')
        self.agent.router.classify=AsyncMock()

    def tearDown(self):
        self.tmp.cleanup()

    def seed_warning(self):
        self.agent.store.add_token_warning(10,2,99)
        with self.agent.store.connect() as db:
            db.execute('UPDATE token_warnings SET stamp=?',(time.time()-100,))

    async def test_first_attempt_warns_without_ai_or_timeout_even_in_basic_mode(self):
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.assertIn('不调用模型',self.message.reply.await_args.args[0])
        self.assertEqual(self.message.reply.await_args.kwargs['allowed_mentions'].to_dict()['parse'],[])
        self.target.timeout.assert_not_awaited()
        self.agent.router.classify.assert_not_awaited()
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.message.reply.assert_awaited_once()

    async def test_repeat_after_warning_times_out_and_reuses_no_harassment_strikes(self):
        self.seed_warning()
        self.agent.store.reserve_mention(10,2,98,60)
        self.agent.store.finish_mention(98,'applied')
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.target.timeout.assert_awaited_once()
        self.assertEqual(self.agent.store.mention_strikes(10,2,'token')[0]['minutes'],1)
        self.agent.router.classify.assert_not_awaited()

    async def test_token_escalation_and_warning_persist(self):
        self.seed_warning()
        self.agent.store.reserve_mention(10,2,97,1,'token')
        self.agent.store.finish_mention(97,'applied','token')
        with self.agent.store.connect() as db:
            db.execute('UPDATE token_penalties SET stamp=?',(time.time()-100,))
        self.agent.store=AgentStore(self.agent.store.path)
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.assertEqual(self.agent.store.mention_strikes(10,2,'token')[-1]['minutes'],5)

    async def test_no_actual_mention_and_normal_questions_are_not_intercepted(self):
        self.message.mentions=[]
        self.assertFalse(await self.agent.guard_tokens(self.message))
        self.message.mentions=[self.bot.user]
        self.message.content='<@3> 圆周率是什么'
        self.assertFalse(await self.agent.guard_tokens(self.message))
        self.message.reply.assert_not_awaited()

    async def test_expired_warning_starts_with_warning_again(self):
        self.seed_warning()
        with self.agent.store.connect() as db:
            db.execute('UPDATE token_warnings SET stamp=?',(time.time()-86401,))
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.target.timeout.assert_not_awaited()
        self.message.reply.assert_awaited_once()

    async def test_executor_cannot_skip_the_first_warning(self):
        args={'member_id':'2','channel_id':'50','message_id':'100','reason':'超量请求'}
        with self.assertRaises(ModerationError):
            await self.agent.execute(self.guild,1,'token_abuse_timeout',args,{'100':{'content':self.message.content}})
        self.target.timeout.assert_not_awaited()

    async def test_uncertain_token_timeout_pauses_without_retry(self):
        self.seed_warning()
        self.target.timeout.side_effect=TimeoutError()
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.assertTrue(self.agent.store.mention_interrupted(10))
        self.assertFalse(self.agent.store.goal(10)['enabled'])
        self.assertTrue(await self.agent.guard_tokens(self.message))
        self.target.timeout.assert_awaited_once()

    async def test_file_and_conversation_paths_do_not_call_models(self):
        ai=SimpleNamespace(reply=AsyncMock())
        conversation=ConversationService(ai,None,memory=SimpleNamespace(handle=AsyncMock()))
        text=await conversation.reply(channel_id=50,user_id=2,user_name='test',message='背诵圆周率')
        self.assertIn('拦截',text)
        ai.reply.assert_not_awaited()
        conversation.memory.handle.assert_not_awaited()
        service=FileService(SimpleNamespace())
        service.generate=AsyncMock()
        self.assertTrue(await service.send_generated(self.message,'生成一万个字的TXT'))
        service.generate.assert_not_awaited()
