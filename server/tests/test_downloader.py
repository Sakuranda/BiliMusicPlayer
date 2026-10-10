import io
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import downloader
from app.downloader import optimize_cover


class CoverOptimizationTests(unittest.TestCase):
    def test_large_png_becomes_small_jpeg(self):
        source = io.BytesIO()
        Image.new("RGB", (1600, 900), "#d97757").save(source, format="PNG")

        result = optimize_cover(source.getvalue())

        self.assertIsNotNone(result)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), 320)
        self.assertLess(len(result), len(source.getvalue()))

    def test_invalid_cover_is_discarded(self):
        self.assertIsNone(optimize_cover(b"not-an-image"))


class CookieFileTests(unittest.TestCase):
    def test_cookie_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cookie.txt"
            downloader.write_cookie_file("SESSDATA=secret; bili_jct=csrf", path)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("SESSDATA\tsecret", path.read_text())


class MetadataTests(unittest.TestCase):
    def test_empty_tag_container_is_created_and_album_can_be_removed(self):
        class FakeAudio:
            def __init__(self):
                self.tags = None
                self.saved = False

            def add_tags(self):
                self.tags = {"\xa9alb": ["旧专辑"]}

            def save(self):
                self.saved = True

        fake = FakeAudio()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            downloader, "MP4", return_value=fake
        ):
            path = Path(tmp) / "song.m4a"
            path.touch()
            downloader.update_audio_metadata(path, title="新歌名", album="")

        self.assertEqual(fake.tags["\xa9nam"], "新歌名")
        self.assertNotIn("\xa9alb", fake.tags)
        self.assertTrue(fake.saved)

    def test_filename_component_respects_utf8_filesystem_limit(self):
        name = downloader._safe("歌" * 100)
        self.assertLessEqual(len(name.encode("utf-8")), 200)
        self.assertNotEqual(downloader._safe(".."), "..")


class DownloadIsolationTests(unittest.TestCase):
    def test_same_video_parts_use_separate_workspaces(self):
        barrier = threading.Barrier(2)

        def direct(bvid, cookie, tmp, cid):
            path = tmp / f"{bvid}.m4a"
            path.write_text(str(cid))
            barrier.wait(timeout=5)
            return {"file": path, "duration": 1, "cover": None}

        results = []
        try:
            with (patch.object(downloader, "_direct_dash", side_effect=direct),
                  patch.object(downloader, "normalize_m4a", return_value=1.0)):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(downloader.download_audio, "BVTEST", cid=cid) for cid in (1, 2)]
                    results = [future.result(timeout=10) for future in futures]
            self.assertNotEqual(results[0]["tmp_dir"], results[1]["tmp_dir"])
            self.assertEqual([result["file"].read_text() for result in results], ["1", "2"])
        finally:
            for result in results:
                shutil.rmtree(result["tmp_dir"])

    def test_failed_download_cleans_up_workspace(self):
        created = []

        def fail(*args):
            created.append(args[-1])
            raise RuntimeError("failed")

        with patch.object(downloader, "_download_audio", side_effect=fail):
            with self.assertRaises(RuntimeError):
                downloader.download_audio("BVTEST")
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())

    def test_retry_cannot_reuse_previous_route_file(self):
        attempts = []

        def direct(bvid, cookie, tmp, cid):
            attempts.append(tmp)
            path = tmp / f"{bvid}.m4a"
            self.assertFalse(path.exists())
            path.write_bytes(b"partial")
            raise RuntimeError("timeout")

        def fallback(bvid, cookie_file, hook, tmp, attempt, part):
            attempts.append(tmp)
            self.assertFalse(list(tmp.glob("*.m4a")))
            path = tmp / f"{bvid}.m4a"
            path.write_bytes(b"complete")
            return {"file": path, "duration": 1, "cover": None}

        with (patch.object(downloader, "_direct_dash", side_effect=direct),
              patch.object(downloader, "_ytdlp_download", side_effect=fallback),
              patch.object(downloader, "normalize_m4a", return_value=1.0),
              patch.object(downloader.time, "sleep")):
            result = downloader.download_audio("BVTEST")
        try:
            self.assertEqual(len(set(attempts)), 4)
            self.assertEqual(result["file"].read_bytes(), b"complete")
        finally:
            shutil.rmtree(result["tmp_dir"])


class DirectDashTests(unittest.TestCase):
    def _download_with_metadata(self, detail, play):
        fake_info = {"ext": "m4a"}

        class FakeYDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                self.download = download
                return fake_info

            def prepare_filename(self, _info):
                return str(target)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "BVTEST.m4a"
            target.write_bytes(b"audio")
            with (
                patch.object(downloader.bilibili, "fetch_detail", side_effect=detail if isinstance(detail, Exception) else None,
                             return_value=detail),
                patch.object(downloader.bilibili, "fetch_playurl", return_value=play) as playurl,
                patch("yt_dlp.YoutubeDL", FakeYDL),
            ):
                result = downloader._direct_dash("BVTEST", None, Path(tmp), cid=9988)

        playurl.assert_called_once_with("BVTEST", 9988, None)
        return result

    def test_known_cid_uses_precise_playurl_duration_when_detail_is_blocked(self):
        result = self._download_with_metadata(RuntimeError("HTTP 412"), {
            "timelength": 214037,
            "dash": {"duration": 215, "audio": [{"id": 30280, "baseUrl": "https://cdn/audio.m4s"}]},
        })
        self.assertEqual(result["duration"], 214.037)

    def test_completeness_duration_is_for_selected_part_not_video_total(self):
        for detail, play_duration, expected in (
            ({"duration": 900, "pages": [{"cid": 9988, "duration": 120}]}, {}, 120),
            ({"duration": 900, "pages": [{"cid": 9988, "duration": 120}]}, {"duration": 121}, 121),
            ({"duration": 900, "pages": [{"cid": 1111, "duration": 900}]}, {}, 0),
        ):
            with self.subTest(detail=detail, dash=play_duration):
                result = self._download_with_metadata(detail, {"dash": {
                    **play_duration, "audio": [{"id": 30280, "baseUrl": "https://cdn/audio.m4s"}],
                }})
                self.assertEqual(result["duration"], expected)


class DownloadCompletenessTests(unittest.TestCase):
    @staticmethod
    def direct(bvid, cookie, tmp, cid):
        path = tmp / 'audio.m4a'
        path.write_bytes(b'short but valid container')
        return {"file": path, "duration": 214.037, "cover": None}

    @staticmethod
    def fallback(bvid, cookie_file, hook, tmp, attempt, part):
        path = tmp / 'audio.m4a'
        path.write_bytes(b'fallback')
        # A fallback claiming the short duration must not override a trusted
        # longer duration already obtained from the exact cid's playurl.
        return {"file": path, "duration": 100.0, "cover": None}

    def test_short_direct_file_uses_fallback_before_returning(self):
        with (patch.object(downloader, '_direct_dash', side_effect=self.direct),
              patch.object(downloader, '_ytdlp_download', side_effect=self.fallback) as fallback,
              patch.object(downloader, 'normalize_m4a', side_effect=[100.27, 214.037]),
              patch.object(downloader.time, 'sleep')):
            result = downloader.download_audio('BVTEST', cid=9988)
        try:
            fallback.assert_called_once()
            self.assertEqual(result['duration'], 214.037)
            self.assertEqual(result['file'].read_bytes(), b'fallback')
        finally:
            shutil.rmtree(result['tmp_dir'])

    def test_both_routes_incomplete_fail_and_remove_all_temporary_audio(self):
        paths = []

        def normalize(path):
            paths.append(path)
            return 100.27

        with (patch.object(downloader, '_direct_dash', side_effect=self.direct),
              patch.object(downloader, '_ytdlp_download', side_effect=self.fallback) as fallback,
              patch.object(downloader, 'normalize_m4a', side_effect=normalize),
              patch.object(downloader.time, 'sleep')):
            with self.assertRaisesRegex(RuntimeError, '214.04'):
                downloader.download_audio('BVTEST', cid=9988)
        self.assertEqual(fallback.call_count, 2)
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_fallback_duration_is_checked_when_direct_never_downloaded(self):
        with (patch.object(downloader, '_direct_dash', side_effect=RuntimeError('no audio')),
              patch.object(downloader, '_ytdlp_download', side_effect=self.fallback),
              patch.object(downloader, 'normalize_m4a', return_value=20),
              patch.object(downloader.time, 'sleep')):
            with self.assertRaisesRegex(RuntimeError, '100.00'):
                downloader.download_audio('BVTEST', cid=9988)


if __name__ == "__main__":
    unittest.main()
