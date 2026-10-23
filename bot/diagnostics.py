"""Scoped, bounded failure receipts. Store no prompts, credentials or document bodies."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


def scrub(value):
    text = str(value)
    for key,secret in os.environ.items():
        if re.search(r'TOKEN|API_KEY|SECRET|PASSWORD',key,re.I) and len(secret) >= 4:
            text = text.replace(secret,'[已隐藏]')
    text = re.sub(r'(?i)(?:bearer\s+)[\w.\-]+','Bearer [已隐藏]',text)
    text = re.sub(r'\b(?:sk-[\w-]{8,}|gh[pousr]_[\w]{8,}|github_pat_[\w_]{8,})','[已隐藏]',text)
    text = re.sub(r'(?i)([?&](?:token|key|api_key|auth|signature|sign)=)[^&\s]+',r'\1[已隐藏]',text)
    profile = os.environ.get('USERPROFILE')
    if profile:
        text = text.replace(profile,'[用户目录]')
    return text[:1600]


def explanation(error):
    name = type(error).__name__
    text = str(error).lower()
    if 'join a voice channel first' in text:
        return '请先进入一个语音频道，再让Klee播放。'
    if 'missing ffmpeg' in text or 'ffmpeg' in text and ('不存在' in text or '缺少' in text):
        return '本机播放组件尚未准备好，需要管理者查看诊断。'
    if getattr(error,'status',None) == 429 or 'ratelimit' in name.lower():
        return '服务暂时限制了请求频率，请稍后重试。'
    if 'timeout' in name.lower():
        return '服务响应超时，暂时没有拿到完整结果。'
    if 'incomplete' in name.lower():
        return '回答未能生成完整，可以把问题拆成几个小问。'
    if '412' in text:
        return 'B站暂时拒绝了访问，请稍后重试或提供具体视频链接。'
    if any(word in text for word in ('initial state','unable to extract','无法解析','来源暂时')):
        return '视频来源暂时无法解析，请尝试具体视频链接。'
    if getattr(error,'status',None) in (401,403) or name in {'AuthenticationError','PermissionDeniedError'}:
        return '服务认证或访问权限检查没有通过，需要管理者查看诊断。'
    return '这次处理失败，详细原因已记录，管理者可以查看诊断。'


class DiagnosticStore:
    def __init__(self,path=None):
        self.path = Path(path) if path else None
        self.memory = []
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,guild TEXT,channel TEXT,actor TEXT,category TEXT,kind TEXT,detail TEXT,stamp REAL)')

    @classmethod
    def from_env(cls):
        root = os.getenv('KLEE_DATA_DIR')
        return cls(Path(root)/'diagnostics.sqlite3' if root else None)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path,timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self,category,error,*,guild=None,channel=None,actor=None):
        incident = dict(id=uuid.uuid4().hex[:10],guild=str(guild or ''),channel=str(channel or ''),actor=str(actor or ''),
                        category=category[:40],kind=type(error).__name__,detail=scrub(error),stamp=time.time())
        if self.path:
            with self.connect() as db:
                db.execute('INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?)',tuple(incident.values()))
                db.execute('DELETE FROM incidents WHERE stamp<?',(time.time()-30*86400,))
                db.execute('DELETE FROM incidents WHERE id NOT IN (SELECT id FROM incidents ORDER BY stamp DESC LIMIT 500)')
        else:
            self.memory.append(incident)
            self.memory = self.memory[-500:]
        return incident['id']

    def recent(self,guild,incident=None):
        if self.path:
            with self.connect() as db:
                rows = db.execute('SELECT * FROM incidents WHERE guild=? AND (? IS NULL OR id=?) ORDER BY stamp DESC LIMIT 5',
                                  (str(guild),incident,incident)).fetchall()
            return [dict(row) for row in rows]
        return [row.copy() for row in reversed(self.memory) if row['guild']==str(guild) and (incident is None or row['id']==incident)][:5]
