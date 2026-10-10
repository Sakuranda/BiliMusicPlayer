import unittest
from unittest.mock import patch

import httpx

from app import bilibili
from app.bilibili import BiliError, _response_json


class BilibiliResponseTest(unittest.TestCase):
    def test_http_error_is_translated(self):
        response = httpx.Response(503, text="service unavailable")

        with self.assertRaisesRegex(BiliError, "HTTP 503"):
            _response_json(response, "获取收藏夹")

    def test_invalid_json_is_translated(self):
        response = httpx.Response(200, text="<html>blocked</html>")

        with self.assertRaisesRegex(BiliError, "无法解析"):
            _response_json(response, "获取收藏夹")


    def fetch_pages(self, pages):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=pages[int(request.url.params['pn']) - 1]))
        with patch.object(bilibili, '_client', return_value=httpx.Client(transport=transport)), patch.object(bilibili, '_throttle'), patch.object(bilibili.time, 'sleep'):
            return bilibili.fetch_favorites(['123'])

    def test_partial_snapshot_is_rejected(self):
        first = {"code": 0, "data": {"info": {"title": "收藏夹"}, "medias": [
            {"type": 2, "bvid": "BV1", "title": "first"}], "has_more": True}}
        with self.assertRaisesRegex(BiliError, "第 2 页失败"):
            self.fetch_pages([first, {"code": -412, "data": None}])

    def test_repeated_entries_across_pages_are_deduplicated(self):
        def page(ids, more):
            return {"code": 0, "data": {"info": {"title": "收藏夹"}, "medias": [
                {"type": 2, "bvid": bvid, "title": bvid} for bvid in ids], "has_more": more}}
        _, items, _ = self.fetch_pages([page(['BV1', 'BV2'], True), page(['BV2', 'BV3'], False)])
        self.assertEqual([item['bvid'] for item in items], ['BV1', 'BV2', 'BV3'])

    def test_empty_nonterminal_page_is_rejected(self):
        with self.assertRaisesRegex(BiliError, "数据不完整"):
            self.fetch_pages([{"code": 0, "data": {"info": {}, "medias": [], "has_more": True}}])

    def test_blocked_detail_uses_complete_official_pagelist(self):
        pages = [{'cid': 10, 'page': 1, 'part': '第一首', 'duration': 100}, {'cid': 20, 'page': 2, 'part': '第二首', 'duration': 200}]
        def respond(request):
            if request.url.path.endswith('/view'):
                return httpx.Response(412)
            return httpx.Response(200, json={'code': 0, 'data': pages})
        with patch.object(bilibili, '_client', return_value=httpx.Client(transport=httpx.MockTransport(respond))), patch.object(bilibili, '_throttle'):
            detail = bilibili.fetch_detail('BVTEST')
        self.assertEqual(detail, {'cid': 10, 'pages': pages})
        self.assertNotIn('pic', detail)
        self.assertNotIn('desc', detail)

    def test_failed_pagelist_does_not_disguise_detail_failure(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(412) if request.url.path.endswith('/view') else httpx.Response(200, json={'code': 0, 'data': []}))
        with patch.object(bilibili, '_client', return_value=httpx.Client(transport=transport)), patch.object(bilibili, '_throttle'):
            with self.assertRaisesRegex(BiliError, 'HTTP 412'):
                bilibili.fetch_detail('BVTEST')

if __name__ == "__main__":
    unittest.main()
