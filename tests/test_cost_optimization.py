import json
from pathlib import Path
from types import SimpleNamespace as NS
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest.mock import AsyncMock

from bot.chat_model import ChatConfig, ChatModel
from bot.mention_router import local_music_request
from bot.model_budget import ModelBudget
from bot.guild_agent import GuildAgent


class CostTests(unittest.IsolatedAsyncioTestCase):
    def test_music_fast_path_only_accepts_simple_requests(self):
        for text,expected in [('<@3> 播放雨爱','雨爱'),('<@!3> 帮我播放《雨爱》','雨爱'),('请播放 https://www.bilibili.com/video/BV123','https://www.bilibili.com/video/BV123')]:
            self.assertEqual(local_music_request(text,3),expected)
        for text in ('播放不了怎么办','播放失败为什么','播放雨爱然后删除频道','播放雨爱并且禁言某人','不要播放雨爱','怎么播放视频','播放雨爱？'):
            self.assertIsNone(local_music_request(text,3),text)

    async def test_local_playback_works_without_model_and_skips_classifier(self):
        with tempfile.TemporaryDirectory() as directory:
            bot=NS(user=NS(id=3),command_prefix='!')
            guild=NS(id=10,owner_id=1)
            author=NS(id=2,bot=False,guild_permissions=NS())
            message=NS(guild=guild,id=100,author=author,mentions=[bot.user],content='<@3> 播放雨爱',channel=NS(id=50),reply=AsyncMock())
            agent=GuildAgent(bot,NS(enabled=False),Path(directory))
            agent.router.classify=AsyncMock()
            agent.management.run=AsyncMock()
            agent.management.play_public_music=AsyncMock(return_value={'ok':True,'message':'已加入播放队列'})
            self.assertTrue(await agent.observe(message))
            agent.router.classify.assert_not_awaited()
            agent.management.run.assert_not_awaited()
            agent.management.play_public_music.assert_awaited_once_with(message,'雨爱')

    async def test_router_none_effort_is_limited_to_supported_model_and_not_sent_as_sdk_metadata(self):
        for name,expected in (('gpt-5.6-luna','none'),('gpt-6-astra','low')):
            model=ChatModel(ChatConfig('openai','key',name,None,60))
            create=AsyncMock(return_value=NS(status='completed',output_text='{}',output=[]))
            model._client=NS(responses=NS(create=create))
            await model.generate(instructions='classify',text='hello',purpose='router',reasoning_effort='none')
            self.assertEqual(create.await_args.kwargs['reasoning']['effort'],expected)
            self.assertNotIn('usage_kind',create.await_args.kwargs)
            self.assertNotIn('purpose',create.await_args.kwargs)

    def test_legacy_totals_migrate_and_detail_counts_do_not_double_count_reasoning(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'usage.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE daily_usage(day TEXT,model TEXT,requests INTEGER,input_tokens INTEGER,output_tokens INTEGER,search_calls INTEGER,PRIMARY KEY(day,model))')
                db.commit()
            budget=ModelBudget(path)
            budget.record(NS(usage=NS(input_tokens=1000,output_tokens=100,
                input_tokens_details=NS(cached_tokens=300,cache_write_tokens=200),
                output_tokens_details=NS(reasoning_tokens=40)),output=[]),'existing','router')
            budget.record(NS(usage=NS(input_tokens=50,output_tokens=10),output=[]),'existing','chat')
            reopened=ModelBudget(path)
            with reopened.connect() as db:
                self.assertEqual(db.execute('SELECT SUM(output_tokens) FROM daily_usage').fetchone()[0],110)
                self.assertEqual(db.execute('SELECT cached_tokens,cache_write_tokens,reasoning_tokens FROM usage_details WHERE purpose=?',('router',)).fetchone(),(300,200,40))
            summary=reopened.summary()
            self.assertIn('分类 1次',summary)
            self.assertIn('输出 110',summary)
            self.assertIn('缓存读取 300',summary)
            self.assertIn('缺少缓存明细：1 次',summary)

    def test_open_and_find_actions_are_not_counted_as_new_searches(self):
        budget=ModelBudget()
        budget.record(NS(usage=NS(input_tokens=1,output_tokens=1), output=[
            NS(type='web_search_call',action=NS(type='search')),
            NS(type='web_search_call',action=NS(type='open_page')),
            NS(type='web_search_call',action={'type':'find_in_page'})]),'existing','chat')
        self.assertIn('搜索 1',budget.summary())
