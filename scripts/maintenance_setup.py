"""Interactive local setup; never prints or uploads credentials."""
import argparse
import getpass
from pathlib import Path
import re


def main():
    from dotenv import dotenv_values, set_key
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    config = args.repo / '.env.local'
    if not config.exists():
        raise ValueError('Run start-local.cmd once to configure Discord first.')
    values = dotenv_values(config)
    print('Configure local maintenance. Blank keeps existing values. Chat keys are preserved.')
    owner = input('Your Discord user ID (comma-separated for multiple owners): ').strip() or values.get('KLEE_OWNER_IDS', '')
    if not owner or not all(part.strip().isdecimal() for part in owner.split(',')):
        raise ValueError('Enter your numeric Discord user ID.')
    repo = input('GitHub repository [M1zukiii/Kleebot]: ').strip() or values.get('KLEE_GITHUB_REPO') or 'M1zukiii/Kleebot'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Repository must be owner/repo.')
    print('GitHub token: fine-grained, this repo only, Actions: write.')
    print('Create it at https://github.com/settings/personal-access-tokens/new')
    token = getpass.getpass('GitHub Actions token (hidden): ').strip() or values.get('KLEE_GITHUB_ACTIONS_TOKEN', '')
    if not token or any(c in token for c in '\r\n\0'):
        raise ValueError('A one-line GitHub token is required.')
    for key, value in {'KLEE_OWNER_IDS': owner, 'KLEE_GITHUB_REPO': repo,
                       'KLEE_GITHUB_ACTIONS_TOKEN': token}.items():
        set_key(config, key, value, quote_mode='always')
    print('Saved locally. Token was not uploaded.')
    print('GitHub still needs the merged workflow and KLEE_DEVELOPER_API_KEY Actions secret.')
    print(f'Secrets: https://github.com/{repo}/settings/secrets/actions')
    print('See LOCAL_MAINTENANCE.md, then launch start-maintained.cmd.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Setup failed: {type(exc).__name__}: {exc}')
        raise SystemExit(1)
