"""Owner-only validated model selection, with no credentials in persisted state."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .chat_model import ChatConfig, ChatModel
from .maintenance import is_owner
from .model_budget import ModelBudget


TARGETS = {'luna':('openai','gpt-6-luna'), 'flash':('deepseek','deepseek-flash')}


class ModelSelection:
    def __init__(self, chat, data_dir):
        self.chat = chat
        self.path = Path(data_dir) / 'model_selection.json'
        self.lock = asyncio.Lock()
        initial = chat.config
        self.profiles = {}
        for name,(provider,model) in TARGETS.items():
            if initial.provider == provider:
                config = replace(initial,model=model)
            elif provider == 'deepseek':
                config = ChatConfig(provider,(os.getenv('DEEPSEEK_API_KEY') or '').strip(),model,
                    os.getenv('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com',initial.timeout)
            else:
                config = ChatConfig(provider,(os.getenv('OPENAI_CHAT_API_KEY') or os.getenv('OPENAI_API_KEY') or '').strip(),model,
                    os.getenv('OPENAI_CHAT_BASE_URL') or os.getenv('OPENAI_BASE_URL') or None,initial.timeout)
            self.profiles[name] = config
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            config = self.profiles[saved['selection']]
            if config.api_key:
                self.chat.config, self.chat._client = config, None
        except (OSError,ValueError,TypeError,KeyError):
            pass

    def status(self):
        config = self.chat.config
        text = f'当前聊天模型：{config.model}（{config.provider}）。'
        text += '\n可切换：'+ '；'.join(model+('：密钥已配置' if self.profiles[name].api_key else '：缺少密钥') for name,(_,model) in TARGETS.items())
        search=os.getenv('KLEE_WEB_SEARCH','true').strip().lower() in {'1','true','yes','on'}
        text += '\n联网搜索：'+('开启' if search else '关闭')+'。'
        if self.chat.budget.state()[0]:
            text += '\n目前仍处于基础模式；切换模型不会自动恢复AI调用，请使用 /恢复完整。'
        return text

    async def probe(self, candidate):
        config = candidate.config
        if config.provider == 'deepseek':
            response = await candidate.completion(usage_kind='model_check',model=config.model,
                messages=[{'role':'user','content':'Reply with OK only.'}],max_tokens=16,
                extra_body={'thinking':{'type':'disabled'}})
            choice = response.choices[0]
            if choice.finish_reason != 'stop' or not choice.message.content:
                raise ValueError('连接测试没有返回完整文字。')
        else:
            response = await candidate.response(usage_kind='model_check',model=config.model,
                instructions='Reply with OK only.',input='OK',max_output_tokens=32,reasoning={'effort':'none'})
            if response.status != 'completed' or not response.output_text:
                raise ValueError('连接测试没有返回完整文字。')
        self.chat.budget.record(response,config.model,'model_check')

    def save(self, selection):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'selection':selection}),encoding='utf-8')
        temporary.replace(self.path)

    async def select(self, selection):
        if selection not in self.profiles:
            raise ValueError('请选择 Luna 或 DeepSeek Flash。')
        async with self.lock:
            config = self.profiles[selection]
            if not config.api_key:
                raise ValueError('目标模型尚未配置密钥，当前模型保持不变。')
            if config == self.chat.config:
                await asyncio.to_thread(self.save,selection)
                return '已经在使用这个模型。\n'+self.status()
            candidate = ChatModel(config)
            # A failed target's quota must not pause the still-working current model.
            candidate.budget = ModelBudget()
            committed = False
            try:
                await asyncio.wait_for(self.probe(candidate),timeout=25)
                await asyncio.to_thread(self.save,selection)
                self.chat.config, self.chat._client = config, candidate._client
                committed = True
            finally:
                if not committed and candidate._client is not None:
                    await candidate._client.close()
            return '连接验证通过，已切换并保存，重启后保留。\n'+self.status()


class ModelControls(commands.Cog):
    def __init__(self, selection):
        self.selection = selection

    @app_commands.command(name='切换模型',description='所有者切换全机器人聊天模型；不填查看状态，切换前会做一次小额连接测试。')
    @app_commands.rename(model='模型')
    @app_commands.describe(model='选择GPT-6 Luna或DeepSeek Flash；不填只查看当前模型。')
    @app_commands.choices(model=[app_commands.Choice(name='GPT-6 Luna',value='luna'),app_commands.Choice(name='DeepSeek Flash',value='flash')])
    async def switch(self, interaction:discord.Interaction, model:str|None=None):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message('只有配置的Klee所有者可以切换模型。',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            text = self.selection.status() if model is None else await self.selection.select(model)
        except ValueError as exc:
            text = str(exc)
        except Exception as exc:
            print('model switch failed: '+type(exc).__name__,flush=True)
            text = '目标模型验证或保存失败，本次没有切换。请检查密钥、余额及网络。'
        await interaction.followup.send(text,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
