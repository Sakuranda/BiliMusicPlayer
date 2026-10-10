import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app import db, importer, main
from app.schemas import SongUpdate
from tests.test_data_safety import song_data


class MetadataPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        with closing(self.connect()) as conn:
            self.sid = db.insert_song(conn, song_data(
                title='手工歌名', artist='真实演唱者', tags='["真实演唱者", "烟花"]',
                raw_title='这句歌词令人泪目', metadata=json.dumps({
                    'title_source': 'manual', 'artist_source': 'manual',
                    'title_confidence': 1, 'artist_confidence': 1,
                }),
            ))

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        return conn

    def test_reimport_preserves_manual_metadata_even_when_enriching(self):
        with closing(self.connect()) as conn:
            db.insert_song(conn, song_data(title='新猜测', artist='切片UP', description='新简介'))
            song = db.get_song(conn, self.sid)
        self.assertEqual((song['title'], song['artist']), ('手工歌名', '真实演唱者'))
        self.assertEqual(song['metadata']['artist_source'], 'manual')
        self.assertEqual(song['description'], '新简介')

    def test_analyze_is_a_read_only_suggestion(self):
        with patch.object(main, 'get_conn', side_effect=self.connect):
            suggestion = main.song_analyze(self.sid)
        self.assertEqual(suggestion['artist'], '真实演唱者')
        with closing(self.connect()) as conn:
            self.assertEqual(db.get_song(conn, self.sid)['title'], '手工歌名')

    def test_failed_retag_does_not_change_database(self):
        with patch.object(main, 'get_conn', side_effect=self.connect), patch.object(main, '_retag_file', side_effect=HTTPException(503, 'tag error')):
            with self.assertRaises(HTTPException):
                main.song_update(self.sid, SongUpdate(title='改名'))
        with closing(self.connect()) as conn:
            self.assertEqual(db.get_song(conn, self.sid)['title'], '手工歌名')

    def test_manual_save_records_trusted_artist(self):
        with patch.object(main, 'get_conn', side_effect=self.connect):
            song = main.song_update(self.sid, SongUpdate(title='烟花', artist='真正歌手'))
        self.assertEqual(song['metadata']['artist_source'], 'manual')
        self.assertFalse(song['metadata']['needs_review'])

    def test_editing_active_download_is_rejected(self):
        with patch.object(main, 'is_song_active', return_value=True):
            with self.assertRaises(HTTPException) as raised:
                main.song_update(self.sid, SongUpdate(title='change'))
        self.assertEqual(raised.exception.status_code, 409)

    def test_track_identity_cannot_be_changed_to_another_part(self):
        with closing(self.connect()) as conn:
            db.update_song(conn, self.sid, parts=json.dumps([{'cid': 222, 'page': 2, 'part': '第二首', 'duration': 42}]))
        with patch.object(main, 'get_conn', side_effect=self.connect):
            with self.assertRaises(HTTPException):
                main.song_update(self.sid, SongUpdate(cid=333))
            with self.assertRaises(HTTPException):
                main.song_update(self.sid, SongUpdate(cid=222, part_index=99, duration=999))
            song = main.song_update(self.sid, SongUpdate(part_index=99, duration=999))
        self.assertEqual((song['cid'], song['part_index'], song['duration']), (1, 1, 120))

    def test_description_and_evidence_are_stored_on_import(self):
        items = [{'bvid': 'BVNEW', 'raw_title': '《烟花》翻唱', 'uploader': '剪辑号', 'cover_url': '', 'duration': 120, 'cid': 10}]
        with patch.object(importer, 'get_conn', side_effect=self.connect), patch.object(importer.bilibili, 'parse_fav_url', return_value=['123']), patch.object(importer.bilibili, 'fetch_favorites', return_value=('歌单', items, '123')), patch.object(importer.bilibili, 'fetch_tags', return_value=['烟花', '真实演唱者']), patch.object(importer.bilibili, 'fetch_detail', return_value={'desc': '演唱：真实演唱者', 'cid': 10, 'duration': 120}):
            job = importer.parse_and_store('https://example.com')
        with closing(self.connect()) as conn:
            song = db.list_songs(conn, job_id=job)[0]
        self.assertEqual(song['description'], '演唱：真实演唱者')
        self.assertEqual(song['artist'], '真实演唱者')
        self.assertIsInstance(song['metadata']['needs_review'], bool)

    def test_weak_artist_candidate_does_not_create_artist_group(self):
        items = [{'bvid': 'BVNEW', 'raw_title': '【小雾翻唱】《烟花》', 'uploader': '剪辑号', 'cover_url': '', 'duration': 120, 'cid': 10}]
        with patch.object(importer, 'get_conn', side_effect=self.connect), patch.object(importer.bilibili, 'parse_fav_url', return_value=['123']), patch.object(importer.bilibili, 'fetch_favorites', return_value=('歌单', items, '123')), patch.object(importer.bilibili, 'fetch_tags', return_value=[]), patch.object(importer.bilibili, 'fetch_detail', return_value={'cid': 10, 'duration': 120}):
            job = importer.parse_and_store('https://example.com')
        with closing(self.connect()) as conn:
            song = db.list_songs(conn, job_id=job)[0]
        self.assertEqual(song['artist'], '未知歌手')
        self.assertEqual(song['metadata']['artist'], '小雾翻唱')
        self.assertLess(song['metadata']['artist_confidence'], 0.8)
        self.assertTrue(song['metadata']['needs_review'])
        # 人工核对并保存候选后，才将其作为实际歌手与后续可信依据。
        with patch.object(main, 'get_conn', side_effect=self.connect):
            confirmed = main.song_update(song['id'], SongUpdate(artist='小雾'))
        self.assertEqual(confirmed['artist'], '小雾')
        self.assertEqual(confirmed['metadata']['artist_source'], 'manual')

    def test_sync_retries_existing_failed_members(self):
        with closing(self.connect()) as conn:
            collection = db.upsert_collection(conn, media_id='123', url='https://example.com', title='歌单', album='歌单', auto_update=True)
            db.create_job(conn, 'retry', collection['url'], '123', '歌单', 1, collection['id'])
            db.add_job_song(conn, 'retry', self.sid, 0, False)
            db.update_song(conn, self.sid, status='error')
        with patch.object(importer, 'get_conn', side_effect=self.connect), patch.object(importer, 'parse_and_store', return_value='retry'), patch.object(importer, 'start_download', return_value={'started': True}) as start:
            importer.refresh_collection(collection['id'])
        start.assert_called_once_with('retry', fetch_lyrics=True, song_ids=[self.sid])

    def test_connection_failure_releases_collection_reservation(self):
        with patch.object(importer, 'get_conn', side_effect=RuntimeError('cannot open DB')):
            with self.assertRaises(RuntimeError):
                importer.refresh_collection(999)
        self.assertNotIn(999, importer._active_collection_ids)

    def test_parallel_mutations_cannot_overwrite_stale_song_snapshot(self):
        with importer.reserve_song_edit(self.sid):
            with patch.object(main, 'get_conn', side_effect=self.connect):
                with self.assertRaises(HTTPException) as raised:
                    main.song_update(self.sid, SongUpdate(artist='stale writer'))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(importer.is_song_active(self.sid))


if __name__ == '__main__':
    unittest.main()
