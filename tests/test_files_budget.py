import asyncio
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from bot.file_service import FileService, build_artifact, extract_document, requested_format, safe_filename, MAX_BYTES
from bot.model_budget import ModelBudget, BasicModeError
from bot.chat_model import ChatModel, ChatConfig


class FileTests(unittest.IsolatedAsyncioTestCase):
    def test_all_seven_formats_produce_real_readable_files(self):
        for extension in ('txt', 'md', 'csv', 'json', 'pdf', 'docx', 'xlsx'):
            with self.subTest(extension=extension):
                spec = {'filename': '../群规.' + extension, 'content': 'Klee 群规\n请友好交流', 'rows': [['姓名', '分数'], ['Klee', 100]]}
                if extension == 'json':
                    spec['content'] = '{"名字":"Klee","分数":100}'
                if extension == 'csv':
                    spec['content'] = '姓名,分数\nKlee,100'
                artifact = build_artifact(spec, extension)
                self.assertEqual(artifact.filename, '群规.' + extension)
                result = extract_document(artifact.filename, artifact.data)
                self.assertIn('Klee', result)
                if extension in {'pdf', 'docx'}:
                    self.assertIn('群规', result)

    def test_bad_json_and_oversized_input_are_rejected(self):
        with self.assertRaises(ValueError):
            build_artifact({'content': '{not json}'}, 'json')
        with self.assertRaises(ValueError):
            extract_document('a.txt', b'x' * (MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            extract_document('a.exe', b'code')

    def test_sheet_strings_never_become_formulas(self):
        from openpyxl import load_workbook
        artifact = build_artifact({'rows': [['content'], ['=HYPERLINK("https://example.org")']]}, 'xlsx')
        book = load_workbook(BytesIO(artifact.data))
        self.assertEqual(book.active['A2'].data_type, 's')
        book.close()

    def test_intent_formats_and_safe_names(self):
        for request, expected in [('生成一个PDF文件', 'pdf'), ('把这个PDF导出成Word文档', 'docx'), ('给我一个Excel表格', 'xlsx'), ('生成JSON文件', 'json'), ('帮我生成一个文件', 'txt'), ('解释PDF是什么', None)]:
            self.assertEqual(requested_format(request), expected)
        self.assertNotIn('/', safe_filename('../../x.exe', 'txt'))
        self.assertEqual(safe_filename('CON', 'pdf'), 'Klee_CON.pdf')

    async def test_oversize_attachments_are_not_downloaded_and_cache_is_scoped(self):
        service = FileService(NS())
        attachment = NS(filename='a.txt', content_type='text/plain', size=MAX_BYTES+1, read=AsyncMock())
        context = await service.read_attachments([attachment])
        self.assertIn('未读取', context)
        attachment.read.assert_not_awaited()
        service.document_context((1, 2, 3), '', '文件内容')
        self.assertEqual(service.document_context((1, 2, 3), '总结刚才文件', ''), '文件内容')
        self.assertEqual(service.document_context((1, 2, 4), '总结刚才文件', ''), '')
        self.assertEqual(service.document_context((1, 2, 3), '你好', ''), '')

    async def test_file_generation_calls_only_text_model_and_creates_attachment(self):
        budget = ModelBudget()
        chat = NS(budget=budget, generate=AsyncMock(return_value=json.dumps({'filename': '群规', 'content': '请友好交流', 'rows': []})))
        service = FileService(chat)
        artifact = await service.generate('生成群规TXT文件', 'txt')
        self.assertEqual(artifact.filename, '群规.txt')
        self.assertNotIn('tools', chat.generate.await_args.kwargs)
        self.assertNotIn('web_search', chat.generate.await_args.kwargs)
        self.assertIn('请友好交流', artifact.data.decode())


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_billing_exhaustion_persists_and_blocks_next_request(self):
        with tempfile.TemporaryDirectory() as directory:
            model = ChatModel(ChatConfig('openai', 'key', 'existing', None, 60))
            model.budget = ModelBudget(Path(directory) / 'usage.db')
            error = RuntimeError('no balance')
            error.code = 'credit_balance_exhausted'
            create = AsyncMock(side_effect=error)
            model._client = NS(responses=NS(create=create))
            with self.assertRaises(RuntimeError):
                await model.generate(instructions='persona', text='hello')
            self.assertTrue(ModelBudget(Path(directory) / 'usage.db').state()[0])
            with self.assertRaises(BasicModeError):
                await model.generate(instructions='persona', text='hello again')
            create.assert_awaited_once()
            self.assertFalse(model.enabled)
            model.budget.set_basic(False, 'owner_restored')
            self.assertTrue(model.enabled)

    def test_normal_rate_limits_do_not_trigger_billing_downgrade(self):
        budget = ModelBudget()
        error = RuntimeError('too fast')
        error.code = 'rate_limit_exceeded'
        self.assertFalse(budget.failed(error))
        self.assertFalse(budget.state()[0])

    def test_usage_records_real_tokens_and_search_calls_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usage.db'
            budget = ModelBudget(path)
            budget.record(NS(usage=NS(input_tokens=123, output_tokens=45), output=[NS(type='web_search_call')]), 'existing')
            summary = ModelBudget(path).summary()
            self.assertIn('输入 123', summary)
            self.assertIn('输出 45', summary)
            self.assertIn('搜索 1', summary)

    async def test_basic_mode_preserves_local_file_extraction_without_model_calls(self):
        budget = ModelBudget()
        budget.set_basic(True)
        chat = NS(budget=budget, generate=AsyncMock())
        message = NS(guild=None, reply=AsyncMock())
        service = FileService(chat)
        self.assertTrue(await service.basic_extract(message, 'Klee 群规'))
        self.assertIn('未进行 AI 分析', message.reply.await_args.args[0])
        chat.generate.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
