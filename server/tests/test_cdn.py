import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import cdn, downloader


class CDNSelectionTests(unittest.TestCase):
    def setUp(self):
        cdn._scores.clear()
        self.urls = ["https://slow.example/audio?secret=one", "https://fast.example/audio?secret=two"]

    def test_collects_camel_and_snake_case_backups_without_changing_signatures(self):
        self.assertEqual(cdn.audio_urls({
            "base_url": self.urls[0], "backup_url": [self.urls[0], self.urls[1], "file:///bad"],
        }), self.urls)
        self.assertEqual(cdn.audio_urls({"backupUrl": self.urls[1]}), [self.urls[1]])

    def test_single_backup_is_first_and_primary_is_not_probed(self):
        with patch.object(cdn, "_probe") as probe:
            self.assertEqual(cdn.ordered_urls(self.urls, primary=self.urls[0]), self.urls[::-1])
            self.assertEqual(cdn.ordered_urls([self.urls[0]], primary=self.urls[0]), [self.urls[0]])
        probe.assert_not_called()

    def test_multiple_backups_are_ranked_but_primary_can_never_overtake_them(self):
        third = "https://third.example/audio?signature=other"
        with patch.object(cdn, "_probe", side_effect=[100, 1600]) as probe:
            self.assertEqual(cdn.ordered_urls([self.urls[0], self.urls[1], third], primary=self.urls[0]),
                             [third, self.urls[1], self.urls[0]])
        self.assertEqual([call.args[0] for call in probe.call_args_list], [self.urls[1], third])

    def test_backup_only_response_does_not_demote_first_backup_as_a_primary(self):
        backups = cdn.audio_urls({"backupUrl": self.urls})
        with patch.object(cdn, "_probe", side_effect=[1600, 100]):
            self.assertEqual(cdn.ordered_urls(backups, primary=None), self.urls)

    def test_fastest_host_cached_across_new_signed_urls_then_reprobed_after_expiry(self):
        with patch.object(cdn, "_probe", side_effect=[100, 1600, 2000, 500]) as probe:
            self.assertEqual(cdn.ordered_urls(self.urls), self.urls[::-1])
            fresh = [url.replace("secret=", "new_signature=") for url in self.urls]
            self.assertEqual(cdn.ordered_urls(fresh), fresh[::-1])
            self.assertEqual(probe.call_count, 2)
            key = next(iter(cdn._scores))
            cdn._scores[key] = (0, cdn._scores[key][1])
            self.assertEqual(cdn.ordered_urls(fresh), fresh)
            self.assertEqual(probe.call_count, 4)

    def test_failed_preferred_host_is_demoted_and_one_host_needs_no_probe(self):
        with patch.object(cdn, "_probe", side_effect=[100, 1600]) as probe:
            cdn.ordered_urls(self.urls)
            cdn.failed(self.urls[1])
            self.assertEqual(cdn.ordered_urls(self.urls), self.urls)
            same_host = [self.urls[0], self.urls[0] + "&other=1"]
            self.assertEqual(cdn.ordered_urls(same_host), same_host)
            self.assertEqual(probe.call_count, 2)

    def test_probe_is_range_bounded_and_never_reads_a_200_full_song(self):
        response = MagicMock(status_code=206, headers={"Content-Range": "bytes 1048576-2097151/10000000"})
        response.iter_raw.return_value = [b"a" * 524288, b"b" * 524288, b"must-not-be-read"]
        context = MagicMock()
        context.__enter__.return_value = response
        with patch.object(cdn.httpx, "stream", return_value=context) as stream:
            self.assertGreater(cdn._probe(self.urls[0]), 0)
            self.assertEqual(stream.call_args.kwargs["headers"]["Range"], "bytes=1048576-2097151")
            self.assertEqual(stream.call_args.kwargs["headers"]["Referer"], "https://www.bilibili.com/")
            response.status_code = 200
            response.iter_raw.reset_mock()
            self.assertEqual(cdn._probe(self.urls[0]), 0)
            response.iter_raw.assert_not_called()

    def test_short_file_416_uses_one_bounded_prefix_retry(self):
        short = MagicMock(status_code=416, headers={"Content-Range": "bytes */65536"})
        valid = MagicMock(status_code=206, headers={"Content-Range": "bytes 0-65535/65536"})
        valid.iter_raw.return_value = [b"a" * 65536]
        first, second = MagicMock(), MagicMock()
        first.__enter__.return_value = short
        second.__enter__.return_value = valid
        with patch.object(cdn.httpx, "stream", side_effect=[first, second]) as stream:
            self.assertGreater(cdn._probe(self.urls[0]), 0)
        self.assertEqual(stream.call_count, 2)
        self.assertEqual(stream.call_args_list[1].kwargs["headers"]["Range"], "bytes=0-1048575")
        short.iter_raw.assert_not_called()

    def test_mismatched_or_oversized_content_range_is_not_consumed(self):
        for content_range in ("bytes 0-2097151/3000000", "bytes 1048576-2097152/3000000",
                              "bytes 1048576-2097151/1048576", "invalid"):
            with self.subTest(content_range=content_range):
                response = MagicMock(status_code=206, headers={"Content-Range": content_range})
                context = MagicMock()
                context.__enter__.return_value = response
                with patch.object(cdn.httpx, "stream", return_value=context):
                    self.assertEqual(cdn._probe(self.urls[0]), 0)
                response.iter_raw.assert_not_called()

    def test_probe_stops_at_time_budget_even_when_range_arrives_slowly(self):
        response = MagicMock(status_code=206, headers={"Content-Range": "bytes 1048576-2097151/3000000"})
        response.iter_raw.return_value = iter([b"a" * 16384, b"must-not-be-read"])
        context = MagicMock()
        context.__enter__.return_value = response
        with (patch.object(cdn.httpx, "stream", return_value=context),
              patch.object(cdn.time, "monotonic", side_effect=[0, 6.1, 6.1])):
            self.assertAlmostEqual(cdn._probe(self.urls[0]), 16384 / 6.1)
        self.assertEqual(next(response.iter_raw.return_value), b"must-not-be-read")

    def test_probe_failure_does_not_expose_signed_url(self):
        with patch.object(cdn.httpx, "stream", side_effect=RuntimeError(self.urls[0])):
            self.assertEqual(cdn._probe(self.urls[0]), 0)

    def test_cold_probe_is_capped_at_three_hosts(self):
        urls = [f"https://cdn{i}.example/file" for i in range(8)]
        with patch.object(cdn, "_probe", return_value=100) as probe:
            cdn.ordered_urls(urls)
            self.assertEqual(probe.call_count, 3)


class CDNDownloadFallbackTests(unittest.TestCase):
    def test_all_cdn_failures_preserve_cid_duration_for_webpage_fallback(self):
        validations = []

        def validate(result, expected):
            validations.append(expected)
            return result

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(downloader, "_direct_dash", side_effect=downloader._DashDownloadError(214.037)),
            patch.object(downloader, "_ytdlp_download", return_value={"file": "fake.m4a", "duration": 50}),
            patch.object(downloader, "_validate_download", side_effect=validate),
        ):
            downloader._download_audio("BVTEST", None, None, 1, 1, Path(directory))
        self.assertEqual(validations, [214.037])

    def test_transfer_failure_or_incomplete_audio_uses_clean_backup_workspace(self):
        for transfer_failure in (True, False):
            with self.subTest(transfer_failure=transfer_failure):
                self._check_fallback(transfer_failure)

    def _check_fallback(self, transfer_failure):
        attempts = []

        class FakeYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def extract_info(self, url, download):
                self.path = Path(self.options["outtmpl"].replace("%(ext)s", "m4a"))
                if self.path.exists():
                    raise AssertionError("Endpoint resumed another endpoint's partial file")
                self.path.write_bytes(b"partial" if not attempts else b"complete")
                attempts.append((url, self.path))
                if len(attempts) == 1 and transfer_failure:
                    raise RuntimeError("failed signed URL " + url)
                return {"ext": "m4a"}

            def prepare_filename(self, _info):
                return str(self.path)

        urls = ["https://slow.example/file?token=secret", "https://fast.example/file?token=secret"]
        play = {"timelength": 100000, "dash": {"audio": [{"id": 30280, "baseUrl": urls[0], "backupUrl": [urls[1]]}]}}

        def validate(result, expected):
            self.assertEqual(expected, 100.0)
            if Path(result["file"]).read_bytes() == b"partial":
                raise RuntimeError("incomplete")
            return result

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(downloader.bilibili, "fetch_detail", side_effect=RuntimeError("HTTP 412")),
            patch.object(downloader.bilibili, "fetch_playurl", return_value=play),
            patch.object(cdn, "failed") as failed,
            patch.object(downloader, "_validate_download", side_effect=validate),
            patch("yt_dlp.YoutubeDL", FakeYDL),
            self.assertLogs(downloader.logger, level="WARNING") as logs,
        ):
            result = downloader._direct_dash("BVTEST", None, Path(directory), cid=7)
            self.assertEqual(result["file"].read_bytes(), b"complete")
            self.assertEqual([url for url, _ in attempts], urls[::-1])
            self.assertNotEqual(attempts[0][1].parent, attempts[1][1].parent)
            failed.assert_called_once_with(urls[1])
            self.assertNotIn("secret", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
