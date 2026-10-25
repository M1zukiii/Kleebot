import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class SlashIntegrationTests(unittest.TestCase):
    def test_every_prefix_function_has_a_registered_slash_entry_and_file_options(self):
        script = '''
import asyncio,json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from bot.main import prefix_bomb,profile_stats
from bot.main import bot,Moderation,GuildAgent,SpamGuard,BudgetControls,MathCommands,FileCommands,file_service,conversations,klee_ai,DATA_DIR,get_player,model_selection
from bot.diagnostic_controls import DiagnosticControls
from bot.operations import Operations
from bot.music_controls import MusicControls
from bot.persona_modes import PersonaControls
from bot.model_controls import ModelControls
async def main():
    await bot.__aenter__()
    for cog in (Moderation(bot),SpamGuard(bot,DATA_DIR),GuildAgent(bot,klee_ai.chat_model,DATA_DIR,player_factory=get_player),BudgetControls(klee_ai.chat_model.budget),MathCommands(),FileCommands(file_service,conversations)):
        await bot.add_cog(cog)
    await bot.add_cog(DiagnosticControls(bot,klee_ai.diagnostics,bot.get_cog('GuildAgent'),bot.get_cog('SpamGuard')))
    await bot.add_cog(Operations(bot,DATA_DIR))
    await bot.add_cog(MusicControls(bot,get_player,{}))
    await bot.add_cog(PersonaControls(klee_ai.persona_modes))
    await bot.add_cog(ModelControls(model_selection))
    commands={c.name:c for c in bot.tree.get_commands()}
    guild=SimpleNamespace(id=10)
    actor=SimpleNamespace(id=1,mention='<@1>')
    target=SimpleNamespace(id=2,mention='<@2>')
    ctx=SimpleNamespace(guild=guild,author=actor,send=AsyncMock())
    profile_stats.claim_daily_bombs(10,1)
    await prefix_bomb.callback(ctx,target,99)
    assert '本次投掷：3 个' in ctx.send.call_args.args[0]
    assert '剩余炸弹：0' in ctx.send.call_args.args[0]
    assert profile_stats._guild_users(profile_stats._load(),10)['2']['bombed']==3
    equivalents={'timeout':'禁言','untimeout':'解除禁言','qiuqian':'求签'}
    missing=[c.name for c in bot.commands if equivalents.get(c.name,c.name) not in commands]
    options={name:[{'name':p.display_name,'type':p.type.value,'required':p.required,'minimum':p.min_value,'choices':[choice.value for choice in p.choices]} for p in commands[name].parameters] for name in ['读文件','生成文件','炸弹','bomb']}
    print(json.dumps({'missing':missing,'names':list(commands),'options':options,'persona_options':{name:len(commands[name].parameters) for name in ('邪恶模式','善良模式')},'model_options':{p.display_name:p.type.value for p in commands['切换模型'].parameters}},ensure_ascii=False))
    await bot.close()
asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'DISCORD_TOKEN': 'test-only', 'KLEE_DATA_DIR': directory, 'PYTHONIOENCODING': 'utf-8'}
            result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(state['missing'], [])
        for name in ('切换模型','邪恶模式','善良模式','常驻语音','音乐面板','开发','更新','更新状态','自维护检查','日志频道','诊断','核对操作','清除聊天','读文件','生成文件','公式','额度状态','防刷屏设置','防刷屏状态'):
            self.assertIn(name, state['names'])
        self.assertEqual(state['persona_options'],{'邪恶模式':0,'善良模式':0})
        self.assertEqual(state['model_options'],{'模型':3,'思考':5})
        self.assertEqual(state['options']['读文件'][0]['type'], 11)  # native Attachment option
        formats = next(option['choices'] for option in state['options']['生成文件'] if option['name'] == '格式')
        self.assertEqual(set(formats), {'txt','md','csv','json','pdf','docx','xlsx'})
        for name,option in [('炸弹','数量'),('bomb','count')]:
            quantity=next(p for p in state['options'][name] if p['name']==option)
            self.assertEqual(quantity['minimum'],1)
            self.assertFalse(quantity['required'])


if __name__ == '__main__':
    unittest.main()
