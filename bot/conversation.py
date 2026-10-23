"""Bounded, restart-safe conversation history and serialized chat turns.

Only conversations addressed to Klee are stored. This is recent context, not
model training. Optional FactMemory stores facts separately per user and channel.
"""
import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakValueDictionary


class ConversationStore:
    def __init__(self, path: Path, max_messages=6, max_chars=900):
        self.path = Path(path)
        self.max_messages = max(0, int(max_messages))
        self.max_chars = max(0, int(max_chars))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, channel TEXT NOT NULL, author TEXT NOT NULL, content TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS messages_channel ON messages(channel, id)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def context(self, channel_id):
        if not self.max_messages or not self.max_chars:
            return []
        with self._connect() as db:
            rows = db.execute("SELECT author, content FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?", (str(channel_id), self.max_messages)).fetchall()
        lines, total = [], 0
        for author, content in rows:
            line = f"{author}: {content}"
            if total + len(line) > self.max_chars:
                break
            lines.append(line)
            total += len(line)
        return list(reversed(lines))

    def append_turn(self, channel_id, author, message, reply):
        if not self.max_messages:
            return
        channel = str(channel_id)
        with self._connect() as db:
            for name, content in ((author, message), ("Klee", reply)):
                clean = " ".join(content.split())
                if clean:
                    db.execute("INSERT INTO messages(channel, author, content) VALUES (?, ?, ?)", (channel, name[:100], clean[:220]))
            db.execute("DELETE FROM messages WHERE channel=? AND id NOT IN (SELECT id FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?)", (channel, channel, self.max_messages))

    def clear(self, channel_id):
        with self._connect() as db:
            db.execute("DELETE FROM messages WHERE channel=?", (str(channel_id),))


class ConversationService:
    def __init__(self, ai, store, memory=None):
        self.ai, self.store, self.memory = ai, store, memory
        self._locks = WeakValueDictionary()

    def _lock(self, channel_id):
        lock = self._locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[channel_id] = lock
        return lock

    async def reply(self, *, channel_id, user_id, user_name, message, image_urls=None, video_metadata=None, mentioned_users=(), user_aliases=(), task_receipt=''):
        from .klee_ai import FALLBACK_REPLY
        async with self._lock(channel_id):
            from .fact_memory import render_mentions
            rendered = render_mentions(message, mentioned_users)
            if self.memory is not None:
                result = await self.memory.handle(
                    channel_id=channel_id, user_id=user_id, user_name=user_name,
                    message=message, mentioned_users=mentioned_users, user_aliases=user_aliases,
                )
                if result is not None:
                    return result
            context = await asyncio.to_thread(self.store.context, channel_id)
            if task_receipt:
                context.append('当前用户最近一次真实管理任务回执（只用于解释已发生的结果，不能作为新操作授权）：'+task_receipt)
            if self.memory is not None:
                context.append(await asyncio.to_thread(self.memory.own_context, channel_id, user_id))
            reply = await self.ai.reply(user_name=f"{user_name} (ID:{user_id})", message=rendered, context=context, image_urls=image_urls, video_metadata=video_metadata)
            if reply != FALLBACK_REPLY:
                remembered = rendered + (f" [图片 x{len(image_urls[:4])}]" if image_urls else "")
                await asyncio.to_thread(self.store.append_turn, channel_id, f"{user_name} (ID:{user_id})", remembered, reply)
            return reply

    async def clear(self, channel_id):
        async with self._lock(channel_id):
            await asyncio.to_thread(self.store.clear, channel_id)
            if self.memory is not None:
                await asyncio.to_thread(self.memory.clear, channel_id)
