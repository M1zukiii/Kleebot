"""Windows/local chat bootstrap. Secrets are entered only on the user's machine."""
import argparse
import getpass
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent.parent


def dotenv_text(values):
    lines = []
    for key, value in values.items():
        if any(c in str(value) for c in '\r\n\0'):
            raise ValueError(f"{key} must fit on one line")
        escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'{key}="{escaped}"')
    return '\n'.join(lines) + '\n'


def configure(path):
    print('Discord Token and API key stay in .env.local on this computer.')
    token = getpass.getpass('Discord bot token (hidden): ').strip()
    if not token:
        raise ValueError('A Discord bot token is required.')
    guild = input('Test server ID (recommended; blank = use joined servers): ').strip()
    owner = input('Your Discord user ID (blank disables maintenance commands): ').strip()
    for value in (guild, owner):
        if value and not value.isdecimal():
            raise ValueError('Discord IDs must contain digits only.')
    provider = input('Chat: 1 = DeepSeek, 2 = OpenAI, 3 = no AI [1]: ').strip() or '1'
    if provider not in {'1', '2', '3'}:
        raise ValueError('Choose 1, 2, or 3.')
    key = getpass.getpass('Chat API key (hidden; blank = fixed fallback replies): ').strip() if provider != '3' else ''
    values = {
        'DISCORD_TOKEN': token, 'GUILD_ID': guild, 'KLEE_OWNER_IDS': owner,
        'CHAT_PROVIDER': 'openai' if provider == '2' else 'deepseek',
        'CHAT_API_KEY': key, 'CHAT_MODEL': '', 'CHAT_BASE_URL': '',
        'OPENAI_API_KEY': '', 'DEEPSEEK_API_KEY': '', 'NOVELAI_TOKEN': '',
        'KLEE_GITHUB_ACTIONS_TOKEN': '',
        'KLEE_DATA_DIR': str(ROOT / 'data' / 'local-test'),
        'KLEE_PROMPT_FILE': str(ROOT / 'klee_prompt.txt'),
        'YTDLP_CACHE_DIR': str(ROOT / 'data' / 'local-test' / 'cache'),
        'YTDLP_COOKIES': str(ROOT / 'data' / 'local-test' / 'cookies.txt'),
        'COMMAND_PREFIX': '!', 'KLEE_CONTEXT_MESSAGES': '12', 'KLEE_CONTEXT_CHARS': '2000',
    }
    path.write_text(dotenv_text(values), encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print('Saved local configuration. To change it later, edit .env.local or use --configure.')


def main():
    if sys.version_info < (3, 12):
        raise RuntimeError('Install Python 3.12 or newer from https://www.python.org/downloads/windows/')
    parser = argparse.ArgumentParser()
    parser.add_argument('--configure', action='store_true', help='Replace local test configuration')
    parser.add_argument('--full', action='store_true', help='Install optional NovelAI image dependencies too')
    args = parser.parse_args()
    os.chdir(ROOT)
    config = ROOT / '.env.local'
    if not config.exists() or args.configure:
        configure(config)
    environment = ROOT / '.venv-local'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('Creating a separate local Python environment...')
        venv.EnvBuilder(with_pip=True).create(environment)
    requirements = ROOT / ('requirements.txt' if args.full else 'requirements-local.txt')
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / 'klee-requirements.sha256'
    if not marker.exists() or marker.read_text() != digest:
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
        marker.write_text(digest)
    print('Starting Kleebot locally. Keep this window open; press Ctrl+C to stop.')
    print('Use a dedicated test Discord server. Chat does not require FFmpeg or Docker.')
    command = "from dotenv import load_dotenv; load_dotenv('.env.local', override=True); import runpy; runpy.run_module('bot.main', run_name='__main__')"
    try:
        return subprocess.call([str(python), '-c', command], cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Local startup failed ({type(exc).__name__}): {exc}', file=sys.stderr)
        raise SystemExit(1)
