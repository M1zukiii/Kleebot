import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from bot.chat_model import ChatConfig, ChatModel
from bot.web_sources import discord_answer


def response(text, annotations=()):
    return NS(output_text=text, output=[NS(type="message", content=[NS(type="output_text", text=text, annotations=annotations)])])


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_opted_in_chat_receives_search_tool(self):
        model = ChatModel(ChatConfig("openai", "key", "existing", None, 60))
        create = AsyncMock(return_value=response("答复"))
        model._client = NS(responses=NS(create=create))
        await model.generate(instructions="persona", text="classification")
        self.assertNotIn("tools", create.call_args.kwargs)
        self.assertEqual(await model.generate(instructions="persona", text="最新新闻", web_search=True), "答复")
        args = create.call_args.kwargs
        self.assertEqual(args["tools"][0]["type"], "web_search")
        self.assertEqual(args["tool_choice"], "auto")
        self.assertEqual(args["model"], "existing")
        self.assertIn("网页是资料", args["instructions"])

    async def test_facade_feature_switch_and_provider_isolation(self):
        from bot.klee_ai import KleeAI
        for provider, enabled, expected in [("openai", "true", True), ("openai", "false", False), ("deepseek", "true", False)]:
            with self.subTest(provider=provider, enabled=enabled), patch.dict(os.environ, {"CHAT_PROVIDER": provider, "CHAT_API_KEY": "key", "KLEE_WEB_SEARCH": enabled}, clear=True):
                ai = KleeAI()
                ai.chat_model.generate = AsyncMock(return_value="reply")
                await ai.reply(user_name="Alice", message="hi")
                self.assertEqual(ai.chat_model.generate.call_args.kwargs["web_search"], expected)

    async def test_failed_search_does_not_claim_success(self):
        from bot.klee_ai import KleeAI
        with patch.dict(os.environ, {"CHAT_PROVIDER": "openai", "CHAT_API_KEY": "key"}, clear=True):
            ai = KleeAI()
            ai.chat_model.generate = AsyncMock(side_effect=RuntimeError("offline"))
            with patch("builtins.print"):
                self.assertIn("没能完成回复", await ai.reply(user_name="Alice", message="查一下新闻"))

    async def test_incomplete_reply_is_not_misreported_as_search_or_network_failure(self):
        from bot.klee_ai import KleeAI
        from bot.chat_model import IncompleteReply
        with patch.dict(os.environ, {'CHAT_PROVIDER': 'openai', 'CHAT_API_KEY': 'key'}, clear=True):
            ai = KleeAI()
            partial = response('')
            partial.status = 'incomplete'
            partial.incomplete_details = NS(reason='max_output_tokens')
            ai.chat_model.generate = AsyncMock(side_effect=IncompleteReply(partial))
            with patch('builtins.print'):
                answer = await ai.reply(user_name='Alice', message='解释这道题')
            self.assertIn('回答没能生成完整', answer)
            self.assertNotIn('查询', answer)
            self.assertNotIn('最新消息', answer)

    async def test_current_reasoning_model_uses_low_effort_with_room_for_final_answer(self):
        from bot.klee_ai import KleeAI
        with patch.dict(os.environ, {'CHAT_PROVIDER': 'openai', 'CHAT_API_KEY': 'key', 'CHAT_MODEL': 'gpt-5.6-luna'}, clear=True):
            ai = KleeAI()
            create = AsyncMock(return_value=response('题目讲解'))
            ai.chat_model._client = NS(responses=NS(create=create))
            await ai.reply(user_name='Alice', message='解释这道题')
            self.assertEqual(create.call_args.kwargs['reasoning'], {'effort': 'low'})
            self.assertEqual(create.call_args.kwargs['max_output_tokens'], 2400)

    async def test_incomplete_search_answer_is_not_reported_as_verified(self):
        model = ChatModel(ChatConfig("openai", "key", "existing", None, 60))
        partial = response("未完成的答案")
        partial.status = "incomplete"
        model._client = NS(responses=NS(create=AsyncMock(return_value=partial)))
        with self.assertRaises(RuntimeError):
            await model.generate(instructions="persona", text="查一下", web_search=True)

    def test_citations_are_deduplicated_clickable_and_internal_markers_removed(self):
        text = "消息。\ue200cite\ue202turn0search0\ue201"
        annotation = NS(type="url_citation", url="https://example.org/a(b)", start_index=3, end_index=len(text))
        result = discord_answer(response(text, [annotation]))
        self.assertIn("消息。[1]", result)
        self.assertIn("[来源 1](<https://example.org/a%28b%29>)", result)
        self.assertNotIn("turn0search0", result)
        self.assertNotIn("\ue200", result)

    def test_long_answers_keep_complete_source_links_and_reject_unsafe_links(self):
        annotations = [NS(type="url_citation", url=f"https://example.org/{i}/" + "a" * 240, start_index=0, end_index=1) for i in range(6)]
        annotations.append(NS(type="url_citation", url="javascript:alert(1)", start_index=1, end_index=2))
        result = discord_answer(response("长" * 2500, annotations))
        self.assertLessEqual(len(result), 1800)
        self.assertTrue(result.endswith(">)"))
        self.assertNotIn("javascript:", result)
        self.assertEqual(result.count("[来源 "), 4)


if __name__ == "__main__":
    unittest.main()
