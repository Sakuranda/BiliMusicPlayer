import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import importer
from app.db import SCHEMA, update_song


class DownloadQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "queue.db"
        conn = self.get_conn()
        conn.execute(
            "INSERT INTO jobs (id, url, media_id, title, status, total, created_at) "
            "VALUES ('job','https://example.com','123','测试','parsed',12,'now')"
        )
        for index in range(12):
            conn.execute(
                "INSERT INTO songs (bvid, job_id, title, artist, status, created_at) "
                "VALUES (?,?,?,?, 'pending','now')",
                (f"BV{index:010d}", "job", f"歌曲{index}", "歌手"),
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    def test_partial_job_is_bounded_idempotent_and_completes(self):
        state_lock = threading.Lock()
        active = 0
        max_active = 0

        def fake_download(conn, song, _cookie_file):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            update_song(conn, song["id"], status="ready", error=None)
            with state_lock:
                active -= 1
            return True

        selected = [f"BV{index:010d}" for index in range(7)]
        with (
            patch.object(importer, "get_conn", side_effect=self.get_conn),
            patch.object(importer, "_download_one", side_effect=fake_download),
        ):
            started = importer.start_download("job", selected, fetch_lyrics=False)
            duplicate = importer.start_download("job", selected, fetch_lyrics=False)
            self.assertTrue(started["started"])
            self.assertEqual(started["queued"], 7)
            self.assertFalse(duplicate["started"])

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                conn = self.get_conn()
                job = conn.execute("SELECT * FROM jobs WHERE id='job'").fetchone()
                conn.close()
                if job["status"] == "done":
                    break
                time.sleep(0.02)
            else:
                self.fail("下载队列未在超时前完成")

        self.assertLessEqual(max_active, importer.DOWNLOAD_THREADS)
        self.assertEqual(job["total"], 7)
        self.assertEqual(job["done"], 7)
        self.assertEqual(job["failed"], 0)

        conn = self.get_conn()
        remaining = conn.execute("SELECT COUNT(*) FROM songs WHERE status='pending'").fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 5)

    def test_existing_file_repairs_stale_status_and_is_not_queued(self):
        music_dir = Path(self.tempdir.name) / "music"
        existing = music_dir / "album" / "existing.m4a"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"audio")
        conn = self.get_conn()
        conn.execute(
            "UPDATE songs SET status='error', error='旧任务中断', file_path=?, "
            "cid=NULL, downloaded_cid=123 WHERE bvid='BV0000000000'",
            ("album/existing.m4a",),
        )
        conn.commit()
        conn.close()

        with (
            patch.object(importer, "get_conn", side_effect=self.get_conn),
            patch.object(importer, "MUSIC_DIR", music_dir),
            patch.object(importer, "_download_one") as download,
        ):
            result = importer.start_download("job", ["BV0000000000"], fetch_lyrics=False)

        self.assertFalse(result["started"])
        self.assertEqual(result["queued"], 0)
        download.assert_not_called()
        conn = self.get_conn()
        row = conn.execute(
            "SELECT status, error FROM songs WHERE bvid='BV0000000000'"
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "ready")
        self.assertIsNone(row["error"])

    def test_queue_setup_failure_releases_reservations(self):
        with patch.object(importer, "get_conn", side_effect=self.get_conn), patch.object(importer, "update_job", side_effect=RuntimeError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                importer.start_download("job", ["BV0000000000"])
        self.assertFalse(importer.is_job_active("job"))
        self.assertFalse(importer.is_song_active(1))

    def test_two_jobs_share_the_same_concurrency_limit(self):
        conn = self.get_conn()
        conn.execute("INSERT INTO jobs (id,url,created_at) VALUES ('second','url','now')")
        conn.execute("UPDATE songs SET job_id='second' WHERE id>6")
        conn.commit()
        conn.close()
        lock = threading.Lock()
        active = peak = 0
        visited = []
        def fake_download(conn, song, cookie):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                visited.append(song['id'])
            time.sleep(0.025)
            update_song(conn, song['id'], status='ready')
            with lock:
                active -= 1
            return True
        with patch.object(importer, "get_conn", side_effect=self.get_conn), patch.object(importer, "_download_one", side_effect=fake_download):
            importer.start_download("job")
            importer.start_download("second")
            deadline = time.monotonic() + 5
            while importer.is_job_active('job') or importer.is_job_active('second'):
                if time.monotonic() > deadline:
                    self.fail('jobs did not finish')
                time.sleep(0.01)
        self.assertLessEqual(peak, importer.DOWNLOAD_THREADS)
        self.assertEqual(len(set(visited)), 12)
        self.assertEqual(len(visited), 12)

    def test_statistics_failure_keeps_running_song_reserved(self):
        release = threading.Event()
        waiting = threading.Event()
        counted = threading.Event()
        def fake_download(conn, song, cookie):
            if song['id'] == 2:
                waiting.set()
                release.wait(3)
            update_song(conn, song['id'], status='ready')
            return True
        def broken_count(*args, **kwargs):
            counted.set()
            raise RuntimeError('statistics failed')
        with patch.object(importer, 'get_conn', side_effect=self.get_conn), patch.object(importer, '_download_one', side_effect=fake_download), patch.object(importer, '_recount_selected', side_effect=broken_count), patch.object(importer.logger, 'exception'):
            importer.start_download('job', ['BV0000000000', 'BV0000000001'])
            try:
                self.assertTrue(waiting.wait(2))
                self.assertTrue(counted.wait(2))
                self.assertTrue(importer.is_song_active(2))
                self.assertTrue(importer.is_job_active('job'))
            finally:
                release.set()
                deadline = time.monotonic() + 3
                while importer.is_job_active('job') and time.monotonic() < deadline:
                    time.sleep(.01)
            self.assertFalse(importer.is_song_active(2))


if __name__ == "__main__":
    unittest.main()
