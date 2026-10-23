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
from bot.main import bot,Moderation,GuildAgent,BudgetControls,MathCommands,FileCommands,file_service,conversations,klee_ai,DATA_DIR,get_player
async def main():
    for cog in (Moderation(bot),GuildAgent(bot,klee_ai.chat_model,DATA_DIR,player_factory=get_player),BudgetControls(klee_ai.chat_model.budget),MathCommands(),FileCommands(file_service,conversations)):
        await bot.add_cog(cog)
    commands={c.name:c for c in bot.tree.get_commands()}
    equivalents={'timeout':'禁言','untimeout':'解除禁言','qiuqian':'求签'}
    missing=[c.name for c in bot.commands if equivalents.get(c.name,c.name) not in commands]
    options={name:[{'name':p.display_name,'type':p.type.value,'choices':[choice.value for choice in p.choices]} for p in commands[name].parameters] for name in ['读文件','生成文件']}
    print(json.dumps({'missing':missing,'names':list(commands),'options':options},ensure_ascii=False))
    await bot.close()
asyncio.run(main())
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'DISCORD_TOKEN': 'test-only', 'KLEE_DATA_DIR': directory, 'PYTHONIOENCODING': 'utf-8'}
            result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['missing'], [])
        for name in ('开发','更新','更新状态','清除聊天','读文件','生成文件','公式','额度状态'):
            self.assertIn(name, state['names'])
        self.assertEqual(state['options']['读文件'][0]['type'], 11)  # native Attachment option
        formats = next(option['choices'] for option in state['options']['生成文件'] if option['name'] == '格式')
        self.assertEqual(set(formats), {'txt','md','csv','json','pdf','docx','xlsx'})


if __name__ == '__main__':
    unittest.main()
