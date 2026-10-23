import json
import os
from pathlib import Path
from datetime import datetime, timezone
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from bot.conversation import ConversationStore
from bot.fact_memory import FactMemory, identity, food_plan
from bot.diagnostics import DiagnosticStore, scrub
from bot.diagnostic_controls import DiagnosticControls
from bot.agent_store import AgentStore
from bot.spam_guard import SpamStore
from bot.moderation import ModerationError
from scripts.develop import collect_context, generate_proposal


class MemoryRegression(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name)/'memory.db'
        ConversationStore(path)
        self.model = NS(enabled=False,generate=AsyncMock())
        self.memory = FactMemory(path,self.model)

    def tearDown(self):
        self.tmp.cleanup()

    async def ask(self,text,channel=10,guild=100,user=1,name='M1zuki'):
        return await self.memory.handle(channel_id=channel,guild_id=guild,user_id=user,user_name=name,message=text)

    async def test_ranked_disclosure_shared_scope_alias_and_isolation(self):
        result = await self.ask('我第一喜欢吃拉面，第二喜欢吃咖喱')
        self.assertIn('拉面',result)
        self.assertIn('咖喱',result)
        self.assertIn('咖喱',await self.ask('我第二喜欢吃什么',channel=11))
        await self.ask('我的别名是 mizuki')
        self.assertIn('拉面',await self.ask('mizuki喜欢吃什么',channel=11,user=2,name='Bob'))
        self.assertNotIn('拉面',await self.ask('我最喜欢吃什么',channel=12,guild=200))
        self.assertNotIn('拉面',await self.ask('我最喜欢吃什么',channel=13,guild=None))
        self.model.generate.assert_not_called()

    async def test_legacy_conflict_requires_confirmation_and_forget_cannot_revive(self):
        p = identity(1,'M1zuki')
        for c,value in [(10,'拉面'),(11,'寿司')]:
            self.memory.register(c,[p])
            self.memory.execute(c,1,[('set',p,'food.favorite.1',value)])
        self.memory.map_channels([(10,100),(11,100)])
        self.assertIn('冲突',await self.ask('我最喜欢吃什么'))
        await self.ask('我最喜欢吃咖喱')
        self.assertIn('咖喱',await self.ask('我最喜欢吃什么',channel=11))
        self.memory.execute('guild:100',1,[('forget',p,'food.favorite.1',None)])
        self.assertIn('已经删除',await self.ask('我最喜欢吃什么',channel=11))

    async def test_channel_history_clear_preserves_legacy_and_shared_personal_memory(self):
        from bot.conversation import ConversationService
        p=identity(1,'M1zuki')
        self.memory.execute(10,1,[('set',p,'food.favorite.1','拉面')])
        recent=ConversationStore(self.memory.path)
        recent.append_turn(10,'M1zuki','hello','hi')
        await ConversationService(None,recent,self.memory).clear(10,guild_id=100)
        self.assertEqual(recent.context(10),[])
        self.assertIn('拉面',await self.ask('我最喜欢吃什么'))

    def test_local_parser_rejects_quoted_negative_duplicate_and_other_person(self):
        for text in ['他说我喜欢吃拉面','我不喜欢吃拉面','我喜欢吃“拉面”','他第三喜欢吃吧唧','我第一喜欢吃拉面，第一喜欢吃咖喱']:
            self.assertIsNone(food_plan(text),text)

    async def test_legacy_deletion_wins_over_an_old_channel_copy(self):
        p=identity(1,'M1zuki')
        self.memory.execute(10,1,[('set',p,'food.favorite.1','拉面')])
        self.memory.execute(11,1,[('forget',p,'food.favorite.1',None)])
        self.memory.map_channels([(10,100),(11,100)])
        self.assertIn('已经删除',await self.ask('我最喜欢吃什么'))

    def test_multiple_writes_roll_back_as_one_transaction_on_limit(self):
        p=identity(1,'M1zuki')
        self.memory.execute(10,1,[('set',p,f'key{n}','x') for n in range(49)])
        with self.assertRaises(ValueError):
            self.memory.execute(10,1,[('set',p,'food.favorite.1','拉面'),('set',p,'food.favorite.2','咖喱')])
        self.assertNotIn('拉面',self.memory.own_context(10,1))


class DiagnosticRegression(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.agent = NS(store=AgentStore(root/'agent.db'),control=AsyncMock())
        self.spam = NS(store=SpamStore(root/'spam.db'),configure=AsyncMock(return_value='已恢复'))
        self.diag = DiagnosticStore(root/'diag.db')
        self.actor = NS(id=1,guild_permissions=NS(administrator=True,manage_guild=True,view_audit_log=True))
        self.bot = NS(user=NS(id=9),get_channel=lambda _:None)
        self.guild = NS(id=100,owner_id=1,fetch_member=AsyncMock(return_value=self.actor))
        self.controls = DiagnosticControls(self.bot,self.diag,self.agent,self.spam)

    def tearDown(self):
        self.tmp.cleanup()

    def test_secret_redaction_persistence_and_guild_isolation(self):
        with patch.dict(os.environ,{'CHAT_API_KEY':'actual-secret-value'}):
            code = self.diag.record('查询',RuntimeError('actual-secret-value Bearer abc.def https://example.org/?token=hidden'),guild=100,channel=10)
        row = DiagnosticStore(self.diag.path).recent(100,code)[0]
        for secret in ('actual-secret-value','abc.def','token=hidden'):
            self.assertNotIn(secret,row['detail'])
        self.assertEqual(self.diag.recent(101,code),[])

    async def test_nonmanager_cannot_inspect(self):
        self.actor.guild_permissions = NS(administrator=False,manage_guild=False)
        self.actor.id=2
        with self.assertRaises(ModerationError):
            await self.controls.describe(self.guild,self.actor)

    async def test_audit_recovery_never_replays_and_requires_exact_reference(self):
        record = self.agent.store.action(100,'manager_create_channel',{'message':'123','actor':'1','channel':'10','arguments':{'name':'测试'}},'failed_or_unconfirmed')
        entry = NS(user=NS(id=9),reason='request 1234',target=NS(id=50,name='测试'),created_at=datetime.now(timezone.utc))
        async def logs(**kwargs):
            yield entry
        self.guild.audit_logs=logs
        self.assertIn('没有找到唯一',await self.controls.reconcile(self.guild,self.actor,'管理',str(record)))
        entry.reason='Klee request 123'
        self.assertIn('没有重新执行',await self.controls.reconcile(self.guild,self.actor,'管理',str(record)))
        self.assertEqual(self.agent.store.recent(100)[0]['state'],'success')
        self.assertIn('不会重复执行',await self.controls.reconcile(self.guild,self.actor,'管理',str(record)))
        self.agent.control.assert_not_called()


class DevelopmentRegression(unittest.TestCase):
    def test_context_is_bounded_and_prioritizes_requested_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'bot').mkdir()
            (root/'bot/music.py').write_text('x=1\n',encoding='utf-8')
            (root/'bot/aaa.py').write_text('a'*200,encoding='utf-8')
            with patch('scripts.develop.subprocess.check_output',return_value='bot/aaa.py\nbot/music.py\n.env\n'):
                result=collect_context(root,'修复播放',limit=80)
            self.assertEqual(list(result['files']),['bot/music.py'])
            self.assertNotIn('.env',result['omitted_files'])

    def test_openai_completed_only_and_no_edits_to_omitted_files(self):
        prompt={'files':{'bot/a.py':'x=1'},'omitted_files':['bot/b.py'],'task':'fix'}
        content=json.dumps({'edits':[{'path':'bot/a.py','old':'x=1','new':'x=2'}]})
        response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':content}]}]}
        with patch.dict(os.environ,{'DEVELOPER_PROVIDER':'openai','DEVELOPER_API_KEY':'test-key'}),patch('scripts.develop.request_json',return_value=response) as request:
            self.assertEqual(generate_proposal(prompt)['edits'][0]['new'],'x=2')
            self.assertFalse(request.call_args.args[2]['store'])
            response['status']='incomplete'
            with self.assertRaises(ValueError):
                generate_proposal(prompt)
