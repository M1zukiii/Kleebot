import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.diagnostics import DiagnosticStore
from bot.operations import Operations
from bot.moderation import ModerationError


class OperationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.permissions=NS(view_channel=True,send_messages=True)
        self.guild=NS(id=100,owner_id=1,me=NS(id=9))
        self.origin=NS(id=10,guild=self.guild,send=AsyncMock(),permissions_for=lambda _:self.permissions)
        self.logs=NS(id=11,guild=self.guild,send=AsyncMock(),permissions_for=lambda _:self.permissions)
        self.channels={10:self.origin,11:self.logs}
        self.bot=NS(get_channel=lambda cid:self.channels.get(cid),user=NS(id=9))
        self.ops=Operations(self.bot,self.root)
        self.ops.store.configure(100,11)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_start_shutdown_restart_no_duplicate_notices_or_cross_guild_loss(self):
        self.ops.store.configure(200,21)
        self.channels[21]=NS(id=21,guild=NS(id=200,me=NS(id=9)),send=AsyncMock(),permissions_for=lambda _:self.permissions)
        await self.ops.on_ready();await self.ops.on_ready();await self.ops.flush();await self.ops.flush()
        self.assertEqual(self.logs.send.await_count,1)
        self.assertEqual(self.channels[21].send.await_count,1)
        await self.ops.shutdown();await self.ops.shutdown()
        self.assertEqual(self.logs.send.await_count,2)
        self.assertEqual(self.ops.store.meta('running'),'0')
        reopened=Operations(self.bot,self.root)
        await reopened.flush()
        self.assertEqual(self.logs.send.await_count,2)

    async def test_crash_is_reported_on_next_start_and_mentions_are_disabled(self):
        self.ops.store.meta('running','1')
        await self.ops.on_ready();await self.ops.flush()
        call=self.logs.send.await_args
        self.assertIn('未记录正常关闭',call.args[0])
        self.assertEqual(call.kwargs['allowed_mentions'].to_dict()['parse'],[])

    async def test_original_and_log_channel_each_receive_completion_once_after_restart(self):
        rid='a'*32
        self.ops.store.track(rid,100,10,1)
        # A second concurrently dispatched run must never be mistaken for this request.
        listing={'workflow_runs':[{'id':222,'display_title':'Klee development '+'b'*32,'status':'completed','conclusion':'success'},
                                  {'id':111,'display_title':'Klee development '+rid,'status':'completed','conclusion':'failure'}]}
        self.ops.api=AsyncMock(side_effect=[listing,{'jobs':[{'name':'validate','conclusion':'failure'}]}])
        self.ops.pull_link=AsyncMock(return_value='https://github.com/M1zukiii/Kleebot/pull/22')
        await self.ops.poll_development()
        self.assertEqual(self.ops.store.pending(),[])
        await Operations(self.bot,self.root).flush()
        await self.ops.flush()
        self.assertEqual(self.origin.send.await_count,1)
        self.assertEqual(self.logs.send.await_count,1)
        text=self.origin.send.await_args.args[0]
        self.assertIn('未通过自动测试',text)
        self.assertIn('/runs/111',text)
        self.assertNotIn('/runs/222',text)

    async def test_success_is_draft_not_installed_and_known_run_needs_no_list(self):
        self.ops.store.track('a'*32,100,10,1,run=123)
        self.ops.api=AsyncMock(return_value={'id':123,'status':'completed','conclusion':'success'})
        self.ops.pull_link=AsyncMock(return_value='')
        await self.ops.poll_development();await self.ops.flush()
        self.ops.api.assert_awaited_once_with('/actions/runs/123')
        self.assertIn('尚未合并或安装',self.origin.send.await_args.args[0])

    async def test_pending_waits_and_failed_delivery_can_retry_without_cross_guild_send(self):
        self.ops.store.track('a'*32,100,10,1,run=123)
        self.ops.api=AsyncMock(return_value={'id':123,'status':'in_progress'})
        self.ops.pull_link=AsyncMock()
        await self.ops.poll_development()
        self.ops.pull_link.assert_not_awaited()
        self.ops.store.event('check',100,'one',10)
        self.permissions.send_messages=False
        await self.ops.flush()
        self.logs.send.assert_not_awaited()
        self.permissions.send_messages=True
        self.origin.guild=NS(id=999,me=NS(id=9))
        await self.ops.flush()
        self.origin.send.assert_not_awaited()
        self.logs.send.assert_awaited_once()

    async def test_log_disabled_keeps_private_request_callback_and_hides_error_details(self):
        self.ops.store.configure(100,None)
        self.ops.store.event('check',100,'finished',10)
        await self.ops.flush()
        self.origin.send.assert_awaited_once()
        self.logs.send.assert_not_awaited()
        self.ops.store.configure(100,11)
        diagnostics=DiagnosticStore(self.root/'diagnostics.sqlite3')
        diagnostics.record('查询',RuntimeError('private-chat-and-secret'),guild=100,channel=10)
        self.ops.capture_local();await self.ops.flush()
        text=' '.join(c.args[0] for c in self.logs.send.await_args_list)
        self.assertIn('诊断编号',text)
        self.assertNotIn('private-chat-and-secret',text)

    async def test_configure_requires_current_native_permission_and_rejects_foreign_channel(self):
        actor=NS(id=2,guild_permissions=NS(administrator=False,manage_guild=False))
        self.guild.fetch_member=AsyncMock(return_value=actor)
        with self.assertRaises(ModerationError):
            await self.ops.configure(self.guild,actor,self.logs)
        actor.guild_permissions.manage_guild=True
        channel=MagicMock(spec=discord.TextChannel)
        channel.guild=NS(id=200)
        with self.assertRaises(ModerationError):
            await self.ops.configure(self.guild,actor,channel)

    async def test_submission_is_correlated_without_persisting_task_text_or_credentials(self):
        ctx=NS(guild=self.guild,channel=self.origin,author=NS(id=1))
        with patch.dict(os.environ,{'KLEE_OWNER_IDS':'1'}),patch('bot.operations.request_development',AsyncMock(return_value='submitted')) as dispatch:
            result=await self.ops.submit(ctx,'private request body')
        pending=self.ops.store.pending()
        self.assertEqual(len(pending),1)
        self.assertEqual(dispatch.await_args.args[1],pending[0]['id'])
        self.assertIn(pending[0]['id'][:8],result)
        self.assertNotIn('private request body',json.dumps(pending))

    async def test_known_submit_rejection_does_not_wait_forever(self):
        ctx=NS(guild=self.guild,channel=self.origin,author=NS(id=1))
        with patch.dict(os.environ,{'KLEE_OWNER_IDS':'1'}),patch('bot.operations.request_development',AsyncMock(side_effect=RuntimeError('HTTP 403'))):
            with self.assertRaises(RuntimeError): await self.ops.submit(ctx,'task')
        self.assertEqual(self.ops.store.pending(),[])

    async def test_update_status_changes_once_and_api_failure_cannot_block_logs(self):
        (self.root/'update_status.json').write_text(json.dumps({'status':'updated','revision':'a'*40,'updated_at':1}),encoding='utf-8')
        self.ops.capture_local();self.ops.capture_local()
        await self.ops.flush()
        self.logs.send.assert_awaited_once()
        self.ops.store.event('another',100,'still delivered')
        self.ops.poll_development=AsyncMock(side_effect=RuntimeError('network down'))
        await self.ops.monitor.coro(self.ops)
        self.assertEqual(self.logs.send.await_count,2)

    async def test_repeated_api_failures_back_off_and_warn_once_without_false_success(self):
        self.ops.store.track('a'*32,100,10,1,run=123)
        self.ops.poll_development=AsyncMock(side_effect=RuntimeError('network'))
        for _ in range(4):
            self.ops.next_poll=0
            await self.ops.monitor.coro(self.ops)
        self.assertGreater(self.ops.next_poll,0)
        self.assertEqual(self.origin.send.await_count,1)
        self.assertIn('暂时无法读取',self.origin.send.await_args.args[0])
        self.assertEqual(len(self.ops.store.pending()),1)

    async def test_duplicate_correlation_is_not_guessed(self):
        rid='a'*32
        self.ops.store.track(rid,100,10,1)
        self.ops.api=AsyncMock(return_value={'workflow_runs':[{'id':i,'display_title':'Klee development '+rid} for i in (1,2)]})
        self.ops.pull_link=AsyncMock()
        await self.ops.poll_development();await self.ops.flush()
        self.ops.pull_link.assert_not_awaited()
        self.assertIn('多个执行记录',self.origin.send.await_args.args[0])
