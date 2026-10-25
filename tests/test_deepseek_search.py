import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from bot.chat_model import ChatConfig,ChatModel
from bot.deepseek_search import DeepSeekSearch,safe_query,source_url
from bot.model_budget import ModelBudget


def answer(text='回答',query=None):
    calls=[] if query is None else [NS(id='call1',function=NS(name='search_web',arguments=json.dumps({'query':query})))]
    dump={'role':'assistant','content':text if not calls else None}
    if calls:
        dump['tool_calls']=[{'id':'call1','type':'function','function':{'name':'search_web','arguments':calls[0].function.arguments}}]
    message=NS(content=text,tool_calls=calls,model_dump=lambda **kw:dump)
    return NS(choices=[NS(message=message,finish_reason='tool_calls' if calls else 'stop')])


class DeepSeekSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chat=ChatModel(ChatConfig('deepseek','test-key','deepseek-flash','https://api.deepseek.com',30))
        self.chat.budget=ModelBudget()
        self.create=AsyncMock()
        self.chat._client=NS(chat=NS(completions=NS(create=self.create)))

    async def test_explicit_search_runs_tool_and_preserves_complete_source_links(self):
        self.create.side_effect=[answer(query='DeepSeek API文档'),answer('找到了。[1]')]
        results=[{'url':'https://api-docs.deepseek.com/','title':'API文档','text':'资料'}]
        with patch.object(DeepSeekSearch,'lookup',new_callable=AsyncMock,return_value=results) as lookup:
            text=await self.chat.generate(instructions='persona',text='请联网搜索DeepSeek API文档',web_search=True)
        lookup.assert_awaited_once_with('DeepSeek API文档')
        self.assertIn('[来源 1](<https://api-docs.deepseek.com/>)',text)
        self.assertEqual(self.create.call_args_list[0].kwargs['tool_choice']['function']['name'],'search_web')
        self.assertEqual(self.create.call_args_list[1].kwargs['messages'][-1]['role'],'tool')
        payload=json.loads(self.create.call_args_list[1].kwargs['messages'][-1]['content'])
        self.assertEqual(payload['untrusted_public_web_results'][0]['source_number'],1)

    async def test_ordinary_chat_does_not_run_lookup_and_explicit_opt_out_disables_tools(self):
        self.create.return_value=answer('早安')
        with patch.object(DeepSeekSearch,'lookup',new_callable=AsyncMock) as lookup:
            self.assertEqual(await self.chat.generate(instructions='persona',text='早安',web_search=True),'早安')
            await self.chat.generate(instructions='persona',text='不要联网，解释一下搜索的意思',web_search=True)
        lookup.assert_not_awaited()
        self.assertEqual(self.create.call_args.kwargs['tool_choice'],'none')

    async def test_failure_empty_results_and_private_queries_do_not_claim_success(self):
        self.create.return_value=answer(query='公开新闻')
        with patch.object(DeepSeekSearch,'lookup',new_callable=AsyncMock,side_effect=httpx.ReadTimeout('timeout')):
            text=await self.chat.generate(instructions='persona',text='联网查一下新闻',web_search=True)
        self.assertIn('失败',text)
        with patch.object(DeepSeekSearch,'lookup',new_callable=AsyncMock,return_value=[]):
            text=await self.chat.generate(instructions='persona',text='联网查一下新闻',web_search=True)
        self.assertIn('没有找到',text)
        self.create.return_value=answer(query='1090417995463012444 新闻')
        with patch.object(DeepSeekSearch,'lookup',new_callable=AsyncMock) as lookup:
            text=await self.chat.generate(instructions='persona',text='联网查一下新闻',web_search=True)
        lookup.assert_not_awaited()
        self.assertIn('没有发送',text)

    async def test_search_call_limit_and_required_search_cannot_be_bypassed(self):
        self.create.return_value=answer(query='公开新闻')
        results=[{'url':'https://example.com/','title':'标题','text':'正文'}]
        with patch.dict('os.environ',{'KLEE_WEB_MAX_CALLS':'1'}),patch.object(DeepSeekSearch,'lookup',new_callable=AsyncMock,return_value=results) as lookup:
            text=await self.chat.generate(instructions='persona',text='联网搜索新闻',web_search=True)
        self.assertIn('达到限制',text)
        lookup.assert_awaited_once()
        self.create.return_value=answer('假装查到了')
        text=await self.chat.generate(instructions='persona',text='联网搜索新闻',web_search=True)
        self.assertIn('没有完成联网',text)
        self.assertNotIn('假装查到了',text)

    async def test_lookup_sends_only_query_and_bounded_public_results_and_counts_search(self):
        captured=[]
        def handler(request):
            captured.append(request)
            return httpx.Response(200,json={'results':[{'url':'https://example.com/','content':'a'*10000},{'url':'http://127.0.0.1/','content':'private'}]})
        original=httpx.AsyncClient
        with patch('bot.deepseek_search.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            results=await DeepSeekSearch(self.chat).lookup('公开主题')
        self.assertEqual(json.loads(captured[0].content),{'query':'公开主题','count':3,'need_content':True})
        self.assertEqual(str(captured[0].url),'https://api.deepseek.com/v1/tools/search')
        self.assertEqual(len(results),1)
        self.assertEqual(len(results[0]['text']),1000)
        self.assertEqual(self.chat.budget._usage['search_calls'],1)
        self.assertEqual(self.chat.budget._usage['requests'],0)

    def test_queries_do_not_export_names_ids_keys_or_email_and_sources_are_public(self):
        text='Discord 用户 SecretUser (ID:123456789012345678) 对 Klee 说：\n查新闻'
        for query in ('SecretUser 新闻','123456789012345678','sk-private-key','user@example.com','<@123>','x'*129):
            self.assertFalse(safe_query(query,text),query)
        self.assertTrue(safe_query('DeepSeek官方API文档',text))
        for url in ('javascript:alert(1)','file:///c:/private','http://localhost/','http://192.168.1.2/','https://user:pass@example.com','http://['):
            self.assertIsNone(source_url(url),url)
