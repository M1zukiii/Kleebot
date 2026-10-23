"""Durable goals, bounded observations, warning evidence and execution journal."""
from contextlib import contextmanager
import json
import sqlite3
import time


class AgentStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS goals (guild TEXT PRIMARY KEY, owner TEXT, channel TEXT, goal TEXT, enabled INTEGER, version INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY AUTOINCREMENT, guild TEXT, channel TEXT, message TEXT UNIQUE, author TEXT, content TEXT, stamp REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS warnings (guild TEXT, member TEXT, message TEXT UNIQUE, channel TEXT, stamp REAL, consumed INTEGER DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS agent_actions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild TEXT, tool TEXT, detail TEXT, state TEXT, stamp REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS manager_tasks (guild TEXT, channel TEXT, actor TEXT, detail TEXT, stamp REAL, PRIMARY KEY(guild,channel,actor))')
            db.execute('CREATE TABLE IF NOT EXISTS mention_penalties (message TEXT PRIMARY KEY, guild TEXT, member TEXT, minutes INTEGER, state TEXT, stamp REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS token_penalties (message TEXT PRIMARY KEY, guild TEXT, member TEXT, minutes INTEGER, state TEXT, stamp REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS token_warnings (message TEXT PRIMARY KEY, guild TEXT, member TEXT, stamp REAL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def set_goal(self, guild, owner, channel, goal):
        with self.connect() as db:
            db.execute('INSERT INTO goals VALUES(?,?,?,?,1,1) ON CONFLICT(guild) DO UPDATE SET owner=excluded.owner, channel=excluded.channel, goal=excluded.goal, enabled=1, version=goals.version+1',
                       (str(guild), str(owner), str(channel), goal))

    def goal(self, guild):
        with self.connect() as db:
            row = db.execute('SELECT * FROM goals WHERE guild=?', (str(guild),)).fetchone()
        return dict(row) if row else None

    def mention_strikes(self, guild, member, kind='mention'):
        table = 'token_penalties' if kind == 'token' else 'mention_penalties'
        with self.connect() as db:
            return [dict(row) for row in db.execute(f"SELECT minutes,stamp FROM {table} WHERE guild=? AND member=? AND state='applied' AND stamp>? ORDER BY stamp",
                (str(guild), str(member), time.time()-86400))]

    def reserve_mention(self, guild, member, message, minutes, kind='mention'):
        table = 'token_penalties' if kind == 'token' else 'mention_penalties'
        with self.connect() as db:
            db.execute(f'DELETE FROM {table} WHERE stamp<?', (time.time()-7*86400,))
            result = db.execute(f'INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?,?)',
                (str(message), str(guild), str(member), minutes, 'pending', time.time()))
            return bool(result.rowcount)

    def finish_mention(self, message, state, kind='mention', stamp=None):
        table = 'token_penalties' if kind == 'token' else 'mention_penalties'
        with self.connect() as db:
            db.execute(f'UPDATE {table} SET state=?,stamp=? WHERE message=?', (state, time.time() if stamp is None else stamp, str(message)))

    def mention_interrupted(self, guild):
        with self.connect() as db:
            return any(db.execute(f"SELECT 1 FROM {table} WHERE guild=? AND state IN ('pending','uncertain') LIMIT 1", (str(guild),)).fetchone()
                       for table in ('mention_penalties', 'token_penalties'))

    def token_warning(self, guild, member):
        with self.connect() as db:
            row = db.execute('SELECT message,stamp FROM token_warnings WHERE guild=? AND member=? AND stamp>? ORDER BY stamp DESC LIMIT 1',
                             (str(guild), str(member), time.time()-86400)).fetchone()
        return dict(row) if row else None

    def add_token_warning(self, guild, member, message):
        with self.connect() as db:
            db.execute('DELETE FROM token_warnings WHERE stamp<?', (time.time()-7*86400,))
            db.execute('INSERT OR IGNORE INTO token_warnings VALUES(?,?,?,?)',
                       (str(message), str(guild), str(member), time.time()))

    def goals(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM goals WHERE enabled=1')]

    def pause(self, guild):
        with self.connect() as db:
            db.execute('UPDATE goals SET enabled=0,version=version+1 WHERE guild=?', (str(guild),))

    def observe(self, guild, channel, message, author, content, stamp):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO observations(guild,channel,message,author,content,stamp) VALUES(?,?,?,?,?,?)',
                       (str(guild), str(channel), str(message), str(author), content[:700], stamp))
            db.execute('DELETE FROM observations WHERE guild=? AND id NOT IN (SELECT id FROM observations WHERE guild=? ORDER BY id DESC LIMIT 80)', (str(guild), str(guild)))

    def observations(self, guild):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM observations WHERE guild=? ORDER BY id', (str(guild),))]

    def warnings(self, guild, member):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM warnings WHERE guild=? AND member=? AND consumed=0 AND stamp>? ORDER BY stamp',
                                                    (str(guild), str(member), time.time() - 86400))]

    def add_warning(self, guild, member, channel, message):
        with self.connect() as db:
            db.execute('INSERT INTO warnings(guild,member,message,channel,stamp) VALUES(?,?,?,?,?)',
                       (str(guild), str(member), str(message), str(channel), time.time()))

    def consume(self, guild, member):
        with self.connect() as db:
            db.execute('UPDATE warnings SET consumed=1 WHERE guild=? AND member=?', (str(guild), str(member)))

    def action(self, guild, tool, detail, state):
        with self.connect() as db:
            cursor = db.execute('INSERT INTO agent_actions(guild,tool,detail,state,stamp) VALUES(?,?,?,?,?)',
                                (str(guild), tool, json.dumps(detail, ensure_ascii=False)[:6000], state, time.time()))
            db.execute('DELETE FROM agent_actions WHERE guild=? AND id NOT IN (SELECT id FROM agent_actions WHERE guild=? ORDER BY id DESC LIMIT 500)', (str(guild), str(guild)))
            db.execute('DELETE FROM warnings WHERE stamp<?', (time.time() - 7 * 86400,))
            return cursor.lastrowid

    def finish(self, action_id, state):
        with self.connect() as db:
            db.execute('UPDATE agent_actions SET state=? WHERE id=?', (state, action_id))

    def recent(self, guild):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,tool,state,detail,stamp FROM agent_actions WHERE guild=? ORDER BY id DESC LIMIT 5', (str(guild),))]

    def last_manager_reply(self, guild, actor, channel):
        task = self.manager_task(guild, actor, channel)
        if task:
            reply = task.get('reply') or '这项任务尚无完成回执，不能确认是否执行；请检查实际状态。'
            return '任务请求：'+task['request']+'\n实际结果：'+reply
        with self.connect() as db:
            rows = db.execute("SELECT detail FROM agent_actions WHERE guild=? AND tool='manager_reply' AND stamp>? ORDER BY id DESC LIMIT 50",
                              (str(guild), time.time()-1800)).fetchall()
        for row in rows:
            detail = json.loads(row['detail'])
            if detail.get('actor') == str(actor) and detail.get('channel') == str(channel):
                request = detail.get('request', '')
                return (('任务请求：'+request+'\n实际结果：') if request else '') + detail.get('reply', '')
        return ''

    def manager_task(self, guild, actor, channel):
        with self.connect() as db:
            row = db.execute('SELECT detail FROM manager_tasks WHERE guild=? AND channel=? AND actor=? AND stamp>?',
                             (str(guild), str(channel), str(actor), time.time()-900)).fetchone()
        return json.loads(row['detail']) if row else None

    def save_manager_task(self, guild, actor, channel, detail):
        with self.connect() as db:
            db.execute('DELETE FROM manager_tasks WHERE stamp<?', (time.time()-900,))
            db.execute('INSERT OR REPLACE INTO manager_tasks VALUES(?,?,?,?,?)',
                       (str(guild), str(channel), str(actor), json.dumps(detail, ensure_ascii=False), time.time()))
