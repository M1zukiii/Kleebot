"""Recover public video metadata when Bilibili returns an empty SSR shell."""
import json
import re

from yt_dlp.extractor.bilibili import BiliBiliIE as UpstreamBiliBiliIE
from yt_dlp.utils import ExtractorError


class BiliBiliIE(UpstreamBiliBiliIE):
    # Preserve the upstream extractor key so URL results from search/shortlinks
    # also use this implementation. Format extraction and access checks stay upstream.
    def _download_webpage_handle(self, url, video_id, *args, **kwargs):
        webpage, handle = super()._download_webpage_handle(url, video_id, *args, **kwargs)
        if not webpage or not getattr(handle, 'url', None):
            return webpage, handle
        match = self._match_valid_url(handle.url)
        if not match or re.search(r'window\.__INITIAL_STATE__\s*=', webpage):
            return webpage, handle
        risk = self._search_json(r'\bwindow\._riskdata_\s*=', webpage, 'risk', video_id, default={})
        if risk.get('v_voucher'):
            return webpage, handle  # Let upstream report rate limiting; no bypass.
        prefix, uid = match.group('prefix').upper(), match.group('id')
        query = {'platform': 'web', 'bvid' if prefix == 'BV' else 'aid': prefix + uid if prefix == 'BV' else uid}
        detail = self._download_json(
            'https://api.bilibili.com/x/web-interface/wbi/view/detail', video_id,
            query=self._sign_wbi(query, video_id),
            headers={'Referer': 'https://www.bilibili.com/', 'Origin': 'https://www.bilibili.com'},
            note='Reading public video metadata for empty webpage')
        data = detail.get('data') or {}
        view = data.get('View') or {}
        if detail.get('code') != 0 or not view:
            code = detail.get('code')
            message = {-403: '此视频需要登录后才能读取。', -404: '视频不存在、已删除或当前地区不可用。',
                       -412: 'B站暂时限制了请求，请稍后再试。'}.get(code, 'B站未返回可用的视频信息，请稍后再试。')
            raise ExtractorError(message, expected=True)
        if view.get('redirect_url'):
            return webpage, handle  # Upstream owns Bangumi/episode redirects.
        if not view.get('bvid') or not view.get('cid') or not view.get('title'):
            raise ExtractorError('B站视频信息不完整，暂时无法播放。', expected=True)
        state = {'videoData': view, 'upData': view.get('owner') or {}, 'tags': data.get('Tags') or []}
        injected = '<script>window.__INITIAL_STATE__=' + json.dumps(state, ensure_ascii=False) + ';</script>'
        self.to_screen('Public API metadata recovered for empty webpage')
        return injected + webpage, handle


def install_bilibili_compat(ydl):
    ydl.add_info_extractor(BiliBiliIE())
