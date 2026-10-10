import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from PIL import Image
from app import downloader


class CoverCacheTests(unittest.TestCase):
    def setUp(self):
        buffer = io.BytesIO()
        Image.new('RGB', (640, 480), 'red').save(buffer, format='JPEG')
        self.picture = buffer.getvalue()

    def test_http_favorite_cover_is_requested_over_https_with_referer(self):
        seen = []
        def handle(request):
            seen.append(request)
            return httpx.Response(200, content=self.picture)
        real = httpx.Client
        with patch.object(downloader.httpx, 'Client', side_effect=lambda **kwargs: real(transport=httpx.MockTransport(handle), **kwargs)), patch.object(downloader.bilibili, 'fetch_detail') as detail:
            result = downloader.fetch_cover('BVtest', cover_url='http://i1.hdslb.com/bfs/a.jpg')
        self.assertGreater(len(result), 0)
        self.assertLessEqual(len(result), 1024 * 1024)
        self.assertLessEqual(max(Image.open(io.BytesIO(result)).size), 320)
        self.assertEqual(str(seen[0].url), 'https://i1.hdslb.com/bfs/a.jpg')
        self.assertEqual(seen[0].headers['referer'], 'https://www.bilibili.com/')
        detail.assert_not_called()

    def test_non_bilibili_and_local_urls_are_rejected_before_fetch(self):
        for url in ['http://127.0.0.1/a.jpg', 'http://hdslb.com.evil.test/a.jpg', 'file:///tmp/a.jpg']:
            with self.subTest(url=url), patch.object(downloader.httpx, 'Client') as client:
                with self.assertRaises(ValueError):
                    downloader.fetch_cover('BVtest', cover_url=url)
                client.assert_not_called()

    def test_legacy_remote_cover_is_cached_and_not_fetched_twice(self):
        with tempfile.TemporaryDirectory() as root, patch.object(downloader, 'COVER_DIR', Path(root)), patch.object(downloader, 'fetch_cover', return_value=self.picture) as fetch:
            first = downloader.cached_remote_cover('BVtest', 'http://i1.hdslb.com/a.jpg')
            second = downloader.cached_remote_cover('BVtest', 'http://i1.hdslb.com/a.jpg')
            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), self.picture)
            fetch.assert_called_once()

    def test_redirect_to_unknown_host_is_not_followed(self):
        real = httpx.Client
        seen=[]
        def handle(request):
            seen.append(request)
            return httpx.Response(302, headers={'Location':'http://127.0.0.1/secret'})
        with patch.object(downloader.httpx, 'Client', side_effect=lambda **kwargs: real(transport=httpx.MockTransport(handle), **kwargs)):
            with self.assertRaises(httpx.HTTPStatusError):
                downloader.fetch_cover('BVtest', cover_url='http://i1.hdslb.com/a.jpg')
        self.assertEqual(len(seen), 1)

    def test_oversized_local_cover_is_not_saved(self):
        with tempfile.TemporaryDirectory() as root, patch.object(downloader, 'COVER_DIR', Path(root)):
            self.assertIsNone(downloader.save_cover(b'x' * (1024 * 1024 + 1), 'large'))
            self.assertEqual(list(Path(root).iterdir()), [])
