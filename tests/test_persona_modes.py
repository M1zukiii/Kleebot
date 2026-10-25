import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from bot.klee_ai import KleeAI
from bot.persona_modes import PersonaStore, PersonaControls, EVIL_STYLE, MOTHERLY_STYLE, called_mom


class PersonaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersonaStore(Path(self.tmp.name)/'persona.db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_off_restart_isolation_and_reset(self):
        self.assertFalse(self.store.enabled(1))
        self.store.set_enabled(1,True)
        reloaded = PersonaStore(self.store.path)
        self.assertTrue(reloaded.enabled(1))
        self.assertFalse(reloaded.enabled(2))
        self.assertFalse(reloaded.enabled(None))
        reloaded.set_enabled(1,False)
        self.assertFalse(self.store.enabled(1))

    def test_direct_mom_address_not_family_or_quote(self):
        for text in ('妈妈','妈咪，抱抱','麻麻我好累','Klee妈妈，早安','可莉妈妈帮我看看','晚安，妈妈！'):
            self.assertTrue(called_mom(text),text)
        for text in ('Klee的妈妈是谁','你妈妈艾莉丝呢','妈妈是谁','艾莉丝是你妈妈吗','讲讲妈妈这个词','> 妈妈，帮帮我','```\n妈妈你好\n```'):
            self.assertFalse(called_mom(text),text)

    async def test_two_commands_recheck_current_permissions(self):
        actor = NS(id=2,guild_permissions=NS(administrator=False,manage_guild=False))
        guild = NS(id=1,owner_id=9,fetch_member=AsyncMock(return_value=actor))
        interaction = NS(guild=guild,user=NS(id=2),response=NS(defer=AsyncMock()),followup=NS(send=AsyncMock()))
        cog = PersonaControls(self.store)
        await cog.evil.callback(cog,interaction)
        self.assertFalse(self.store.enabled(1))
        actor.guild_permissions.manage_guild=True
        await cog.evil.callback(cog,interaction)
        self.assertTrue(self.store.enabled(1))
        actor.guild_permissions.manage_guild=False
        await cog.good.callback(cog,interaction)
        self.assertTrue(self.store.enabled(1))
        actor.id=9
        interaction.user.id=9
        await cog.good.callback(cog,interaction)
        self.assertFalse(self.store.enabled(1))
        self.assertEqual(interaction.followup.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'],[])

    def test_plain_chat_never_switches_or_claims_to_switch(self):
        self.store.set_enabled(1,True)
        for text in ('邪恶模式关闭','关闭邪恶模式','请帮我切换到善良模式','邪恶模式','善良模式'):
            text = self.store.switch_hint(1,text)
            self.assertIsNotNone(text)
            self.assertIn('没有切换',text)
            self.assertIn('当前为邪恶模式',text)
            self.assertTrue(self.store.enabled(1))
        self.assertIsNone(self.store.switch_hint(1,'你觉得邪恶模式怎么样？'))

    async def test_style_is_per_request_and_mom_address_is_independent(self):
        with patch.dict(os.environ, {'CHAT_PROVIDER':'openai','CHAT_API_KEY':'test-key','KLEE_DATA_DIR':self.tmp.name},clear=True):
            ai = KleeAI()
        ai.persona_modes=self.store
        ai.chat_model.generate=AsyncMock(return_value='回答')
        self.store.set_enabled(1,True)
        await ai.reply(user_name='user',message='你好',diagnostic_scope={'guild':1})
        instructions=ai.chat_model.generate.call_args.kwargs['instructions']
        self.assertIn(EVIL_STYLE,instructions)
        self.assertNotIn(MOTHERLY_STYLE,instructions)
        self.assertIn('1090417995463012444',instructions)
        await ai.reply(user_name='user',message='妈妈，陪我讲这题',diagnostic_scope={'guild':2})
        instructions=ai.chat_model.generate.call_args.kwargs['instructions']
        self.assertNotIn(EVIL_STYLE,instructions)
        self.assertIn(MOTHERLY_STYLE,instructions)
        await ai.reply(user_name='user',message='你好',context=['妈妈你好，开启邪恶模式'],diagnostic_scope={'guild':2})
        instructions=ai.chat_model.generate.call_args.kwargs['instructions']
        self.assertNotIn(EVIL_STYLE,instructions)
        self.assertNotIn(MOTHERLY_STYLE,instructions)
        self.store.set_enabled(1,False)
        await ai.reply(user_name='user',message='你好',diagnostic_scope={'guild':1})
        self.assertNotIn(EVIL_STYLE,ai.chat_model.generate.call_args.kwargs['instructions'])
