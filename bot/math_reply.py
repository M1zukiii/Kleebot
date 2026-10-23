"""Local formula formatting for Discord; never invokes a TeX executable."""
import asyncio
from dataclasses import dataclass
from io import BytesIO
import math
import re
from threading import Lock

import discord
from discord.ext import commands


_LOCK = Lock()
_TOKENS = re.compile(
    r'(?P<link>\[[^\]\n]*\]\([^\n]*?\))|(?P<url>https?://[^\s<>]+)|'
    r'(?P<fence>```(?P<lang>[^\n`]*)\n(?P<code>.*?)```)|'
    r'(?P<inline_code>`[^`\n]*`)|'
    r'(?P<display>\$\$(?P<display_body>.*?)\$\$)|'
    r'(?P<bracket>\\\[(?P<bracket_body>.*?)\\\])|'
    r'(?P<paren>\\\((?P<paren_body>.*?)\\\))|'
    r'(?P<dollar>(?<![\\$])\$(?!\$)(?P<dollar_body>[^$\n]+?)(?<!\\)\$(?!\$))',
    re.S,
)
_SYMBOLS = {'pi': 'π', 'alpha': 'α', 'beta': 'β', 'theta': 'θ', 'Delta': 'Δ',
            'times': '×', 'cdot': '·', 'pm': '±', 'leq': '≤', 'geq': '≥', 'neq': '≠', 'infty': '∞', 'approx': '≈'}
_SUPER = str.maketrans('0123456789+-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻')
_SUB = str.maketrans('0123456789+-', '₀₁₂₃₄₅₆₇₈₉₊₋')


@dataclass
class MathReply:
    text: str
    images: list[bytes]


def simple_formula(expression):
    if len(expression) > 120 or '\n' in expression:
        return None
    value = re.sub(r'\\([A-Za-z]+)', lambda m: _SYMBOLS.get(m[1], m[0]), expression)
    value = re.sub(r'\^(?:\{([0-9+-]+)\}|([0-9]))', lambda m: (m[1] or m[2]).translate(_SUPER), value)
    value = re.sub(r'_(?:\{([0-9+-]+)\}|([0-9]))', lambda m: (m[1] or m[2]).translate(_SUB), value)
    if re.search(r'[\\{}_^$`]', value):
        return None
    return value


def render_formula(expression):
    if not 1 <= len(expression) <= 600 or expression.count('{') > 50 or '$' in expression:
        raise ValueError('Formula is too large or malformed')
    with _LOCK:
        import matplotlib
        from matplotlib.font_manager import FontProperties
        from matplotlib.mathtext import MathTextParser, math_to_image
        from PIL import Image, ImageOps

        expression = expression.replace(r'\dfrac', r'\frac').replace(r'\tfrac', r'\frac')
        expression = expression.replace(r'\displaystyle', '').strip()
        source = '$' + expression + '$'
        prop = FontProperties(size=18)
        # Measure before raster allocation to bound generated image dimensions.
        with matplotlib.rc_context({'text.usetex': False, 'mathtext.fontset': 'dejavusans'}):
            parsed = MathTextParser('path').parse(source, dpi=160, prop=prop)
            if not all(math.isfinite(v) for v in (parsed.width, parsed.height)) or parsed.width > 1800 or parsed.height > 500:
                raise ValueError('Formula image exceeds size limit')
            buffer = BytesIO()
            math_to_image(source, buffer, prop=prop, dpi=160, format='png', color='#172033')
        buffer.seek(0)
        with Image.open(buffer) as image:
            padded = ImageOps.expand(image.convert('RGB'), border=18, fill='white')
            output = BytesIO()
            padded.save(output, format='PNG')
            return output.getvalue()


def format_math_reply(text, *, can_attach=True):
    images = []
    fallback = False

    def convert(match):
        nonlocal fallback
        if match['inline_code'] or match['link'] or match['url']:
            return match[0]
        if match['fence']:
            if match['lang'].strip().lower() not in {'math', 'latex', 'tex'}:
                return match[0]
            expression = match['code'].strip()
        else:
            expression = next(match[name] for name in ('display_body', 'bracket_body', 'paren_body', 'dollar_body') if match[name] is not None).strip()
        # Avoid treating currency amounts as inline formulas.
        if match['dollar'] and not re.search(r'\\[A-Za-z]+|[_^=+*/<>]|^[A-Za-zα-ωΑ-Ω]$', expression):
            return match[0]
        simple = simple_formula(expression)
        if simple is not None:
            return simple
        if can_attach and len(images) < 4:
            try:
                images.append(render_formula(expression))
                return f'【公式 {len(images)}，见附图】'
            except Exception as exc:
                print(f'formula render fallback: {type(exc).__name__}', flush=True)
        fallback = True
        # Keep unsupported or excess formulas intact and copyable.
        return '\n```latex\n' + expression.replace('```', '` ` `') + '\n```\n'

    formatted = _TOKENS.sub(convert, text)
    if fallback:
        formatted += '\n（未能转成图片的公式已保留原文。）'
    return MathReply(formatted, images)


async def send_math_reply(message, text):
    can_attach = not message.guild or message.channel.permissions_for(message.guild.me).attach_files
    formatted = await asyncio.to_thread(format_math_reply, text, can_attach=can_attach)
    # Split oversized text without truncating formulas, code blocks or source links.
    chunks, current = [], ''
    for line in re.findall(r'```[\s\S]*?```|[^\n]+\n?|\n', formatted.text):
        if len(current) + len(line) > 1900:
            if current:
                chunks.append(current)
            current = ''
        current += line
    if current:
        chunks.append(current)
    chunks = chunks or ['Klee 把公式放在附图里啦。']
    files = [discord.File(BytesIO(data), filename=f'formula-{i}.png') for i, data in enumerate(formatted.images, 1)]
    try:
        for index, chunk in enumerate(chunks):
            await message.reply(chunk, files=files if index == 0 else [], allowed_mentions=discord.AllowedMentions.none())
    finally:
        for file in files:
            file.close()


class MathCommands(commands.Cog):
    @commands.hybrid_command(name='公式', description='将数学公式转成图片或易读文字，不消耗 AI 额度。')
    async def formula(self, ctx, *, expression: str):
        if ctx.interaction:
            await ctx.defer()
        expression = expression.strip()
        wrapped = expression if expression.startswith(('$', r'\(', r'\[', '```')) else r'\[' + expression + r'\]'
        await send_math_reply(ctx, wrapped)
