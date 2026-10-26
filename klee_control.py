"""Small local Klee start/stop control window; never reads API keys."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.klee-local'
PRIVATE.mkdir(exist_ok=True)
HIDDEN = subprocess.CREATE_NO_WINDOW


def processes():
    # Return only IDs; command lines and credentials never enter UI or logs.
    quoted = str(ROOT).replace("'", "''")
    command = "$r='" + quoted + "'; @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -like '*local_supervisor.py*' -and $_.CommandLine.Contains($r) } | Select-Object ProcessId,ParentProcessId) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15, creationflags=HIDDEN)
    if result.returncode:
        raise RuntimeError('无法读取运行状态')
    value = json.loads(result.stdout.strip() or '[]')
    return value if isinstance(value, list) else [value]


def connected():
    now = time.time()
    for path in PRIVATE.glob('ready-*'):
        try:
            stamp = float(path.read_text(encoding='utf-8'))
            if 0 <= now - stamp < 45:
                return True
        except (OSError, ValueError):
            pass
    return False


class Panel:
    def __init__(self, window):
        self.window = window
        self.busy = False
        self.checking = False
        self.running = False
        self.events = []
        self.event_lock = threading.Lock()
        window.title('Klee 控制台')
        window.geometry('470x375')
        window.resizable(False, False)
        window.configure(bg='#f5f3ee')
        tk.Label(window, text='Klee', font=('Microsoft YaHei UI', 28, 'bold'), fg='#b54b3f', bg='#f5f3ee').pack(pady=(24, 0))
        tk.Label(window, text='你的火花骑士，随时待命', font=('Microsoft YaHei UI', 10), fg='#716c65', bg='#f5f3ee').pack(pady=(0, 20))
        self.status = tk.Label(window, text='● 正在检查…', font=('Microsoft YaHei UI', 13, 'bold'), bg='#f5f3ee', fg='#716c65')
        self.status.pack()
        self.detail = tk.Label(window, text=' ', font=('Microsoft YaHei UI', 9), bg='#f5f3ee', fg='#716c65', wraplength=420)
        self.detail.pack(pady=(5, 17))
        row = tk.Frame(window, bg='#f5f3ee')
        row.pack()
        self.start_button = tk.Button(row, text='启动 Klee', command=lambda: self.action('start'), width=15, font=('Microsoft YaHei UI', 11), bg='#b54b3f', fg='white', relief='flat', pady=9)
        self.start_button.pack(side='left', padx=8)
        self.stop_button = tk.Button(row, text='关闭 Klee', command=lambda: self.action('stop'), width=15, font=('Microsoft YaHei UI', 11), bg='#e6e2d9', fg='#403e39', relief='flat', pady=9)
        self.stop_button.pack(side='left', padx=8)
        tk.Button(window, text='填写配置 / 密钥', command=self.configure, font=('Microsoft YaHei UI', 9), relief='flat', bg='#f5f3ee', fg='#716c65').pack(pady=(12, 0))
        tk.Label(window, text='关闭这个窗口，Klee 仍会在后台运行。', font=('Microsoft YaHei UI', 9), fg='#817b73', bg='#f5f3ee').pack(pady=(19, 0))
        window.after(50, self.tick)

    def post(self, kind, value):
        with self.event_lock:
            self.events.append((kind, value))

    def configure(self):
        path = ROOT / '.env.local'
        if not path.is_file():
            messagebox.showinfo('Klee', '请先双击 install-windows.cmd 完成安装。', parent=self.window)
            return
        subprocess.Popen(['notepad.exe', str(path)])

    def scan(self):
        try:
            found = processes()
            self.post('state', (bool(found), connected() if found else False))
        except Exception:
            self.post('scan_error', None)
        finally:
            self.checking = False

    def action(self, name):
        if self.busy:
            return
        self.busy = True
        self.start_button.config(state='disabled')
        self.stop_button.config(state='disabled')
        self.status.config(text='● 正在启动…' if name == 'start' else '● 正在关闭…', fg='#9b7132')
        self.detail.config(text='正在连接 Discord，请稍候。' if name == 'start' else '正在停止 Klee 和后台启动程序。')
        threading.Thread(target=self.perform, args=(name,), daemon=True).start()

    def perform(self, name):
        try:
            found = processes()
            if name == 'start':
                if not found:
                    from dotenv import dotenv_values
                    token = dotenv_values(ROOT / '.env.local').get('DISCORD_TOKEN', '')
                    if not token or token == 'put-your-token-here':
                        raise RuntimeError('请先点击「填写配置 / 密钥」，填写 DISCORD_TOKEN 并保存，再启动。')
                    python = ROOT / '.venv-local' / 'Scripts' / 'python.exe'
                    supervisor = ROOT / 'scripts' / 'local_supervisor.py'
                    if not python.is_file() or not supervisor.is_file():
                        raise RuntimeError('启动文件缺失，请检查 Klee 文件夹。')
                    env = os.environ.copy()
                    env['PYTHONIOENCODING'] = 'utf-8'
                    with (PRIVATE / 'desktop-launch.log').open('w', encoding='utf-8') as out, (PRIVATE / 'desktop-launch.err.log').open('w', encoding='utf-8') as err:
                        child = subprocess.Popen([str(python), '-u', str(supervisor), '--repo', str(ROOT), '--full'], cwd=ROOT, env=env, stdout=out, stderr=err, creationflags=HIDDEN | subprocess.CREATE_NEW_PROCESS_GROUP)
                    for _ in range(90):
                        if child.poll() is not None:
                            raise RuntimeError('启动失败，请让助手检查启动日志。')
                        if connected():
                            break
                        time.sleep(1)
                    else:
                        raise RuntimeError('连接时间较长，Klee 仍在后台尝试。请稍后查看状态。')
            else:
                ids = {p['ProcessId'] for p in found}
                for process in found:
                    if process['ParentProcessId'] not in ids:
                        subprocess.run(['taskkill.exe', '/PID', str(process['ProcessId']), '/T', '/F'], capture_output=True, timeout=20, creationflags=HIDDEN)
                if processes():
                    raise RuntimeError('关闭未完成，请稍后再试。')
            self.post('done', None)
        except Exception as exc:
            self.post('error', str(exc))

    def tick(self):
        with self.event_lock:
            events, self.events = self.events, []
        for kind, value in events:
            if kind == 'state' and not self.busy:
                self.running, online = value
                self.status.config(text='● 已连接 Discord' if online else ('● 正在连接 / 重连' if self.running else '● 已关闭'), fg='#36774e' if online else ('#9b7132' if self.running else '#817b73'))
                self.detail.config(text='Klee 正在后台运行。' if online else ('启动程序正在运行，请稍候。' if self.running else '点击「启动 Klee」即可上线。'))
                self.start_button.config(state='disabled' if self.running else 'normal')
                self.stop_button.config(state='normal' if self.running else 'disabled')
            elif kind in ('done', 'error'):
                self.busy = False
                if kind == 'error':
                    messagebox.showerror('Klee', value, parent=self.window)
            elif kind == 'scan_error' and not self.busy:
                self.status.config(text='● 暂时无法读取状态', fg='#9b7132')
                self.start_button.config(state='normal')
                self.stop_button.config(state='normal')
        if not self.checking and not self.busy:
            self.checking = True
            threading.Thread(target=self.scan, daemon=True).start()
        self.window.after(2000, self.tick)


def main():
    window = tk.Tk()
    panel = Panel(window)
    if '--check-ui' in sys.argv:
        window.update()
        assert window.winfo_width() == 470
        assert panel.start_button.cget('text') == '启动 Klee'
        assert panel.stop_button.cget('text') == '关闭 Klee'
        window.destroy()
        print('UI layout check passed')
        return
    import msvcrt
    lock = (PRIVATE / 'control-panel.lock').open('a+b')
    lock.seek(0)
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        messagebox.showinfo('Klee', '控制台已经打开，请从任务栏切换到 Klee 控制台。', parent=window)
        window.destroy()
        return
    window.mainloop()
    lock.close()


if __name__ == '__main__':
    main()
