from io import BytesIO
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from bot.math_reply import format_math_reply, render_formula, send_math_reply


class MathTests(unittest.IsolatedAsyncioTestCase):
    def test_bare_acid_dissociation_formula_and_inline_code_are_rendered(self):
        expression = r'\alpha_{\mathrm{A^-}}=\frac{K_a}{K_a+[\mathrm{H^+}]}'
        for source in (expression, '`' + expression + '`', '解离分数：' + expression + '。这表示酸根的比例。'):
            with self.subTest(source=source):
                result = format_math_reply(source)
                self.assertEqual(len(result.images), 1)
                self.assertNotIn('\\frac', result.text)
        result = format_math_reply('解离分数：' + expression + '。这表示酸根的比例。')
        self.assertIn('。这表示酸根的比例。', result.text)

    def test_bare_formula_detection_preserves_code_links_and_windows_paths(self):
        for source in ('```python\nformula = r"\\frac{1}{2}"\n```', r'https://example.org/\alpha', r'C:\alpha\test.txt'):
            self.assertEqual(format_math_reply(source).text, source)

    def test_simple_formula_keeps_readable_unicode_and_no_image(self):
        result = format_math_reply(r'圆的方程：\(x^{2}+y^{2}=r^{2}\)，\(\pi\approx 3.14\)。')
        self.assertIn('x²+y²=r²', result.text)
        self.assertIn('π≈ 3.14', result.text)
        self.assertEqual(result.images, [])

    def test_complex_formula_is_real_readable_png(self):
        result = format_math_reply(r'答案是：\[x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}\]。')
        self.assertIn('公式 1', result.text)
        self.assertEqual(len(result.images), 1)
        with Image.open(BytesIO(result.images[0])) as image:
            self.assertEqual(image.format, 'PNG')
            self.assertGreater(image.width, 100)
            self.assertLessEqual(image.width, 1900)
            self.assertGreater(image.height, 40)

    def test_delimiters_and_math_fence_work_but_code_currency_and_links_stay_intact(self):
        source = r'`$x^2$`，花费 $5 和 $10。[来源](https://example.org/$path)' + '\n```python\nvalue = "$x^2$"\n```'
        self.assertEqual(format_math_reply(source).text, source)
        for wrapped in (r'$\frac{1}{2}$', r'$$\frac{1}{2}$$', r'\(\frac{1}{2}\)', r'\[\frac{1}{2}\]', '```latex\n\\frac{1}{2}\n```'):
            self.assertEqual(len(format_math_reply(wrapped).images), 1)

    def test_unsupported_and_attachment_denial_keep_source(self):
        for source, can_attach in [(r'\[\unknowncommand{x}\]', True), (r'\[\frac{1}{2}\]', False)]:
            result = format_math_reply(source, can_attach=can_attach)
            self.assertIn('```latex', result.text)
            self.assertIn('保留原文', result.text)
            self.assertEqual(result.images, [])

    def test_render_count_and_expression_size_are_bounded(self):
        result = format_math_reply(r'\[\frac{1}{2}\]' * 6)
        self.assertEqual(len(result.images), 4)
        self.assertEqual(result.text.count('```latex'), 2)
        with self.assertRaises(ValueError):
            render_formula('x' * 601)

    async def test_sender_attaches_image_without_mention_notifications(self):
        message = NS(guild=None, reply=AsyncMock())
        await send_math_reply(message, r'结果：\[\int_0^1 x^2\,dx=\frac{1}{3}\]')
        args = message.reply.await_args
        self.assertEqual(len(args.kwargs['files']), 1)
        self.assertEqual(args.kwargs['allowed_mentions'].to_dict()['parse'], [])

    async def test_sender_without_attachment_permission_still_replies(self):
        message = NS(guild=NS(me=NS()), channel=NS(permissions_for=Mock(return_value=NS(attach_files=False))), reply=AsyncMock())
        await send_math_reply(message, r'\[\frac{1}{2}\]')
        self.assertEqual(message.reply.await_args.kwargs['files'], [])
        self.assertIn('\\frac{1}{2}', message.reply.await_args.args[0])


if __name__ == '__main__':
    unittest.main()
