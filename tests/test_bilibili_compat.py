import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from yt_dlp.utils import ExtractorError

from bot.bilibili_compat import BiliBiliIE, UpstreamBiliBiliIE
from bot.resolver import Resolver


URL = 'https://www.bilibili.com/video/BV1vApn63EJ2/'


class BilibiliCompatTests(unittest.TestCase):
    def setUp(self):
        self.ie = BiliBiliIE()
        self.ie._search_json = Mock(return_value={})
        self.ie._sign_wbi = Mock(side_effect=lambda query, uid: query)
        self.ie._download_json = Mock(return_value={'code': 0, 'data': {'View': {
            'bvid': 'BV1vApn63EJ2', 'cid': 123, 'title': '测试视频', 'owner': {'name': '作者'},
            'is_upower_exclusive': True, 'rights': {'is_stein_gate': 0}}}})
        self.ie.to_screen = Mock()

    def page(self, text):
        with patch.object(UpstreamBiliBiliIE, '_download_webpage_handle', return_value=(text, NS(url=URL))):
            return self.ie._download_webpage_handle(URL, '1vApn63EJ2')[0]

    def test_empty_shell_uses_public_metadata_and_preserves_access_flags(self):
        text = self.page('<html>empty shell</html>')
        state = json.JSONDecoder().raw_decode(text.split('window.__INITIAL_STATE__=', 1)[1])[0]
        self.assertEqual(state['videoData']['cid'], 123)
        self.assertTrue(state['videoData']['is_upower_exclusive'])
        self.assertEqual(state['upData']['name'], '作者')
        self.ie._download_json.assert_called_once()
        self.assertEqual(self.ie.ie_key(), 'BiliBili')

    def test_normal_pages_and_risk_challenges_do_not_use_fallback(self):
        normal = '<script>window.__INITIAL_STATE__={"videoData":{}};</script>'
        self.assertEqual(self.page(normal), normal)
        self.ie._download_json.assert_not_called()
        self.ie._search_json.return_value = {'v_voucher': 'challenge'}
        self.assertEqual(self.page('challenge-page'), 'challenge-page')
        self.ie._download_json.assert_not_called()

    def test_api_denial_and_missing_fields_fail_without_fabricating_data(self):
        self.ie._download_json.return_value = {'code': -403}
        with self.assertRaisesRegex(ExtractorError, '登录'):
            self.page('empty')
        self.ie._download_json.return_value = {'code': 0, 'data': {'View': {'title': 'incomplete'}}}
        with self.assertRaisesRegex(ExtractorError, '不完整'):
            self.page('empty')

    def test_share_links_remove_tracking_but_keep_part_selection(self):
        resolver = Resolver()
        self.assertEqual(resolver._normalize_query(URL + '?share_source=COPY&buvid=private&p=2'), URL + '?p=2')
        self.assertEqual(resolver._normalize_query('BV1vApn63EJ2'), URL)


if __name__ == '__main__':
    unittest.main()
