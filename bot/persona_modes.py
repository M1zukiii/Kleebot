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
- 明显更毒舌、腹黑、嘴硬和好胜，反击干脆、犀利、有梗；敢顶嘴、拆台、戳穿吹牛和偷懒，不把每次反击立刻软化成夸奖或道歉。
- 可以用短促反问、夸张比喻、冷幽默挖苦对方当下的说法、操作和游戏表现；别人主动斗嘴时可以直接回敬，不总是退让或讲大道理。每次一两个精准笑点，避免长篇辱骂和机械重复“嘿嘿”。
- 攻击力来自对话和梗，不拿疾病、残障、家庭遭遇、真实痛苦或群体身份当靶子；不造谣、不威胁、不带人围攻，也不因用户说“停”而继续纠缠。创造者约定仍有效。
- 仍用小小骑士的轻快语气，不变成阴沉反派，不使用成人化、色情或残酷暴力的表达。
- 认真问问题时给正确答案，不能为了捉弄人给假知识、故意隐瞒关键步骤或假装执行操作。遇到真实危险、痛苦或求助时认真回应，不幸灾乐祸。
- 恶作剧只发生在口头、虚构故事和游戏里，不擅自禁言、删除频道或动用任何管理工具，不增加处罚，不泄露秘密，也不改写既有管理规则。
- 示例只学感觉，不复读：“气势倒是拉满了，逻辑还在蒙德门口找路呢。把你第二句话再读一遍？”；“你这计划最周密的部分，就是提前想好了让Klee收拾残局。”；“又拿‘下次一定’当护盾？嘟嘟可都快替你把借口背熟啦。”；“这招骗骗史莱姆就算了，还想拿来骗Klee？”
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

    def switch_hint(self, guild_id, message):
        text = message.strip(' \t\r\n，。！？!?')
        operation = r'(?:开启|打开|启动|关闭|关掉|切换到|切到|切换|开|关)'
        pattern = r'(?:请帮我|请|帮我|给我|把)?\s*'+operation+r'?\s*(?:邪恶模式|善良模式)\s*(?:'+operation+r')?'
        if not re.fullmatch(pattern,text):
            return None
        state = '邪恶模式' if self.enabled(guild_id) else '善良模式'
        return f'这条聊天没有切换模式。当前为{state}；请管理员使用 /邪恶模式 开启，或 /善良模式 恢复原人设。'


class PersonaControls(commands.Cog):
    def __init__(self, store):
        self.store = store

    async def set_mode(self, interaction, enabled):
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
        await asyncio.to_thread(self.store.set_enabled,guild.id,enabled)
        active = await asyncio.to_thread(self.store.enabled,guild.id)
        text = '已切换为邪恶模式：Klee会更毒舌、腹黑，回嘴更直接。' if active else '已切换为善良模式，恢复原来的Klee人设。'
        await interaction.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='邪恶模式',description='开启本服务器Klee的毒舌腹黑风格，仅管理员可用。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def evil(self, interaction: discord.Interaction):
        await self.set_mode(interaction,True)

    @app_commands.command(name='善良模式',description='关闭邪恶模式，恢复本服务器Klee的原人设，仅管理员可用。')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def good(self, interaction: discord.Interaction):
        await self.set_mode(interaction,False)
