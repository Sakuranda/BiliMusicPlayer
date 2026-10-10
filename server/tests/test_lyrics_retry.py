import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

from app import importer, lyrics_retry
from app.db import SCHEMA


class LyricsRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'lyrics.db'
        self.callbacks = []
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(lyrics_retry, 'get_conn', side_effect=self.connect))
        self.stack.enter_context(patch.object(lyrics_retry, 'maintenance_lease', nullcontext))
        self.stack.enter_context(patch.object(lyrics_retry._executor, 'submit', side_effect=self.enqueue))
        self.embedded = self.stack.enter_context(patch.object(lyrics_retry.downloader, 'update_embedded_lyrics'))
        self.sidecar = self.stack.enter_context(patch.object(lyrics_retry.downloader, 'write_lrc_sidecar'))
        with self.connection() as conn:
            conn.execute("INSERT INTO songs(bvid,title,artist,status,file_path,created_at) "
                         "VALUES ('BV0000000001','歌曲','歌手','ready','audio.m4a','now')")
            with patch.object(lyrics_retry.time, 'time', return_value=1000):
                lyrics_retry.record_failure(conn, 1, '没有匹配到歌词')

    def enqueue(self, function):
        called = False

        def once():
            nonlocal called
            if not called:
                called = True
                function()
        self.callbacks.append(once)

    def tearDown(self):
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics', return_value={'plain': '', 'lrc': '', 'source': ''}):
            for callback in self.callbacks:
                callback()
        self.stack.close()
        with lyrics_retry._lock:
            lyrics_retry._queued.clear()
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    @contextmanager
    def connection(self):
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def state(self):
        with self.connection() as conn:
            return dict(conn.execute('SELECT * FROM lyrics_failures WHERE song_id=1').fetchone())

    def test_five_minute_retry_uses_force_and_persists_notice_after_success(self):
        with patch.object(lyrics_retry.time, 'time', return_value=1299):
            lyrics_retry.retry_due()
        self.assertEqual(self.callbacks, [])
        with patch.object(lyrics_retry.time, 'time', return_value=1300), \
                patch.object(lyrics_retry.lyrics, 'fetch_lyrics',
                             return_value={'plain': '歌词', 'lrc': '[00:01]歌词', 'source': 'lrclib'}) as fetch:
            lyrics_retry.retry_due()
            lyrics_retry.retry_due()
            self.assertEqual(len(self.callbacks), 1)
            self.assertTrue(importer.is_song_active(1))
            self.callbacks[0]()
            fetch.assert_called_once_with('歌曲', '歌手', 0, force=True)
        self.assertFalse(importer.is_song_active(1))
        self.assertEqual(self.state()['status'], 'ready')
        self.assertIsNone(self.state()['next_retry_at'])
        self.assertEqual(self.state()['attempts'], 2)
        self.assertEqual(len(lyrics_retry.list_failures()), 1)
        self.embedded.assert_called_once_with('audio.m4a', '[00:01]歌词')
        self.sidecar.assert_called_once_with('audio.m4a', '[00:01]歌词')
        with self.connection() as conn:
            row = conn.execute('SELECT lyrics,lrc,lyrics_source,status FROM songs WHERE id=1').fetchone()
            self.assertEqual(tuple(row), ('歌词', '[00:01]歌词', 'lrclib', 'ready'))

    def test_skip_stops_automatic_retry_and_manual_retry_reactivates(self):
        lyrics_retry.dismiss(1)
        self.assertEqual(lyrics_retry.list_failures(), [])
        self.assertIsNone(self.state()['next_retry_at'])
        with patch.object(lyrics_retry.time, 'time', return_value=2000):
            lyrics_retry.retry_due()
            self.assertEqual(self.callbacks, [])
            self.assertTrue(lyrics_retry.retry_song(1)['started'])
        self.assertEqual(self.state()['dismissed'], 0)
        self.assertEqual(self.state()['status'], 'queued')

    def test_inflight_skip_cannot_be_undone_by_new_failure(self):
        lyrics_retry.retry_song(1)
        lyrics_retry.dismiss(1)
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics', return_value={'plain': '', 'lrc': '', 'source': ''}), \
                patch.object(lyrics_retry.time, 'time', return_value=2000):
            self.callbacks[0]()
        self.assertEqual(self.state()['dismissed'], 1)
        self.assertIsNone(self.state()['next_retry_at'])
        self.assertFalse(importer.is_song_active(1))

    def test_no_match_and_provider_exception_preserve_existing_lyrics(self):
        with self.connection() as conn:
            conn.execute("UPDATE songs SET lyrics='旧歌词',lrc='[00:02]旧歌词',lyrics_source='lrclib' WHERE id=1")
        lyrics_retry.retry_song(1)
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics', return_value={'plain': '', 'lrc': '', 'source': ''}):
            self.callbacks[0]()
        lyrics_retry.retry_song(1)
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics', side_effect=RuntimeError('provider failed')), \
                patch.object(lyrics_retry.logger, 'warning'):
            self.callbacks[1]()
        with self.connection() as conn:
            row = conn.execute('SELECT lyrics,lrc,lyrics_source FROM songs WHERE id=1').fetchone()
            self.assertEqual(tuple(row), ('旧歌词', '[00:02]旧歌词', 'lrclib'))
        self.embedded.assert_not_called()
        self.sidecar.assert_not_called()
        self.assertEqual(self.state()['status'], 'ready')
        self.assertIsNone(self.state()['next_retry_at'])
        self.assertFalse(importer.is_song_active(1))

    def test_uploaded_lyrics_are_never_overwritten(self):
        with self.connection() as conn:
            conn.execute("UPDATE songs SET lyrics='人工歌词',lyrics_source='upload' WHERE id=1")
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics') as fetch:
            self.assertFalse(lyrics_retry.retry_song(1)['started'])
            fetch.assert_not_called()
        self.assertEqual(self.state()['status'], 'ready')
        self.assertEqual(self.callbacks, [])
        self.assertFalse(importer.is_song_active(1))

    def test_disabled_lyrics_skip_automatic_but_manual_reenables(self):
        with self.connection() as conn:
            conn.execute('UPDATE songs SET lyrics_enabled=0 WHERE id=1')
        self.assertFalse(lyrics_retry.retry_song(1, automatic=True)['started'])
        self.assertEqual(self.state()['status'], 'ready')
        self.assertTrue(lyrics_retry.retry_song(1)['started'])
        with self.connection() as conn:
            self.assertEqual(conn.execute('SELECT lyrics_enabled FROM songs WHERE id=1').fetchone()[0], 1)

    def test_download_ownership_and_queue_capacity_block_duplicates(self):
        with importer.reserve_song_edit(1):
            self.assertFalse(lyrics_retry.retry_song(1)['started'])
            self.assertTrue(importer.is_song_active(1))
        self.assertEqual(self.callbacks, [])
        with patch.object(lyrics_retry, 'MAX_QUEUED', 0):
            self.assertFalse(lyrics_retry.retry_song(1)['started'])
        self.assertTrue(lyrics_retry.retry_song(1)['started'])
        self.assertFalse(lyrics_retry.retry_song(1)['started'])
        self.assertEqual(len(self.callbacks), 1)

    def test_only_running_and_one_waiting_song_are_reserved(self):
        with self.connection() as conn:
            for index in (2, 3):
                conn.execute("INSERT INTO songs(bvid,title,artist,status,created_at) VALUES (?,?,?,'ready','now')",
                             (f'BV{index:010}', '歌曲', '歌手'))
        self.assertTrue(lyrics_retry.retry_song(1)['started'])
        self.assertTrue(lyrics_retry.retry_song(2)['started'])
        self.assertFalse(lyrics_retry.retry_song(3)['started'])
        self.assertTrue(importer.is_song_active(1))
        self.assertTrue(importer.is_song_active(2))
        self.assertFalse(importer.is_song_active(3))
        self.assertEqual(len(self.callbacks), 2)

    def test_provider_failure_without_old_lyrics_uses_safe_message_and_due_time(self):
        lyrics_retry.retry_song(1)
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics', side_effect=RuntimeError('https://provider/?token=secret')), \
                patch.object(lyrics_retry.logger, 'warning') as warning, \
                patch.object(lyrics_retry.time, 'time', return_value=2000):
            self.callbacks[0]()
        self.assertEqual(self.state()['last_error'], '歌词匹配暂时失败，请重试或上传歌词')
        self.assertEqual(self.state()['next_retry_at'], 2300)
        self.assertNotIn('secret', str(warning.call_args_list))
        self.assertFalse(importer.is_song_active(1))

    def test_metadata_only_historical_lyrics_are_treated_as_missing(self):
        with self.connection() as conn:
            conn.execute('DELETE FROM lyrics_failures')
            conn.execute("UPDATE songs SET lrc='[ti:歌曲]\n[00:00.00]' WHERE id=1")
            conn.commit()
            lyrics_retry.reconcile(conn)
        self.assertEqual(self.state()['status'], 'error')

    def test_worker_connection_exception_releases_owner_and_preserves_notice(self):
        lyrics_retry.retry_song(1)
        attempts = 0

        def connect():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError('temporary DB error')
            return self.connect()

        with patch.object(lyrics_retry, 'get_conn', side_effect=connect), \
                patch.object(lyrics_retry.logger, 'warning'):
            self.callbacks[0]()
        self.assertEqual(self.state()['status'], 'error')
        self.assertFalse(importer.is_song_active(1))
        self.assertNotIn(1, lyrics_retry._queued)

    def test_submit_failure_releases_lease_and_song_with_persistent_due_time(self):
        with patch.object(lyrics_retry._executor, 'submit', side_effect=RuntimeError('executor unavailable')), \
                patch.object(lyrics_retry.time, 'time', return_value=2000):
            with self.assertRaisesRegex(RuntimeError, 'executor unavailable'):
                lyrics_retry.retry_song(1)
        self.assertFalse(importer.is_song_active(1))
        self.assertNotIn(1, lyrics_retry._queued)
        self.assertEqual(self.state()['status'], 'error')
        self.assertEqual(self.state()['next_retry_at'], 2300)

    def test_historical_ready_empty_lyrics_and_restart_are_reconciled(self):
        with self.connection() as conn:
            conn.execute('DELETE FROM lyrics_failures')
            for index, status, source, enabled in ((2, 'ready', '', 1), (3, 'pending', '', 1),
                                                   (4, 'ready', 'upload', 1), (5, 'ready', '', 0)):
                conn.execute('INSERT INTO songs(bvid,title,artist,status,lyrics_source,lyrics_enabled,created_at) '
                             'VALUES (?,?,?,?,?,?,?)',
                             (f'BV{index:010}', '测试', '歌手', status, source, enabled, 'now'))
            conn.commit()
            with patch.object(lyrics_retry.time, 'time', return_value=1000):
                lyrics_retry.reconcile(conn)
            self.assertEqual([r[0] for r in conn.execute('SELECT song_id FROM lyrics_failures ORDER BY song_id')], [1, 2])
            conn.execute("UPDATE lyrics_failures SET status='queued',next_retry_at=1300 WHERE song_id=1")
            conn.execute("UPDATE lyrics_failures SET dismissed=1,next_retry_at=NULL WHERE song_id=2")
            conn.commit()
            with patch.object(lyrics_retry.time, 'time', return_value=2000):
                lyrics_retry.reconcile(conn)
            rows = conn.execute('SELECT status,next_retry_at,dismissed FROM lyrics_failures ORDER BY song_id').fetchall()
            self.assertEqual(tuple(rows[0]), ('error', 1300, 0))
            self.assertEqual(tuple(rows[1]), ('error', None, 1))

    def test_sidecar_write_failure_keeps_database_lyrics_and_releases_owner(self):
        with self.connection() as conn:
            conn.execute("UPDATE songs SET lyrics='旧歌词' WHERE id=1")
        lyrics_retry.retry_song(1)
        with patch.object(lyrics_retry.lyrics, 'fetch_lyrics',
                          return_value={'plain': '新歌词', 'lrc': '[00:01]新歌词', 'source': 'netease'}), \
                patch.object(lyrics_retry.downloader, 'write_lrc_sidecar', side_effect=OSError('disk full')), \
                patch.object(lyrics_retry.logger, 'warning'):
            self.callbacks[0]()
        with self.connection() as conn:
            self.assertEqual(conn.execute('SELECT lyrics FROM songs WHERE id=1').fetchone()[0], '旧歌词')
        self.assertEqual(self.state()['status'], 'ready')
        self.assertIsNone(self.state()['next_retry_at'])
        self.assertFalse(importer.is_song_active(1))


if __name__ == '__main__':
    unittest.main()
