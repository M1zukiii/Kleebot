import unittest
from types import SimpleNamespace

from bot.lifecycle_log import LifecycleLog


class FakeChannel:
    def __init__(self, name):
        self.name = name
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


class LifecycleLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_is_logged_once_only_to_log_channels(self):
        log_channel = FakeChannel('日志')
        other_channel = FakeChannel('general')
        bot = SimpleNamespace(
            add_listener=lambda callback, name: setattr(bot, 'ready_listener', callback),
            get_all_channels=lambda: [log_channel, other_channel],
            close=lambda: None,
        )
        LifecycleLog(bot)

        await bot.ready_listener()
        await bot.ready_listener()

        self.assertEqual(log_channel.messages, ['🟢 Kleebot 已启动。'])
        self.assertEqual(other_channel.messages, [])

    async def test_close_logs_shutdown_before_closing(self):
        log_channel = FakeChannel('日志')
        events = []

        async def close():
            events.append('closed')

        bot = SimpleNamespace(
            add_listener=lambda callback, name: setattr(bot, 'ready_listener', callback),
            get_all_channels=lambda: [log_channel],
            close=close,
        )
        LifecycleLog(bot)

        await bot.close()
        await bot.close()

        self.assertEqual(log_channel.messages, ['🔴 Kleebot 正在关闭。'])
        self.assertEqual(events, ['closed', 'closed'])
