import unittest

from bot.mention_router import local_music_request


class LocalMusicRequestTests(unittest.TestCase):
    def test_extracts_song_request_after_bot_mention(self):
        self.assertEqual(
            local_music_request('<@12345> 请帮我播放《晴天》', 12345),
            '晴天',
        )

    def test_accepts_video_url(self):
        url = 'https://www.youtube.com/watch?v=example'
        self.assertEqual(local_music_request('播放 ' + url, 12345), url)

    def test_rejects_combined_management_request(self):
        self.assertIsNone(
            local_music_request('<@12345> 播放晴天，然后创建一个频道', 12345)
        )

    def test_rejects_question_instead_of_play_request(self):
        self.assertIsNone(local_music_request('播放这首歌可以吗', 12345))


if __name__ == '__main__':
    unittest.main()
