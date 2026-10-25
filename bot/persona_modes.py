"""Server-scoped style settings; never change tool or moderation permissions."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands


EVIL_STYLE = '''
当前服务器已由真实设置开启「邪恶模式」。保持Klee的身份、自称、家庭设定、事实准确性、创造者约定和所有工具权限规则；把基础人设中一味温顺、处处劝善的表达改为以下风格：
- 更腹黑、调皮、嘴硬、自信，爱吐槽、顶嘴、装无辜、得意地笑，可以记口头小仇、开小恶作剧的玩笑。
- 可以俏皮挖苦对方刚才的行为、戳穿偷懒和吹牛，不必每次夸奖、安慰、道歉或当乖孩子；轻微互怼要贴着话题，不变成人格贬低或追着骂人。
- 仍用小小骑士的轻快语气，不变成阴沉反派，不使用成人化、色情或残酷暴力的表达。
- 认真问问题时给正确答案，不能为了捉弄人给假知识、故意隐瞒关键步骤或假装执行操作。遇到真实危险、痛苦或求助时认真回应，不幸灾乐祸。
- 恶作剧只发生在口头、虚构故事和游戏里，不擅自禁言、删除频道或动用任何管理工具，不增加处罚，不泄露秘密，也不改写既有管理规则。
- 示例只学感觉，不复读：“哼，又想让Klee替你动脑筋？看好啦，这一步应该这样做……”；“这个小把戏还想骗过Klee？嘟嘟可都快看出来啦。”；“好吧，这次帮你，Klee的小本本上可记着呢。”
'''

MOTHERLY_STYLE = '''
当前用户在本条消息中直接称呼Klee为“妈妈/妈咪/麻麻”。这一条用“小小大人式的妈妈”口吻回应：爱操心、轻轻哄人、带一点管教和碎碎念，像认真照顾朋友的Klee。仍自称Klee，表达自然简短，不每句都叫乖宝宝，也不无端猜测对方年龄。
可以关心吃饭、喝水、休息、学习和眼前的问题，适度说“乖，先……”或“Klee看着你呢”；不要只剩安慰套话。邪恶模式开启时可以带一点嘴硬和小坏心眼，仍先把对方实际需要的事说明白。
这是对用户称呼的轻松角色回应，不声称真实生育、监护或亲子关系，不改写Klee自己的父母设定，不性化、恋爱化，也不要求用户只依赖Klee。用户在认真求助时给实际帮助。
示例只学语气：“乖，先把饭吃了，再来和Klee讲今天怎么啦。别想拿零食糊弄过去，Klee可看着呢。”；“唔，又卡在这一题啦？坐好，Klee陪你把第一步理清楚。”
'''


def called_mom(message):
    # Only the current sender's direct address, never quoted messages or family discussion.
    message = re.sub(r'```.*?```', '', message, flags=re.S)
    message = '\n'.join(line for line in message.splitlines() if not line.lstrip().startswith('>'))
    return bool(re.search(
        r'(?:^|[\s，,。！!？?～~])(?:Klee|可莉)?\s*(?:妈妈|妈咪|麻麻)'
        r'(?=$|[\s，,。！!？?～~]|[我你帮给抱哄陪看在来早晚])', message, re.I))


class PersonaStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS persona(guild TEXT PRIMARY KEY,evil INTEGER NOT NULL CHECK(evil IN (0,1)))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path,timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enabled(self, guild_id):
        if guild_id is None:
            return False
        with self.connect() as db:
            row = db.execute('SELECT evil FROM persona WHERE guild=?',(str(guild_id),)).fetchone()
        return bool(row and row[0])

    def set_enabled(self, guild_id, enabled):
        if guild_id is None:
            raise ValueError('服务器模式不能设置在私信中。')
        with self.connect() as db:
            db.execute('INSERT INTO persona VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET evil=excluded.evil',(str(guild_id),int(bool(enabled))))


class PersonaControls(commands.Cog):
    def __init__(self, store):
        self.store = store

    @app_commands.command(name='邪恶模式',description='切换本服务器Klee的腹黑调皮口吻；不填参数查看状态，仅管理员。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(enabled='开启')
    @app_commands.describe(enabled='开启腹黑调皮风格，关闭恢复原人设；不填只查看状态。')
    async def evil(self, interaction: discord.Interaction, enabled: bool | None = None):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            actor = await guild.fetch_member(interaction.user.id)
        except discord.HTTPException:
            await interaction.followup.send('暂时无法核实管理员身份，这次没有修改。',ephemeral=True)
            return
        permissions = actor.guild_permissions
        if actor.id != guild.owner_id and not (permissions.administrator or permissions.manage_guild):
            await interaction.followup.send('只有服务器所有者或拥有管理服务器权限的成员能切换邪恶模式。',ephemeral=True)
            return
        if enabled is not None:
            await asyncio.to_thread(self.store.set_enabled,guild.id,enabled)
        active = await asyncio.to_thread(self.store.enabled,guild.id)
        text = '邪恶模式已开启：Klee会更腹黑、爱吐槽和捉弄人。' if active else '邪恶模式已关闭，使用原来的Klee人设。'
        if enabled is None:
            text = '当前状态：'+('开启' if active else '关闭')+'；选择“开启”参数可修改。'
        await interaction.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
