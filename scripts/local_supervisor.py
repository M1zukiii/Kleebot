"""Local owner-requested main updates; keep runtime, config and data separate."""
import argparse
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen
import uuid
import venv
import zipfile


SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def python_in(environment):
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def build_environment():
    # Preparation never inherits the bot's API keys or .env.local.
    names = {'PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE',
             'LOCALAPPDATA', 'APPDATA', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}
    return {k: v for k, v in os.environ.items() if k.upper() in {n.upper() for n in names}}


def extract_source(archive, destination):
    """Bounded GitHub source archive with no links or escaping paths."""
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 5000 or sum(item.file_size for item in entries) > 100_000_000:
            raise ValueError('Source archive exceeds limits')
        roots = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            if ('\\' in item.orig_filename or ':' in item.filename or path.is_absolute()
                    or not path.parts or any(p in {'.', '..'} for p in path.parts)
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Unsafe source archive')
            roots.add(path.parts[0])
        if len(roots) != 1:
            raise ValueError('Expected one source directory')
        source.extractall(destination)
    return destination / roots.pop()


@contextmanager
def single_instance(path):
    with open(path, 'a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


class LocalSupervisor:
    def __init__(self, root, values, full=False):
        self.root = Path(root).resolve()
        self.private = self.root / '.klee-local'
        self.private.mkdir(exist_ok=True)
        configured = Path(values.get('KLEE_DATA_DIR') or 'data/local-test')
        self.data = configured if configured.is_absolute() else self.root / configured
        self.data = self.data.resolve()
        if self.private.is_relative_to(self.data) or self.data.is_relative_to(self.private):
            raise ValueError('KLEE_DATA_DIR must be outside .klee-local')
        self.data.mkdir(parents=True, exist_ok=True)
        self.repo = values.get('KLEE_GITHUB_REPO') or 'M1zukiii/Kleebot'
        if not REPO.fullmatch(self.repo):
            raise ValueError('KLEE_GITHUB_REPO must be owner/repo')
        self.full = full
        # Deep package paths (notably NumPy) exceed MAX_PATH below a long checkout.
        # Keep each install/revision isolated under the shorter user runtime cache.
        local_app_data = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else None
        installation = hashlib.sha256(str(self.root).encode('utf-8')).hexdigest()[:16]
        self.runtime_cache = Path(local_app_data) / 'Kleebot' / 'runtimes' / installation if local_app_data else None
        self.process = None
        self.log_thread = None
        self.ready = None
        self.started = 0
        self.active = None
        state = self.private / 'state.json'
        self.state = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {}

    def status(self, state, message, revision=None):
        atomic_json(self.data / 'update_status.json', {
            'status': state, 'revision': revision or self.active or 'local',
            'message': message, 'updated_at': time.time(), 'runtime': 'windows-local',
        })
        print(f'[Klee] {message}', flush=True)

    def save(self):
        atomic_json(self.private / 'state.json', self.state)

    def source(self, revision):
        if revision is None:
            return self.root, python_in(self.root / '.venv-local')
        if not SHA.fullmatch(revision):
            raise ValueError('Invalid revision')
        release = self.private / 'releases' / revision
        environment = self.environment(revision)
        legacy = release / 'venv'
        # Existing releases remain usable for rollback after upgrading the supervisor.
        if not python_in(environment).is_file() and python_in(legacy).is_file():
            environment = legacy
        return release / 'source', python_in(environment)

    def environment(self, revision):
        if not SHA.fullmatch(revision):
            raise ValueError('Invalid revision')
        if self.runtime_cache is not None:
            return self.runtime_cache / revision
        return self.private / 'releases' / revision / 'venv'

    def start(self, revision):
        source, python = self.source(revision)
        self.ready = self.private / ('ready-' + uuid.uuid4().hex)
        self.started = time.time()
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        # Apply runtime paths after dotenv so old absolute prompt paths cannot
        # pin the persona to the original installation.
        command = (
            'from dotenv import load_dotenv; import os,runpy; '
            'load_dotenv(' + repr(str(self.root / '.env.local')) + ', override=True); '
            'os.environ.update(' + repr({'KLEE_DATA_DIR': str(self.data),
                'KLEE_READY_FILE': str(self.ready), 'KLEE_LOCAL_SUPERVISED': '1',
                'KLEE_PROMPT_FILE': str(source / 'klee_prompt.txt')}) + '); '
            "runpy.run_module('bot.main', run_name='__main__')"
        )
        process = subprocess.Popen([str(python), '-u', '-c', command], cwd=source,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        self.process = process
        self.active = revision
        # Retain real startup errors as well as showing them in the launch window.
        def copy_log():
            with open(self.private / 'runtime.log', 'a', encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(line, end='', flush=True)
            process.stdout.close()
        self.log_thread = threading.Thread(target=copy_log, daemon=True)
        self.log_thread.start()

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            if os.name == 'nt':
                try:
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=15, check=False)
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    # The Windows venv launcher may have a real Python child.
                    # Stop only the tree created by this supervisor.
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=15, check=False)
                else:
                    self.process.kill()
                self.process.wait(timeout=10)
        if self.log_thread is not None:
            self.log_thread.join(timeout=3)
        if self.ready is not None:
            self.ready.unlink(missing_ok=True)

    def wait_ready(self, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return False
            try:
                stamp = float(self.ready.read_text(encoding='utf-8'))
                if self.started <= stamp <= time.time() and time.time() - stamp < 45:
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(1)
        return False

    def latest(self):
        request = Request(f'https://api.github.com/repos/{self.repo}/commits/main',
                          headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Kleebot-local'})
        with urlopen(request, timeout=30) as response:
            revision = json.loads(response.read(1_000_000))['sha']
        if not isinstance(revision, str) or not SHA.fullmatch(revision):
            raise ValueError('Invalid GitHub revision')
        return revision

    def prepare(self, revision):
        if not SHA.fullmatch(revision):
            raise ValueError('Invalid revision')
        release = self.private / 'releases' / revision
        if release.exists():
            # This is a supervisor-owned directory derived from a validated SHA.
            if release.resolve().parent != (self.private / 'releases').resolve():
                raise ValueError('Invalid release directory')
            shutil.rmtree(release)
        release.mkdir(parents=True)
        archive = release / 'source.zip'
        with urlopen(f'https://codeload.github.com/{self.repo}/zip/{revision}', timeout=60) as response:
            with archive.open('wb') as output:
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > 30_000_000:
                        raise ValueError('Download exceeds limits')
                    output.write(chunk)
        extracted = extract_source(archive, release)
        source = release / 'source'
        extracted.rename(source)
        # Refuse the old main before the maintenance framework is merged.
        if not (source / 'scripts/local_supervisor.py').is_file():
            raise ValueError('main 尚未包含 Windows 自维护，请先合并自维护更新')
        environment = self.environment(revision)
        intended_parent = self.runtime_cache if self.runtime_cache is not None else release
        if environment.resolve().parent != intended_parent.resolve():
            raise ValueError('Invalid runtime directory')
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = python_in(environment)
        env = build_environment()
        requirements = source / ('requirements.txt' if self.full else 'requirements-local.txt')
        with open(self.private / 'prepare.log', 'a', encoding='utf-8') as log:
            for args in ([str(python), '-m', 'pip', 'install', '-r', str(requirements)],
                         [str(python), '-m', 'compileall', '-q', 'bot', 'tests'],
                         [str(python), '-m', 'unittest', 'discover', '-s', 'tests', '-v']):
                subprocess.run(args, cwd=source, env=env, check=True, timeout=900,
                               stdout=log, stderr=subprocess.STDOUT)

    def backup(self):
        destination = self.private / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        shutil.copytree(self.data, destination, ignore=shutil.ignore_patterns('cache', 'update_request.json'))
        return destination

    def update(self):
        previous = self.active
        revision = None
        stopped = False
        try:
            self.status('checking', '正在检查 main；当前 Klee 继续运行。')
            revision = self.latest()
            if revision == previous:
                self.status('current', '当前已是 main 最新版。')
                return
            self.status('building', '正在准备并测试新版；当前 Klee 继续运行。', revision)
            self.prepare(revision)
            self.stop()
            stopped = True
            backup = self.backup()
            self.status('deploying', f'测试通过，已备份到 {backup.name}，正在重启。', revision)
            self.start(revision)
            if not self.wait_ready():
                raise RuntimeError('新版未在 120 秒内连接 Discord')
            self.state.update(revision=revision, previous=previous)
            self.save()
            self.status('updated', '新版已连接 Discord，更新完成。', revision)
        except Exception as exc:
            if stopped:
                self.stop()
                self.start(previous)
                if not self.wait_ready():
                    self.status('rollback_failed', '新版和旧版都无法上线；请检查 runtime.log，记忆备份在 backups 中。', previous)
                    raise RuntimeError('Rollback failed') from exc
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            self.status('failed', f'更新失败（{detail}）；旧版继续运行。日志在 .klee-local；如有数据结构变更，请从备份恢复。', revision)

    def run(self):
        with single_instance(self.private / 'supervisor.lock'):
            try:
                active = self.state.get('revision')
                self.start(active)
                if not self.wait_ready():
                    raise RuntimeError('Klee 未连接 Discord，请查看 .klee-local/runtime.log')
                self.status('ready', 'Windows 自维护已启动。!更新 检查 main；!更新状态 查看结果。')
                crashes = []
                while True:
                    if self.process.poll() is not None:
                        now = time.monotonic()
                        crashes = [stamp for stamp in crashes if now - stamp < 600]
                        if len(crashes) >= 3:
                            raise RuntimeError('10 分钟内多次退出，已停止自动重启，请查看 runtime.log')
                        crashes.append(now)
                        self.status('restarting', 'Klee 意外退出，正在重新启动。')
                        time.sleep(5)
                        self.stop()
                        self.start(self.active)
                        if not self.wait_ready():
                            raise RuntimeError('自动重启未连接 Discord，请查看 runtime.log')
                        self.status('ready', 'Klee 已重新连接 Discord。')
                    request = self.data / 'update_request.json'
                    if request.exists():
                        request.unlink()
                        self.update()
                    time.sleep(2)
            finally:
                self.stop()


def main():
    from dotenv import dotenv_values
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    supervisor = LocalSupervisor(args.repo, dotenv_values(args.repo / '.env.local'), args.full)
    try:
        supervisor.run()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        supervisor.status('stopped', f'自维护已停止：{type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
