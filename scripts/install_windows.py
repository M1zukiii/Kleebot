"""Idempotent Windows setup: dependencies, blank credentials and desktop panel."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


def create_config(root):
    """Never replace or rewrite existing settings, even when they are blank."""
    path = root / '.env.local'
    template = '''# 在等号后手动填写，保存后从控制台启动 Klee。
# 必填：Discord 机器人 Token（开发者门户 Bot 页面）。
DISCORD_TOKEN=
# 可选：服务器 ID，可加快 / 指令同步；主人 Discord ID 用于管理指令。
GUILD_ID=
KLEE_OWNER_IDS=
COMMAND_PREFIX=!
# 聊天服务：openai 或 deepseek，填写对应密钥即可。
CHAT_PROVIDER=openai
CHAT_MODEL=
CHAT_API_KEY=
CHAT_BASE_URL=
CHAT_TIMEOUT=60
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING=false
DEEPSEEK_REASONING_EFFORT=low
KLEE_WEB_SEARCH=true
KLEE_WEB_MAX_CALLS=2
KLEE_CONTEXT_MESSAGES=6
KLEE_CONTEXT_CHARS=900
# 生图服务及可选密钥。
IMAGE_PROVIDER=openai
NOVELAI_TOKEN=
NOVELAI_AVOID_ANLAS=true
# 自维护修复需要你自己的 GitHub 授权及仓库 Actions 配置。
KLEE_GITHUB_REPO=M1zukiii/Kleebot
KLEE_GITHUB_ACTIONS_TOKEN=
KLEE_DATA_DIR=data/local-test
YTDLP_CACHE_DIR=data/local-test/cache
YTDLP_COOKIES=data/local-test/cookies.txt
'''
    try:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(template)
        return True
    except FileExistsError:
        return False


def prepare(root, run=subprocess.run):
    requirements = root / 'requirements.txt'
    if not requirements.is_file() or not (root / 'klee_control.py').is_file():
        raise ValueError('请完整解压仓库后再运行安装程序。')
    environment = root / '.venv-local'
    python = environment / 'Scripts' / 'python.exe'
    if not python.is_file():
        print('Creating the local Python environment...', flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / 'klee-requirements.sha256'
    print('Installing/checking all dependencies (chat, music, files, images)...', flush=True)
    run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], cwd=root, check=True)
    print('Checking voice, FFmpeg, documents and control panel...', flush=True)
    check = (
        'import tkinter, discord, nacl, yt_dlp, openai, httpx, dotenv; '
        'import matplotlib, pypdf, docx, openpyxl, reportlab; '
        'import novelai_python; '
        'from imageio_ffmpeg import get_ffmpeg_exe; '
        'import subprocess; '
        'subprocess.run([get_ffmpeg_exe(), "-version"], '
        'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)'
    )
    run([str(python), '-c', check], cwd=root, check=True)
    marker.write_text(digest, encoding='utf-8')
    (root / 'data' / 'local-test' / 'cache').mkdir(parents=True, exist_ok=True)
    created = create_config(root)
    return python, created


def shortcuts(root, run=subprocess.run):
    # Root paths travel through the environment, never through shell interpolation.
    env = os.environ.copy()
    env['KLEE_INSTALL_ROOT'] = str(root)
    command = r'''
$ErrorActionPreference='Stop'
$root=$env:KLEE_INSTALL_ROOT
$wsh=New-Object -ComObject WScript.Shell
$desktop=[Environment]::GetFolderPath('Desktop')
$link=$wsh.CreateShortcut((Join-Path $desktop 'Klee 控制台.lnk'))
$link.TargetPath=Join-Path $root '.venv-local\Scripts\pythonw.exe'
$link.Arguments='"'+(Join-Path $root 'klee_control.py')+'"'
$link.WorkingDirectory=$root
$link.Description='启动、关闭 Klee，查看状态和填写配置'
$link.Save()
'''
    run(['powershell.exe', '-NoProfile', '-Command', command], env=env, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--no-launch', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This installer supports Windows only.')
    root = args.root.resolve()
    python, created = prepare(root)
    shortcuts(root)
    private = root / '.klee-local'
    private.mkdir(exist_ok=True)
    (private / 'installation.json').write_text(json.dumps({'complete': True, 'python': str(python), 'config_created': created}), encoding='utf-8')
    print('Installed. Open the desktop Klee panel, fill configuration, then click Start.', flush=True)
    print('Existing configuration and data were preserved.', flush=True)
    if not args.no_launch:
        subprocess.Popen([str(python.with_name('pythonw.exe')), str(root / 'klee_control.py')], cwd=root)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Installation failed ({type(exc).__name__}). Fix the error above and retry.', file=sys.stderr)
        raise SystemExit(1)
