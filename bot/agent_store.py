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
            return [dict(row) for row in db.execute('SELECT tool,state,detail,stamp FROM agent_actions WHERE guild=? ORDER BY id DESC LIMIT 5', (str(guild),))]
