"""Persist API usage and stop model requests after a confirmed billing limit."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from threading import RLock


BASIC_MESSAGE = 'Klee 现在处于基础模式，AI 聊天、联网、agent 和智能文件生成已暂停。手动指令、常规点歌和文件文字提取仍可用。'
EXHAUSTED_CODES = {'insufficient_quota', 'credit_balance_exhausted', 'billing_hard_limit_reached',
                   'organization_spend_limit_exceeded', 'project_spend_limit_exceeded', 'usage_limit_reached'}


class BasicModeError(RuntimeError):
    pass


class ModelBudget:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = RLock()
        self._basic = False
        self._reason = ''
        self._usage = {'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'search_calls': 0}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS budget_state (id INTEGER PRIMARY KEY CHECK(id=1), basic INTEGER, reason TEXT)')
                db.execute('INSERT OR IGNORE INTO budget_state VALUES(1,0,\'\')')
                db.execute('CREATE TABLE IF NOT EXISTS daily_usage(day TEXT,model TEXT,requests INTEGER,input_tokens INTEGER,output_tokens INTEGER,search_calls INTEGER,PRIMARY KEY(day,model))')

    @classmethod
    def from_env(cls):
        root = os.getenv('KLEE_DATA_DIR')
        return cls(Path(root) / 'model_usage.sqlite3' if root else None)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.lock:
            if self.path:
                with self.connect() as db:
                    basic, reason = db.execute('SELECT basic,reason FROM budget_state WHERE id=1').fetchone()
                return bool(basic), reason
            return self._basic, self._reason

    def set_basic(self, basic, reason='manual'):
        with self.lock:
            self._basic, self._reason = bool(basic), reason[:100]
            if self.path:
                with self.connect() as db:
                    db.execute('UPDATE budget_state SET basic=?,reason=? WHERE id=1', (int(basic), reason[:100]))

    def before_request(self):
        if self.state()[0]:
            raise BasicModeError(BASIC_MESSAGE)

    def failed(self, error):
        code = getattr(error, 'code', None)
        body = getattr(error, 'body', None)
        if not code and isinstance(body, dict):
            detail = body.get('error', body)
            if isinstance(detail, dict):
                code = detail.get('code') or detail.get('type')
        if code in EXHAUSTED_CODES:
            self.set_basic(True, code)
            return True
        return False

    def record(self, response, model):
        usage = getattr(response, 'usage', None)
        incoming = getattr(usage, 'input_tokens', None)
        outgoing = getattr(usage, 'output_tokens', None)
        incoming = int(incoming if incoming is not None else getattr(usage, 'prompt_tokens', 0) or 0)
        outgoing = int(outgoing if outgoing is not None else getattr(usage, 'completion_tokens', 0) or 0)
        searches = sum(getattr(item, 'type', None) == 'web_search_call' for item in getattr(response, 'output', []) or [])
        with self.lock:
            values = (1, incoming, outgoing, searches)
            for key, value in zip(self._usage, values):
                self._usage[key] += value
            if self.path:
                day = datetime.now(timezone.utc).date().isoformat()
                with self.connect() as db:
                    db.execute('INSERT INTO daily_usage VALUES(?,?,?,?,?,?) ON CONFLICT(day,model) DO UPDATE SET requests=requests+excluded.requests,input_tokens=input_tokens+excluded.input_tokens,output_tokens=output_tokens+excluded.output_tokens,search_calls=search_calls+excluded.search_calls', (day, model, *values))

    def summary(self):
        basic, reason = self.state()
        counts = dict(self._usage)
        if self.path:
            day = datetime.now(timezone.utc).date().isoformat()
            with self.connect() as db:
                row = db.execute('SELECT COALESCE(SUM(requests),0),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(search_calls),0) FROM daily_usage WHERE day=?', (day,)).fetchone()
            counts = dict(zip(counts, row))
        return ('当前：' + ('基础模式' if basic else '完整模式') + f'（{reason or "未收到额度耗尽错误"}）\n'
                f'今日 UTC：模型请求 {counts["requests"]} 次；输入 {counts["input_tokens"]} tokens；输出 {counts["output_tokens"]} tokens；搜索 {counts["search_calls"]} 次。\n'
                '这是接入统计后的成功请求用量，不是账户剩余额度或实际账单。赠金和付费余额由提供商结算；仅在接口明确报额度耗尽时自动降级。')
