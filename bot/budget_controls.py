import discord
from discord.ext import commands

from .maintenance import is_owner


class BudgetControls(commands.Cog):
    def __init__(self, budget):
        self.budget = budget

    async def reply(self, ctx, text):
        await ctx.send(text, ephemeral=bool(ctx.interaction), allowed_mentions=discord.AllowedMentions.none())

    async def owner(self, ctx):
        if is_owner(ctx.author.id):
            return True
        await self.reply(ctx, '只有配置的 Klee 所有者可以查看或修改额度模式。')
        return False

    @commands.hybrid_command(name='额度状态', description='查看 Klee 模式和今日模型 token、搜索用量（仅所有者）。')
    async def status(self, ctx):
        if await self.owner(ctx):
            await self.reply(ctx, self.budget.summary())

    @commands.hybrid_command(name='基础模式', description='暂停 AI 调用，保留手动指令和本地文件处理（仅所有者）。')
    async def basic(self, ctx):
        if await self.owner(ctx):
            self.budget.set_basic(True, 'owner_enabled')
            await self.reply(ctx, 'Klee 已进入基础模式，新的 AI 请求已暂停。')

    @commands.hybrid_command(name='恢复完整', description='确认额度可用后，手动恢复 Klee AI 功能（仅所有者）。')
    async def restore(self, ctx):
        if await self.owner(ctx):
            self.budget.set_basic(False, 'owner_restored')
            await self.reply(ctx, 'Klee 已恢复完整模式；后续用量仍由提供商结算。')
