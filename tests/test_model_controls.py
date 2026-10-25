import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot.agent_model import ToolModel
from bot.chat_model import ChatConfig, ChatModel
from bot.klee_ai import KleeAI
from bot.model_budget import ModelBudget
from bot.model_controls import ModelSelection, ModelControls


class ModelControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.chat = ChatModel(ChatConfig('openai','luna-test-key','gpt-6-luna',None,60))
        self.chat.budget = ModelBudget()
        self.chat._client = NS(close=AsyncMock())
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'flash-test-key'},clear=True):
            self.service = ModelSelection(self.chat,self.root)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_validated_selection_persists_no_secrets_and_old_snapshot_is_pinned(self):
        pinned = ToolModel(self.chat).pin()
        self.service.probe = AsyncMock()
        await self.service.select('flash')
        self.assertEqual(self.chat.config.provider,'deepseek')
        self.assertEqual(self.chat.config.api_key,'flash-test-key')
        self.assertEqual(self.chat.config.base_url,'https://api.deepseek.com')
        saved = self.service.path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(saved),{'selection':'flash'})
        self.assertNotIn('test-key',saved)
        old_history,new_history = [],[]
        pinned.result(old_history,'id',{'ok':True})
        ToolModel(self.chat).result(new_history,'id',{'ok':True})
        self.assertEqual(old_history[0]['type'],'function_call_output')
        self.assertEqual(new_history[0]['role'],'tool')
        self.assertIs(pinned.chat.budget,self.chat.budget)
        fresh = ChatModel(ChatConfig('openai','luna-test-key','gpt-6-luna',None,60))
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'flash-test-key'},clear=True):
            ModelSelection(fresh,self.root)
        self.assertEqual(fresh.config.model,'deepseek-flash')

    async def test_failed_target_or_failed_save_leaves_current_config_and_budget(self):
        original = self.chat.config
        async def failed(candidate):
            candidate.budget.set_basic(True,'target_quota')
            raise RuntimeError('target failed')
        self.service.probe = failed
        with self.assertRaises(RuntimeError):
            await self.service.select('flash')
        self.assertEqual(self.chat.config,original)
        self.assertFalse(self.chat.budget.state()[0])
        self.assertFalse(self.service.path.exists())
        self.service.probe = AsyncMock()
        with patch.object(self.service,'save',side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                await self.service.select('flash')
        self.assertEqual(self.chat.config,original)

    async def test_missing_key_does_not_fall_back_to_other_provider_key(self):
        with patch.dict(os.environ,{},clear=True):
            service = ModelSelection(self.chat,self.root)
        service.probe = AsyncMock()
        with self.assertRaisesRegex(ValueError,'密钥'):
            await service.select('flash')
        service.probe.assert_not_awaited()

    async def test_owner_only_and_status_does_not_probe_or_expose_credentials(self):
        self.service.probe = AsyncMock()
        controls = ModelControls(self.service)
        interaction = NS(user=NS(id=2),response=NS(send_message=AsyncMock(),defer=AsyncMock()),followup=NS(send=AsyncMock()))
        with patch.dict(os.environ,{'KLEE_OWNER_IDS':'1'},clear=True):
            await controls.switch.callback(controls,interaction,'flash')
            self.service.probe.assert_not_awaited()
            interaction.followup.send.assert_not_awaited()
            interaction.user.id=1
            await controls.switch.callback(controls,interaction,None)
        self.service.probe.assert_not_awaited()
        self.assertNotIn('test-key',interaction.followup.send.call_args.args[0])

    async def test_switches_are_serialized_and_active_target_skips_probe(self):
        calls=[]
        async def probe(candidate):
            calls.append(candidate.config.provider)
            await asyncio.sleep(0)
        self.service.probe = probe
        await asyncio.gather(self.service.select('flash'),self.service.select('luna'))
        self.assertEqual(calls,['deepseek','openai'])
        self.assertEqual(self.chat.config.provider,'openai')
        await self.service.select('luna')
        self.assertEqual(len(calls),2)

    async def test_inflight_reply_finishes_on_old_backend_after_switch(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def create(**kwargs):
            entered.set()
            await release.wait()
            return NS(status='completed',output_text='old reply')
        self.chat._client=NS(responses=NS(create=create))
        pending=asyncio.create_task(self.chat.generate(instructions='persona',text='hello'))
        await entered.wait()
        self.service.probe=AsyncMock()
        await self.service.select('flash')
        release.set()
        self.assertEqual(await pending,'old reply')
        response=NS(choices=[NS(message=NS(content='new reply'))])
        self.chat._client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response))))
        self.assertEqual(await self.chat.generate(instructions='persona',text='hello'),'new reply')

    async def test_probe_uses_target_protocol_and_records_usage(self):
        response = NS(choices=[NS(finish_reason='stop',message=NS(content='OK'))],usage=NS(prompt_tokens=8,completion_tokens=1))
        create = AsyncMock(return_value=response)
        candidate = ChatModel(self.service.profiles['flash'])
        candidate._client = NS(chat=NS(completions=NS(create=create)))
        await self.service.probe(candidate)
        self.assertEqual(create.call_args.kwargs['model'],'deepseek-flash')
        self.assertEqual(create.call_args.kwargs['extra_body'],{'thinking':{'type':'disabled'}})
        self.assertEqual(self.chat.budget._usage['requests'],1)

    async def test_facade_web_and_prompt_model_follow_switch_without_changing_images(self):
        with patch.dict(os.environ,{'CHAT_PROVIDER':'openai','CHAT_API_KEY':'chat-test','OPENAI_API_KEY':'image-test'},clear=True):
            ai=KleeAI()
        self.assertTrue(ai.web_search)
        ai.chat_model.config=self.service.profiles['flash']
        self.assertFalse(ai.web_search)
        self.assertEqual(ai.novelai_prompt_model,'deepseek-flash')
        self.assertEqual(ai.api_key,'image-test')
        ai.chat_model.config=self.service.profiles['luna']
        self.assertTrue(ai.web_search)
        self.assertEqual(ai.novelai_prompt_model,'gpt-6-luna')
