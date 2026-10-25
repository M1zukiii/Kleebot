import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

import discord

from bot.conversation import ConversationService, ConversationStore
from bot.reply_context import read_reply_context


class ReplyContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.permissions = NS(view_channel=True,read_message_history=True)
        self.guild = NS(id=1,me=NS(id=3))
        self.channel = NS(id=5,permissions_for=lambda _:self.permissions,fetch_message=AsyncMock())
        self.target = NS(id=10,guild=self.guild,channel=self.channel,author=NS(id=3,display_name='Klee'),content='上一句的实际内容',embeds=[],attachments=[])
        self.channel.fetch_message.return_value = self.target
        self.message = NS(guild=self.guild,channel=self.channel,author=NS(id=7),mentions=[],reference=NS(message_id=10,channel_id=5,guild_id=1))

    async def test_fetches_actual_target_and_keeps_author(self):
        self.message.reference.resolved = NS(content='旧缓存')
        result = await read_reply_context(self.message,3)
        self.assertTrue(result.to_bot)
        self.assertIn('上一句的实际内容',result.text)
        self.assertIn('Klee (ID:3)',result.text)
        self.assertNotIn('旧缓存',result.text)
        self.channel.fetch_message.assert_awaited_once_with(10)

    async def test_unrelated_cached_reply_does_not_fetch_or_trigger(self):
        self.message.reference.resolved = NS(author=NS(id=8))
        result = await read_reply_context(self.message,3)
        self.assertFalse(result.to_bot)
        self.assertFalse(result.text)
        self.channel.fetch_message.assert_not_awaited()

    async def test_other_channel_and_missing_history_permission_never_fetch(self):
        self.message.reference.channel_id = 999
        result = await read_reply_context(self.message,3)
        self.assertIn('未读取',result.text)
        self.message.reference.channel_id = 5
        self.permissions.read_message_history = False
        result = await read_reply_context(self.message,3)
        self.assertIn('权限',result.text)
        self.channel.fetch_message.assert_not_awaited()

    async def test_missing_or_deleted_target_is_explained(self):
        self.channel.fetch_message.side_effect = discord.NotFound(NS(status=404,reason='not found'), 'deleted')
        result = await read_reply_context(self.message,3)
        self.assertFalse(result.to_bot)
        self.assertIn('不能猜测',result.text)
        self.message.reference = None
        self.assertEqual((await read_reply_context(self.message,3)).text,'')

    async def test_bounded_embeds_and_attached_images_and_no_forward_read(self):
        self.target.content = ''
        self.target.embeds = [discord.Embed(title='公式',description='x² = 4')]
        self.target.attachments = [NS(filename='formula.png',content_type='image/png',url='https://cdn.discordapp.com/test.png')]
        result = await read_reply_context(self.message,3)
        self.assertIn('x² = 4',result.text)
        self.assertEqual(len(result.images),1)
        self.target.content = 'a'*10000
        result = await read_reply_context(self.message,3)
        self.assertLess(len(result.text),3300)
        self.assertIn('截断',result.text)
        self.message.reference.type = discord.MessageReferenceType.forward
        self.assertFalse((await read_reply_context(self.message,3)).text)

    async def test_quoted_text_reaches_ai_but_is_not_saved_as_users_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            ai = NS(reply=AsyncMock(return_value='解释'))
            memory = NS(handle=AsyncMock(),own_context=lambda *args:'')
            service = ConversationService(ai,ConversationStore(Path(directory)/'chat.db'),memory)
            await service.reply(channel_id=5,user_id=7,user_name='用户',message='为什么',reply_context='别人：我最喜欢拉面')
            memory.handle.assert_not_awaited()
            self.assertIn('别人：我最喜欢拉面','\n'.join(ai.reply.call_args.kwargs['context']))
            self.assertEqual(ai.reply.call_args.kwargs['message'],'为什么')


class ReplyIntegrationTests(unittest.TestCase):
    def test_reply_without_ping_reaches_chat_with_target_context(self):
        script = '''
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import bot.main as m
async def main():
    bot_user=NS(id=3,mention='<@3>')
    m.bot._connection.user=bot_user
    guild=NS(id=1,me=bot_user)
    channel=NS(id=5,permissions_for=lambda _:NS(view_channel=True,read_message_history=True))
    target=NS(id=10,author=NS(id=3,display_name='Klee'),guild=guild,channel=channel,content='原消息提到的解释',embeds=[],attachments=[])
    channel.fetch_message=AsyncMock(return_value=target)
    msg=NS(id=11,author=NS(id=7,bot=False,display_name='用户',name='user',global_name=None),guild=guild,channel=channel,
           reference=NS(message_id=10,channel_id=5,guild_id=1),mentions=[],content='为什么呢',attachments=[],reply=AsyncMock())
    m.afk_store.clear_afk=lambda *args:False
    m.video_metadata_for_prompt=AsyncMock(return_value=[])
    m.file_service=NS(read_attachments=AsyncMock(return_value=''),document_context=lambda *args:'',basic_extract=AsyncMock(return_value=False),send_generated=AsyncMock(return_value=False))
    m.conversations.reply=AsyncMock(return_value='回答')
    m.bot.process_commands=AsyncMock()
    m.send_math_reply=AsyncMock()
    await m.on_message(msg)
    args=m.conversations.reply.call_args.kwargs
    assert args['message']=='为什么呢'
    assert '原消息提到的解释' in args['reply_context']
    m.send_math_reply.assert_awaited_once()
    m.conversations.reply.reset_mock()
    target.author.id=8
    msg.reference.resolved=target
    await m.on_message(msg)
    m.conversations.reply.assert_not_awaited()
    print('reply integration verified')
    await m.bot.close()
asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ,'DISCORD_TOKEN':'test-only','KLEE_DATA_DIR':directory,'PYTHONIOENCODING':'utf-8'}
            result = subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('reply integration verified',result.stdout)
