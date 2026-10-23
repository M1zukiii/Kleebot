from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot.agent_model import ToolModel
from bot.agent_store import AgentStore
from bot.guild_agent import GuildAgent
from bot.management_agent import TOOLS
from bot.moderation import ModerationError


def person(uid, guild, rank, moderator=False, bot=False):
    return SimpleNamespace(id=uid, guild=guild, top_role=rank, bot=bot,
        guild_permissions=SimpleNamespace(manage_guild=moderator, moderate_members=moderator, administrator=False),
        timeout=AsyncMock(), is_timed_out=lambda: False)


class GuildAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.guild = SimpleNamespace(id=10, owner_id=1)
        self.owner = person(1, self.guild, 20, True)
        self.target = person(2, self.guild, 1)
        self.bot_member = person(3, self.guild, 10, True, True)
        self.guild.me = self.bot_member
        self.bot_user = SimpleNamespace(id=3)
        members = {p.id: p for p in (self.owner, self.target, self.bot_member)}
        self.guild.fetch_member = AsyncMock(side_effect=lambda uid: members[uid])
        self.permissions = SimpleNamespace(view_channel=True, read_message_history=True, send_messages=True, embed_links=True)
        self.message = SimpleNamespace(id=400, author=self.target, content='重复刷屏', created_at=datetime.now(timezone.utc), mentions=[self.bot_user], guild=self.guild)
        self.channel = SimpleNamespace(id=50, guild=self.guild, permissions_for=lambda _: self.permissions,
                                       fetch_message=AsyncMock(return_value=self.message), send=AsyncMock())
        self.bot = SimpleNamespace(command_prefix='!', get_channel=lambda uid: self.channel if uid == 50 else None,
                                   get_guild=lambda uid: self.guild if uid == 10 else None, user=self.bot_user)
        self.message.channel = self.channel
        self.agent = GuildAgent(self.bot, SimpleNamespace(enabled=True), self.data)
        self.agent.router = SimpleNamespace(classify=AsyncMock(return_value={'route': 'chat', 'reason': ''}))
        self.agent.management.run = AsyncMock(return_value=True)
        self.agent.store.set_goal(10, 1, 50, '多次警告仍刷屏才禁言60秒')
        self.args = {'member_id': '2', 'channel_id': '50', 'message_id': '400', 'reason': '警告后仍重复刷屏'}

    def tearDown(self):
        self.tmp.cleanup()

    def seed_warnings(self, count=3):
        for n in range(count):
            self.agent.store.add_warning(10, 2, 50, 100+n)
        with self.agent.store.connect() as db:
            db.execute('UPDATE warnings SET stamp=?', (time.time()-120,))

    async def execute(self, name):
        return await self.agent.execute(self.guild, 1, name, self.args, {'400'})

    async def test_insufficient_warnings_cannot_timeout(self):
        self.seed_warnings(2)
        with self.assertRaisesRegex(ModerationError, '三次'):
            await self.execute('timeout_member')
        self.target.timeout.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_non_mentions_never_record_or_invoke_model(self):
        self.message.mentions = []
        self.assertFalse(await self.agent.observe(self.message))
        self.assertEqual(self.agent.store.observations(10), [])
        self.agent.router.classify.assert_not_awaited()
        self.agent.management.run.assert_not_awaited()

    async def test_normal_mentions_never_call_agent(self):
        self.assertFalse(await self.agent.observe(self.message))
        self.agent.router.classify.assert_awaited_once()
        self.agent.management.run.assert_not_awaited()
        self.assertEqual(self.agent.store.observations(10), [])

    async def test_inappropriate_mention_warns_without_agent(self):
        self.agent.router.classify.return_value = {'route': 'warn', 'reason': '直接辱骂'}
        self.assertTrue(await self.agent.observe(self.message))
        self.channel.send.assert_awaited_once()
        self.agent.management.run.assert_not_awaited()

    async def test_nonstaff_cannot_invoke_agent_even_if_router_requests_it(self):
        self.agent.router.classify.return_value = {'route': 'agent', 'reason': ''}
        self.assertFalse(await self.agent.observe(self.message))
        self.agent.management.run.assert_not_awaited()

    async def test_staff_task_invokes_agent(self):
        self.message.author = self.owner
        self.agent.router.classify.return_value = {'route': 'agent', 'reason': ''}
        self.assertTrue(await self.agent.observe(self.message))
        self.agent.management.run.assert_awaited_once_with(self.message)

    async def test_executor_rejects_unmentioned_evidence(self):
        self.message.mentions = []
        with self.assertRaisesRegex(ModerationError, '@Klee'):
            await self.execute('warn_member')
        self.channel.send.assert_not_awaited()

    async def test_guild_owner_can_receive_warning_but_never_timeout(self):
        self.message.author = self.owner
        self.args['member_id'] = '1'
        result = await self.execute('warn_member')
        self.assertTrue(result['ok'])
        self.channel.send.assert_awaited_once()
        self.owner.timeout.assert_not_awaited()

    async def test_success_after_three_warnings_is_exactly_sixty_seconds(self):
        self.seed_warnings()
        before = time.time()
        result = await self.execute('timeout_member')
        self.assertTrue(result['ok'])
        self.assertEqual(result['timeout_seconds'], 60)
        self.assertAlmostEqual(self.target.timeout.await_args.args[0].timestamp()-before, 60, delta=2)
        self.assertEqual(self.agent.store.warnings(10, 2), [])
        self.assertEqual(self.channel.send.await_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        fields = {field.name: field.value for field in self.channel.send.await_args.kwargs['embed'].fields}
        self.assertEqual(fields['禁言时长'], '60秒')
        self.assertIn('Klee Agent', fields['执行者'])

    async def test_old_evidence_before_last_warning_is_rejected(self):
        self.seed_warnings()
        self.message.created_at = datetime.fromtimestamp(time.time()-180, timezone.utc)
        with self.assertRaisesRegex(ModerationError, '上次警告之前'):
            await self.execute('timeout_member')
        self.target.timeout.assert_not_awaited()

    async def test_evidence_must_be_observed_and_belong_to_target(self):
        with self.assertRaisesRegex(ModerationError, '观察到'):
            await self.agent.execute(self.guild, 1, 'warn_member', self.args, set())
        self.message.author = self.owner
        with self.assertRaisesRegex(ModerationError, '作者'):
            await self.execute('warn_member')
        self.channel.send.assert_not_awaited()

    async def test_paused_or_changed_goal_cancels_action(self):
        self.agent.store.pause(10)
        with self.assertRaisesRegex(ModerationError, '暂停'):
            await self.execute('warn_member')
        self.channel.send.assert_not_awaited()
        self.agent.store.set_goal(10, 1, 50, 'new goal')
        with self.assertRaises(ModerationError):
            await self.execute('warn_member')

    async def test_warning_record_is_written_only_after_public_send(self):
        self.channel.send.side_effect = RuntimeError('network')
        with self.assertRaises(RuntimeError):
            await self.execute('warn_member')
        self.assertEqual(self.agent.store.warnings(10, 2), [])
        self.assertFalse(self.agent.store.goal(10)['enabled'])

    async def test_pause_during_preflight_stops_timeout_at_final_boundary(self):
        self.seed_warnings()
        original = self.agent.current
        count = 0
        async def pause_after_check(*args):
            nonlocal count
            result = await original(*args)
            count += 1
            if count == 2:
                self.agent.store.pause(10)
            return result
        self.agent.current = pause_after_check
        with self.assertRaises(ModerationError):
            await self.execute('timeout_member')
        self.target.timeout.assert_not_awaited()

    async def test_warning_persists_and_same_evidence_cannot_count_twice(self):
        result = await self.execute('warn_member')
        self.assertEqual(result['warning_count'], 1)
        reopened = AgentStore(self.data/'guild_agent.sqlite3')
        self.assertEqual(len(reopened.warnings(10, 2)), 1)
        with self.assertRaises(ModerationError):
            await self.execute('warn_member')
        self.channel.send.assert_awaited_once()

    async def test_observations_remain_bounded(self):
        for mid in range(100):
            self.agent.store.observe(10, 50, mid, 2, 'x'*1000, time.time())
        rows = self.agent.store.observations(10)
        self.assertEqual(len(rows), 80)
        self.assertEqual(len(rows[0]['content']), 700)

    async def test_permission_loss_disables_autonomy(self):
        self.owner.guild_permissions.manage_guild = False
        with self.assertRaises(ModerationError):
            await self.execute('warn_member')
        self.assertFalse(self.agent.store.goal(10)['enabled'])
        self.channel.send.assert_not_awaited()


class AgentProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_native_tool_items_are_returned_to_model(self):
        call = SimpleNamespace(type='function_call', call_id='one', name='warn_member', arguments='{}')
        create = AsyncMock(return_value=SimpleNamespace(status='completed', output=[call], output_text=''))
        chat = SimpleNamespace(config=SimpleNamespace(provider='openai', model='configured-model'),
                               _get_client=lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
        model = ToolModel(chat)
        history = [{'role':'user','content':'test'}]
        calls, _ = await model.step('instructions', history, TOOLS)
        self.assertEqual(calls[0]['name'], 'warn_member')
        self.assertIs(history[-1], call)
        model.result(history, 'one', {'ok': True})
        self.assertEqual(history[-1]['type'], 'function_call_output')
        self.assertFalse(create.await_args.kwargs['parallel_tool_calls'])
        self.assertFalse(create.await_args.kwargs['store'])

    async def test_incomplete_response_is_not_executed(self):
        create = AsyncMock(return_value=SimpleNamespace(status='incomplete'))
        model = ToolModel(SimpleNamespace(config=SimpleNamespace(provider='openai', model='configured'),
                         _get_client=lambda: SimpleNamespace(responses=SimpleNamespace(create=create))))
        with self.assertRaises(ValueError):
            await model.step('instructions', [], TOOLS)
