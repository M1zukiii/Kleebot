from collections import deque
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from bot.player import GuildPlayer, ffmpeg_executable
from bot.resolver import Resolver, Track


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def test_bilibili_search_pages_and_prefixes_normalize(self):
        resolver = Resolver()
        for query in ('https://search.bilibili.com/zh-Hans/all?keyword=%E9%9B%A8%E7%88%B1', 'B站搜索雨爱', 'bilisearch1:雨爱'):
            self.assertEqual(resolver._normalize_query(query), 'bilisearch1:雨爱')
        with self.assertRaises(ValueError):
            resolver._normalize_query('https://search.bilibili.com/all')

    def test_bili_search_does_not_become_youtube_search(self):
        resolver = Resolver()
        resolver.download_streams = False
        with patch('bot.resolver.yt_dlp.YoutubeDL') as ydl:
            extract = ydl.return_value.__enter__.return_value.extract_info
            extract.return_value = {'title': '雨爱', 'url': 'https://example.org/audio'}
            resolver._resolve_sync('B站搜索雨爱', 'user')
            self.assertEqual(extract.call_args.args[0], 'bilisearch1:雨爱')

    def test_ffmpeg_fallback_and_explicit_bad_path(self):
        with patch('bot.player.shutil.which', return_value=None), patch.dict('os.environ', {}, clear=True), patch.dict('sys.modules', {'imageio_ffmpeg': NS(get_ffmpeg_exe=lambda: 'bundled.exe')}):
            self.assertEqual(ffmpeg_executable(), 'bundled.exe')
        with patch('bot.player.shutil.which', return_value=None), patch.dict('os.environ', {'FFMPEG_EXECUTABLE': 'missing.exe'}):
            with self.assertRaisesRegex(RuntimeError, '路径不存在'):
                ffmpeg_executable()

    async def test_failed_start_does_not_leave_queued_or_current_track(self):
        track = Track('雨爱', 'https://example.org/v', 'audio', None, 'user', {})
        resolver = NS(resolve=AsyncMock(return_value=track))
        player = GuildPlayer(NS(), 1, resolver)
        async def fail(instance, interaction):
            player.current = player.queue.popleft()
            raise RuntimeError('play failed')
        with patch('bot.player.ffmpeg_executable', return_value='ffmpeg'), patch.object(GuildPlayer, 'ensure_playing', fail):
            with self.assertRaises(RuntimeError):
                await player.enqueue(NS(user=NS(display_name='user')), '雨爱')
        self.assertEqual(len(player.queue), 0)
        self.assertIsNone(player.current)


if __name__ == '__main__':
    unittest.main()
