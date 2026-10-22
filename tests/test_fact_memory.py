import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.conversation import ConversationService, ConversationStore
from bot.fact_memory import FactMemory, identity, render_mentions


class FactStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "chat.db"
        self.recent = ConversationStore(self.path)
        self.memory = FactMemory(self.path, None)
        self.alice = identity(1, "M1zuki", ["mizuki"])
        self.bob = identity(2, "JustEric")
        self.memory.register(10, [self.alice, self.bob])

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, uid, key, value):
        person = self.alice if uid == 1 else self.bob
        self.memory.execute(10, uid, [("set", person, key, value)])

    def test_independent_facts_restart_and_channel_isolation(self):
        self.put(1, "food.favorite.1", "拉面")
        self.put(1, "food.favorite.2", "咖喱")
        reopened = FactMemory(self.path, None)
        reply = reopened.execute(10, 1, [("query", self.alice, "*", None)])
        self.assertIn("拉面", reply)
        self.assertIn("咖喱", reply)
        self.assertNotIn("拉面", reopened.execute(11, 1, [("query", self.alice, "*", None)]))

    def test_forget_second_preserves_first_and_other_user(self):
        self.put(1, "food.favorite.1", "拉面")
        self.put(1, "food.favorite.2", "咖喱")
        self.put(2, "food.favorite.1", "寿司")
        self.recent.append_turn(10, "M1zuki", "我第二喜欢咖喱", "记住咖喱")
        self.memory.execute(10, 1, [("forget", self.alice, "food.favorite.2", None)])
        self.assertEqual(self.recent.context(10), [])
        result = self.memory.execute(10, 1, [("query", self.alice, "*", None)])
        self.assertIn("拉面", result)
        self.assertNotIn("咖喱", result)
        self.assertIn("寿司", self.memory.execute(10, 2, [("query", self.bob, "*", None)]))

    def test_database_boundary_blocks_forged_cross_user_delete(self):
        self.put(1, "food.favorite.2", "咖喱")
        with self.assertRaises(PermissionError):
            self.memory.execute(10, 2, [("forget", self.alice, "food.favorite.2", None)])
        self.assertIn("咖喱", self.memory.execute(10, 1, [("query", self.alice, "*", None)]))

    def test_preflight_rejects_entire_mixed_unauthorized_plan(self):
        actions = [{"op": "set", "subject": "self", "key": "food.favorite.1", "value": "寿司"},
                   {"op": "forget", "subject": "name:mizuki", "key": "food.favorite.2"}]
        with self.assertRaises(PermissionError):
            self.memory.prepare(actions, self.bob, [], self.memory.people(10), "我喜欢寿司，忘掉mizuki第二喜欢的")
        self.assertNotIn("寿司", self.memory.execute(10, 2, [("query", self.bob, "*", None)]))

    def test_alias_collisions_require_actual_mention(self):
        other = identity(3, "M1zuki", ["mizuki"])
        self.memory.register(10, [other])
        known = self.memory.people(10)
        self.assertIsNone(self.memory.resolve("name:mizuki", self.bob, [], known, "mizuki喜欢什么"))
        self.assertEqual(self.memory.resolve("id:1", self.bob, [self.alice], known, "<@1>喜欢什么")["id"], "1")
        self.assertIsNone(self.memory.resolve("id:1", self.bob, [], known, "somebody"))

    def test_no_hallucinated_fact_value(self):
        with self.assertRaises(ValueError):
            self.memory.prepare([{"op": "set", "subject": "self", "key": "food.favorite.1", "value": "披萨"}],
                                self.alice, [], self.memory.people(10), "我喜欢拉面")

    def test_real_mention_replaced_with_name_and_id(self):
        text = render_mentions("<@!2> 喜欢什么", [self.bob])
        self.assertIn("JustEric", text)
        self.assertIn("用户ID:2", text)
        self.assertNotIn("<@", text)

    def test_full_delete_is_explicit_and_does_not_affect_other_user(self):
        self.put(1, "food.favorite.1", "拉面")
        self.put(2, "food.favorite.1", "寿司")
        with self.assertRaises(ValueError):
            self.memory.prepare([{"op": "forget", "subject": "self", "key": "*"}],
                                self.alice, [], self.memory.people(10), "忘掉我第二喜欢的")
        self.memory.execute(10, 1, [("forget", self.alice, "*", None)])
        self.assertNotIn("拉面", self.memory.execute(10, 1, [("query", self.alice, "*", None)]))
        self.assertIn("寿司", self.memory.execute(10, 2, [("query", self.bob, "*", None)]))


class MemoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_channel_other_user_cannot_forget_alice(self):
        with tempfile.TemporaryDirectory() as tmp:
            recent = ConversationStore(Path(tmp) / "chat.db")
            model = SimpleNamespace(enabled=True, generate=AsyncMock())
            memory = FactMemory(recent.path, model)
            alice = identity(1, "M1zuki", ["mizuki"])
            memory.register(10, [alice])
            memory.execute(10, 1, [("set", alice, "food.favorite.1", "拉面")])
            model.generate.return_value = json.dumps({"actions": [{"op": "forget", "subject": "name:mizuki", "key": "food.favorite.1"}]})
            ai = SimpleNamespace(reply=AsyncMock(return_value="I would forget it"))
            service = ConversationService(ai, recent, memory)
            result = await service.reply(channel_id=10, user_id=2, user_name="JustEric", message="忘掉mizuki喜欢的食物")
            self.assertIn("不能改别人", result)
            ai.reply.assert_not_awaited()
            self.assertIn("拉面", memory.execute(10, 1, [("query", alice, "*", None)]))

    async def test_query_mention_uses_target_database_not_bot_persona(self):
        with tempfile.TemporaryDirectory() as tmp:
            recent = ConversationStore(Path(tmp) / "chat.db")
            bob = identity(2, "Loveroin")
            model = SimpleNamespace(enabled=True, generate=AsyncMock(return_value=json.dumps(
                {"actions": [{"op": "query", "subject": "id:2", "key": "*"}]})))
            memory = FactMemory(recent.path, model)
            memory.register(10, [bob])
            memory.execute(10, 2, [("set", bob, "food.favorite.1", "寿司")])
            ai = SimpleNamespace(reply=AsyncMock(return_value="Klee likes Dodoco"))
            service = ConversationService(ai, recent, memory)
            result = await service.reply(channel_id=10, user_id=1, user_name="M1zuki", message="<@2>喜欢什么", mentioned_users=[bob])
            self.assertIn("Loveroin", result)
            self.assertIn("寿司", result)
            self.assertNotIn("Dodoco", result)
            ai.reply.assert_not_awaited()

    async def test_invalid_plan_cannot_write_or_claim_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            recent = ConversationStore(Path(tmp) / "chat.db")
            model = SimpleNamespace(enabled=True, generate=AsyncMock(return_value="not valid JSON"))
            memory = FactMemory(recent.path, model)
            result = await memory.handle(channel_id=10, user_id=1, user_name="Alice", message="记住我喜欢拉面")
            self.assertIn("没有执行修改", result)
            self.assertNotIn("拉面", memory.own_context(10, 1))

    async def test_clear_command_removes_facts_and_recent_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            recent = ConversationStore(Path(tmp) / "chat.db")
            memory = FactMemory(recent.path, None)
            alice = identity(1, "Alice")
            memory.register(10, [alice])
            memory.execute(10, 1, [("set", alice, "food.favorite.1", "拉面")])
            recent.append_turn(10, "Alice", "hi", "hello")
            await ConversationService(None, recent, memory).clear(10)
            self.assertEqual(recent.context(10), [])
            self.assertNotIn("拉面", memory.own_context(10, 1))
