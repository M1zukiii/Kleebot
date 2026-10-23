import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.chat_model import ChatConfig, ChatModel
from bot.conversation import ConversationStore, ConversationService


class ConfigTests(unittest.TestCase):
    def test_openai_default_is_gpt_6_luna(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ChatConfig.from_env().model,'gpt-6-luna')

    def test_legacy_openai_and_provider_isolation(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "old-key", "OPENAI_MODEL": "old-model"}, clear=True):
            config = ChatConfig.from_env()
            self.assertEqual((config.api_key, config.model), ("old-key", "old-model"))
            os.environ["CHAT_PROVIDER"] = "deepseek"
            config = ChatConfig.from_env()
            self.assertEqual(config.api_key, "")
            self.assertEqual(config.model, "deepseek-flash")
            self.assertEqual(config.base_url, "https://api.deepseek.com")

    def test_overrides_and_invalid_config(self):
        with patch.dict(os.environ, {"CHAT_PROVIDER": "deepseek", "CHAT_MODEL": "custom", "CHAT_API_KEY": "custom-key"}, clear=True):
            self.assertEqual(ChatConfig.from_env().model, "custom")
            os.environ["CHAT_BASE_URL"] = "bad-url"
            with self.assertRaises(ValueError):
                ChatConfig.from_env()


class StoreTests(unittest.TestCase):
    def test_restart_isolation_retention_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chat.db"
            store = ConversationStore(path, 2, 900)
            store.append_turn(1, "Alice", "old", "old reply")
            store.append_turn(1, "Alice", "new", "new reply")
            store.append_turn(2, "Bob", "private", "private reply")
            reopened = ConversationStore(path, 2, 900)
            self.assertEqual(reopened.context(1), ["Alice: new", "Klee: new reply"])
            reopened.clear(1)
            self.assertEqual(reopened.context(1), [])
            self.assertIn("Bob: private", reopened.context(2))

    def test_zero_and_character_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "chat.db", 0, 900)
            store.append_turn(1, "Alice", "hello", "hi")
            self.assertEqual(store.context(1), [])
            store.max_messages = 6
            store.max_chars = 1
            store.append_turn(1, "Alice", "hello", "hi")
            self.assertEqual(store.context(1), [])


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_payload(self):
        model = ChatModel(ChatConfig("deepseek", "key", "deepseek-flash", None, 60))
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=" hello "))]))
        model._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        self.assertEqual(await model.generate(instructions="persona", text="question", image_urls=["https://example.com/a.png"]), "hello")
        args = create.call_args.kwargs
        self.assertEqual(args["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertEqual(args["messages"][1]["content"][1]["type"], "image_url")
        self.assertEqual(args["messages"][0]["role"], "system")

    async def test_openai_payload(self):
        model = ChatModel(ChatConfig("openai", "key", "existing", None, 60))
        create = AsyncMock(return_value=SimpleNamespace(output_text=" hi "))
        model._client = SimpleNamespace(responses=SimpleNamespace(create=create))
        self.assertEqual(await model.generate(instructions="persona", text="question"), "hi")
        self.assertEqual(create.call_args.kwargs["instructions"], "persona")
        self.assertNotIn("extra_body", create.call_args.kwargs)

    async def test_serialized_turns_and_fallback_not_saved(self):
        # Service imports the facade only for its existing fallback constant.
        from bot.klee_ai import FALLBACK_REPLY
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "chat.db", 6, 2000)
            contexts = []
            async def reply(**kwargs):
                contexts.append(kwargs["context"])
                await asyncio.sleep(0)
                return "answer"
            ai = SimpleNamespace(reply=reply)
            service = ConversationService(ai, store)
            await asyncio.gather(*(service.reply(channel_id=1, user_id=42, user_name="Alice", message=str(i)) for i in range(2)))
            self.assertEqual(contexts[0], [])
            self.assertEqual(len(contexts[1]), 2)
            self.assertIn("ID:42", contexts[1][0])
            before = store.context(1)
            ai.reply = AsyncMock(return_value=FALLBACK_REPLY)
            await service.reply(channel_id=1, user_id=42, user_name="Alice", message="fail")
            self.assertEqual(store.context(1), before)
            await service.clear(1)
            self.assertEqual(store.context(1), [])


class FacadeTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_chat_keeps_openai_images_separate(self):
        from bot.klee_ai import KleeAI
        with patch.dict(os.environ, {"CHAT_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "chat-secret", "OPENAI_API_KEY": "image-secret", "OPENAI_MODEL": "old-openai", "OPENAI_BASE_URL": "https://images.example/v1"}, clear=True):
            ai = KleeAI()
            self.assertEqual(ai.api_key, "image-secret")
            self.assertEqual(ai.base_url, "https://images.example/v1")
            self.assertEqual(ai.chat_model.config.api_key, "chat-secret")
            self.assertEqual(ai.chat_model.config.base_url, "https://api.deepseek.com")
            self.assertEqual(ai.novelai_prompt_model, "deepseek-flash")
            ai.chat_model.generate = AsyncMock(return_value="reply")
            self.assertEqual(await ai.reply(user_name="Alice", message="hello"), "reply")
            summary = ai.config_summary()
            self.assertNotIn("chat-secret", summary)
            self.assertNotIn("image-secret", summary)

    async def test_missing_key_and_failed_calls_use_fallback(self):
        from bot.klee_ai import KleeAI, FALLBACK_REPLY
        with patch.dict(os.environ, {"CHAT_PROVIDER": "deepseek"}, clear=True):
            ai = KleeAI()
            self.assertEqual(await ai.reply(user_name="Alice", message="hi"), FALLBACK_REPLY)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "key", "CHAT_PROVIDER": "deepseek"}, clear=True):
            ai = KleeAI()
            ai.chat_model.generate = AsyncMock(side_effect=RuntimeError("offline"))
            with patch('builtins.print'):
                self.assertIn('诊断编号',await ai.reply(user_name="Alice", message="hi"))
