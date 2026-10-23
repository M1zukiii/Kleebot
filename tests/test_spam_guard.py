from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from bot.moderation import ModerationError, ModerationReceipt
from bot.spam_guard import SpamGuard, SpamStore, fingerprint, violation


class SpamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.guild = SimpleNamespace(id=10, owner_id=1)
        permissions = SimpleNamespace(administrator=False, moderate_members=True, manage_guild=True)
        self.owner = SimpleNamespace(id=1, guild=self.guild, guild_permissions=permissions, top_role=20, bot=False)
        self.bot_member = SimpleNamespace(id=3, guild=self.guild, guild_permissions=permissions, top_role=10, bot=True)
        self.target = SimpleNamespace(id=2, guild=self.guild, guild_permissions=SimpleNamespace(administrator=False),
                                      top_role=1, bot=False, is_timed_out=lambda: False, timeout=AsyncMock())
        members = {m.id:m for m in (self.owner, self.bot_member, self.target)}
        self.guild.fetch_member = AsyncMock(side_effect=lambda uid: members[uid])
        self.guild.me = self.bot_member
        self.channel = SimpleNamespace(id=50, guild=self.guild, send=AsyncMock(),
            permissions_for=lambda _: SimpleNamespace(view_channel=True, read_message_history=True, send_messages=True, embed_links=True))
        self.bot = SimpleNamespace(user=SimpleNamespace(id=3))
        self.guard = SpamGuard(self.bot, Path(self.tmp.name))
        self.guard.store.configure(10,1,True)
        self.messages = {}
        self.channel.fetch_message = AsyncMock(side_effect=lambda mid:self.messages[mid])

    def tearDown(self):
        self.tmp.cleanup()

    def message(self, mid, stamp, content=None):
        msg = SimpleNamespace(id=mid, guild=self.guild, channel=self.channel, author=self.target,
            content=str(mid) if content is None else content, attachments=[], mentions=[],
            created_at=datetime.fromtimestamp(stamp, timezone.utc))
        self.messages[mid] = msg
        return msg

    def burst(self, base=100, same=False):
        now = time.time()
        messages = [self.message(base+i, now-8+i, 'same' if same else None) for i in range(5 if same else 8)]
        rows = [{'id':m.id, 'stamp':m.created_at.timestamp(), 'hash':fingerprint(m)} for m in messages]
        return messages[-1], rows

    def seed(self, count):
        for n in range(count):
            msg=self.message(900+n, time.time()-100)
            self.guard.store.reserve(msg,1)
            self.guard.store.finish(msg.id,'applied')
        with self.guard.store.connect() as db:
            db.execute('UPDATE penalties SET stamp=?', (time.time()-100,))

    async def test_burst_without_mention_directly_times_out_and_announces(self):
        message, rows = self.burst()
        with patch('bot.spam_guard.apply_timeout', wraps=__import__('bot.spam_guard',fromlist=['apply_timeout']).apply_timeout) as apply:
            self.assertTrue(await self.guard.enforce(message,self.guard.store.policy(10),rows))
        self.target.timeout.assert_awaited_once()
        self.assertEqual(apply.await_args.args[5],1)
        self.assertIn('60秒',self.channel.send.await_args.kwargs['embed'].fields[2].value)
        self.assertEqual(self.channel.send.await_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        self.assertEqual(self.guard.store.strikes(10,2)[0][0],1)

    async def test_escalation_persists_caps_and_expires_after_24h(self):
        for count, expected in ((1,5),(2,15),(3,60),(5,60)):
            with self.guard.store.connect() as db:
                db.execute('DELETE FROM penalties')
            self.seed(count)
            self.guard.store = SpamStore(self.guard.store.path)
            message, rows = self.burst(100+count*10)
            receipt = ModerationReceipt(2,1,expected,'spam',datetime.now(timezone.utc))
            with patch('bot.spam_guard.apply_timeout', new=AsyncMock(return_value=receipt)) as apply:
                await self.guard.enforce(message,self.guard.store.policy(10),rows)
                self.assertEqual(apply.await_args.args[5],expected)
        with self.guard.store.connect() as db:
            db.execute('UPDATE penalties SET stamp=?', (time.time()-86401,))
        self.assertEqual(self.guard.store.strikes(10,2),[])

    async def test_rate_repeat_slow_chat_replays_and_channel_isolation(self):
        now=time.time()-1
        for n in range(7):
            self.assertFalse(self.guard.collect(self.message(n,now-8+n),1))
        self.assertEqual(len(self.guard.collect(self.message(8,now-1),1)),8)
        self.assertFalse(self.guard.collect(self.message(8,now-1),1))
        self.guard.buffers.clear()
        for n in range(4):
            self.assertFalse(self.guard.collect(self.message(n,now-16+n*4,'重复'),1))
        self.assertTrue(self.guard.collect(self.message(5,now,'重复'),1))
        self.assertFalse(violation([{'stamp':n*11,'hash':str(n)} for n in range(8)]))
        self.guard.buffers.clear()
        for n in range(10):
            msg=self.message(100+n,now-1)
            msg.channel=SimpleNamespace(id=100+n)
            self.assertFalse(self.guard.collect(msg,1))

    async def test_edited_evidence_revoked_permission_and_pause_reject(self):
        message, rows=self.burst()
        self.messages[rows[0]['id']].content='edited'
        with self.assertRaises(ModerationError):
            await self.guard.enforce(message,self.guard.store.policy(10),rows)
        self.target.timeout.assert_not_awaited()
        self.owner.guild_permissions.manage_guild=False
        self.owner.guild_permissions.moderate_members=False
        with self.assertRaises(ModerationError):
            await self.guard.enforce(message,self.guard.store.policy(10),rows)
        self.assertFalse(self.guard.store.policy(10)['enabled'])

    async def test_unknown_timeout_result_pauses_and_never_retries(self):
        message, rows=self.burst()
        with patch('bot.spam_guard.apply_timeout', new=AsyncMock(side_effect=TimeoutError())) as apply:
            with self.assertRaises(TimeoutError):
                await self.guard.enforce(message,self.guard.store.policy(10),rows)
            self.assertFalse(await self.guard.observe(message))
            apply.assert_awaited_once()
        self.assertTrue(self.guard.store.interrupted(10))
        self.assertEqual(self.guard.store.strikes(10,2),[])

    async def test_disabled_bot_and_private_messages_do_nothing(self):
        message,_=self.burst()
        self.guard.store.configure(10,1,False)
        self.assertFalse(await self.guard.observe(message))
        message.guild=None
        self.assertFalse(await self.guard.observe(message))
        self.target.timeout.assert_not_awaited()

    async def test_observe_uses_no_model_and_applies_only_one_penalty(self):
        message,rows=self.burst()
        for row in rows[:-1]:
            self.assertFalse(await self.guard.observe(self.messages[row['id']]))
        self.assertTrue(await self.guard.observe(message))
        self.assertFalse(await self.guard.observe(message))
        self.target.timeout.assert_awaited_once()
