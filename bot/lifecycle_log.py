"""Send one startup and shutdown notice to each channel named 日志."""


class LifecycleLog:
    def __init__(self, bot):
        self.bot = bot
        self.started = False
        self.stopping = False
        bot.add_listener(self.on_ready, 'on_ready')

        # Wrap the client's terminal close method so transient disconnects do not
        # produce misleading shutdown notices.
        if not getattr(bot, '_klee_lifecycle_close_wrapped', False):
            original_close = bot.close
            logger = self

            async def close_with_notice():
                await logger.on_shutdown()
                await original_close()

            bot.close = close_with_notice
            bot._klee_lifecycle_close_wrapped = True

    async def _send(self, message):
        for channel in self.bot.get_all_channels():
            if getattr(channel, 'name', None) != '日志' or not callable(getattr(channel, 'send', None)):
                continue
            try:
                await channel.send(message)
            except Exception as exc:
                print(f'lifecycle log delivery failed: {type(exc).__name__}', flush=True)

    async def on_ready(self):
        if self.started:
            return
        self.started = True
        await self._send('🟢 Kleebot 已启动。')

    async def on_shutdown(self):
        if self.stopping:
            return
        self.stopping = True
        await self._send('🔴 Kleebot 正在关闭。')
