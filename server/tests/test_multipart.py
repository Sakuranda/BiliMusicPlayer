import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import ANY, patch

from fastapi import HTTPException
from app import artist_playlists, db, importer, main, downloader
from app.schemas import CollectionUpdate, PartSelection
from tests.test_data_safety import song_data

PARTS = [dict(cid=100+p, page=p, part=f'歌曲{p}', duration=p*60) for p in (1, 2, 3)]


class MultipartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'library.db'
        self.stack = ExitStack()
        for module in (db, importer, main, artist_playlists):
            self.stack.enter_context(patch.object(module, 'MUSIC_DIR', self.root))
        for module in (db, importer, main):
            self.stack.enter_context(patch.object(module, 'get_conn', side_effect=self.connect))
        self.stack.enter_context(patch.object(main, 'sync_artist_playlists_safely'))

    def tearDown(self):
        self.stack.close(); self.temp.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(db.SCHEMA)
        return conn

    def import_parts(self, policy='ask', refresh=None):
        items = [dict(bvid='BVMULTI', raw_title='【演唱者】《歌曲》', duration=360, uploader='剪辑号', cover_url='', cid=101)]
        with patch.object(importer.bilibili, 'parse_fav_url', return_value=['123']), patch.object(importer.bilibili, 'fetch_favorites', return_value=('歌单', copy.deepcopy(items), '123')), patch.object(importer.bilibili, 'fetch_tags', return_value=[]), patch.object(importer.bilibili, 'fetch_detail', return_value={'pages': PARTS, 'desc': '演唱：演唱者'}):
            if refresh:
                return importer.refresh_collection(refresh)
            job = importer.parse_and_store('https://example.com', save_collection=True, multipart_policy=policy)
        with closing(self.connect()) as conn:
            return job, db.list_job_songs(conn, job), db.list_collections(conn)[0]

    def test_independent_parts_and_reimport_keep_edits(self):
        _, songs, collection = self.import_parts()
        self.assertEqual([s['part_index'] for s in songs], [1, 2, 3])
        self.assertEqual(collection['pending_multipart_count'], 1)
        with closing(self.connect()) as conn:
            db.update_song(conn, songs[1]['id'], title='手工第二首', artist='手工歌手')
        _, again, _ = self.import_parts()
        self.assertEqual([s['id'] for s in again], [s['id'] for s in songs])
        self.assertEqual((again[1]['title'], again[1]['artist']), ('手工第二首', '手工歌手'))

    def test_collection_lists_known_multipart_even_after_choices_saved(self):
        _, songs, collection = self.import_parts()
        with closing(self.connect()) as conn:
            sid = db.insert_song(conn, song_data(bvid='BVSINGLE', job_id=None))
            db.replace_collection_songs(conn, collection['id'], [s['id'] for s in songs] + [sid])
            conn.execute('UPDATE songs SET download_requested=0'); conn.commit()
        self.assertEqual(len(main.collection_songs(collection['id'])), 4)
        self.assertEqual(len(main.collection_songs(collection['id'], multipart_only=True)), 3)
        current = main.collections()[0]
        self.assertEqual(current['pending_multipart_count'], 0)
        self.assertEqual(current['multipart_count'], 1)

    def test_refresh_does_not_silently_download_undecided_parts(self):
        _, songs, collection = self.import_parts()
        with patch.object(importer, 'start_download') as start:
            result = self.import_parts(refresh=collection['id'])
        start.assert_not_called()
        self.assertEqual(result['pending_multipart_count'], 1)
        with closing(self.connect()) as conn:
            self.assertTrue(all(s['download_requested'] is None for s in db.list_collection_songs(conn, collection['id'])))

    def test_default_policies_queue_exact_track_ids(self):
        for policy, pages in [('first', [1]), ('all', [1, 2, 3])]:
            with closing(self.connect()) as conn:
                conn.execute('DELETE FROM collections'); conn.execute('DELETE FROM songs'); conn.commit()
            _, songs, collection = self.import_parts(policy)
            expected = [s['id'] for s in songs if s['part_index'] in pages]
            with patch.object(importer, 'start_download', return_value={'started': True, 'queued': len(expected)}) as start:
                self.import_parts(policy, collection['id'])
            start.assert_called_once_with(ANY, fetch_lyrics=True, song_ids=expected)

    def test_partial_choice_preserves_files_and_skip_all_persists(self):
        _, songs, collection = self.import_parts()
        with closing(self.connect()) as conn:
            for song in songs:
                path = self.root / f"{song['id']}.m4a"; path.write_bytes(b'audio')
                db.update_song(conn, song['id'], file_path=path.name, downloaded_cid=song['cid'], status='ready')
        result = main.collection_parts(collection['id'], PartSelection(song_ids=[songs[1]['id']], bvids=['BVMULTI']))
        self.assertEqual(result['queued'], 0)
        with closing(self.connect()) as conn:
            again = db.list_collection_songs(conn, collection['id'])
        self.assertEqual([s['download_requested'] for s in again], [0, 1, 0])
        self.assertTrue(all((self.root / s['file_path']).is_file() for s in again))
        with self.assertRaises(HTTPException):
            main.collection_parts(collection['id'], PartSelection(song_ids=[999], bvids=['BVMULTI']))
        main.collection_parts(collection['id'], PartSelection(song_ids=[], bvids=['BVMULTI']))
        with patch.object(importer, 'start_download') as start:
            self.import_parts(refresh=collection['id'])
        start.assert_not_called()

    def test_old_p2_expands_full_list_without_changing_audio_or_edits(self):
        with closing(self.connect()) as conn:
            collection = db.upsert_collection(conn, media_id='123', url='url', title='旧收藏夹', album='专辑', auto_update=True)
            sid = db.insert_song(conn, song_data(bvid='BVOLD', job_id=None, cid=102, part_index=2, parts=json.dumps(PARTS), title='用户第二首', artist='用户歌手'))
            db.update_song(conn, sid, file_path='old.m4a', downloaded_cid=102, status='ready')
            db.replace_collection_songs(conn, collection['id'], [sid])
            (self.root / 'old.m4a').write_bytes(b'keep audio')
            self.assertEqual(importer.materialize_cached_parts(conn), 2)
            self.assertEqual(importer.materialize_cached_parts(conn), 0)
            self.assertEqual(len(db.list_collection_songs(conn, collection['id'])), 3)
            old = db.get_song(conn, sid)
            self.assertEqual((old['title'], old['artist'], old['cid'], old['file_path']), ('用户第二首', '用户歌手', 102, 'old.m4a'))
            self.assertEqual(db.list_collections(conn)[0]['pending_multipart_count'], 1)
            self.assertEqual((self.root / 'old.m4a').read_bytes(), b'keep audio')

    def test_migration_keeps_relations_and_deleted_id_high_water(self):
        path = self.root / 'old.db'
        legacy = db.SCHEMA.replace('bvid        TEXT NOT NULL,', 'bvid        TEXT UNIQUE NOT NULL,').replace('    UNIQUE (bvid, part_index)', '    CHECK (id > 0)')
        conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
        conn.executescript(legacy)
        conn.execute("INSERT INTO songs (id,bvid,title,artist,part_index,created_at) VALUES (7,'BVOLD','旧歌','旧歌手',2,'old')")
        conn.execute("INSERT INTO songs (id,bvid,title,artist,created_at) VALUES (999,'BVDELETED','删掉的歌','歌手','old')")
        conn.execute('DELETE FROM songs WHERE id=999')
        conn.execute("INSERT INTO playlists (id,name,created_at) VALUES (1,'歌单','old')")
        conn.execute("INSERT INTO playlist_songs (playlist_id,song_id,added_at) VALUES (1,7,'old')")
        conn.commit()
        with patch.object(db, 'DB_PATH', path):
            db.migrate_multipart(conn); db.migrate_multipart(conn)
        self.assertTrue(path.with_suffix('.pre-multipart.bak').is_file())
        self.assertEqual(conn.execute('SELECT song_id FROM playlist_songs').fetchone()[0], 7)
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertGreater(db.insert_song(conn, song_data(bvid='BVOLD', part_index=1)), 999)
        conn.close()

    def test_artist_exports_follow_exact_membership_after_rename(self):
        _, songs, _ = self.import_parts()
        with closing(self.connect()) as conn:
            for song in songs:
                path = self.root / f"{song['id']}.m4a"; path.write_bytes(b'audio')
                db.update_song(conn, song['id'], artist='歌手甲', file_path=path.name, status='ready')
        self.assertEqual(artist_playlists.sync_artist_playlists(), 1)
        files = list((self.root / 'MusicPlayer-Artists').glob('*.nsp'))
        payload = json.loads(files[0].read_text())
        self.assertEqual(payload['name'], '歌手 · 歌手甲')
        self.assertEqual(len(payload['all'][1]['any']), 3)
        with closing(self.connect()) as conn:
            db.update_song(conn, songs[1]['id'], artist='歌手乙')
        self.assertEqual(artist_playlists.sync_artist_playlists(), 2)
        self.assertEqual(len(json.loads(files[0].read_text())['all'][1]['any']), 2)

    def test_cover_failure_keeps_original_reference(self):
        _, songs, _ = self.import_parts()
        with closing(self.connect()) as conn:
            db.update_song(conn, songs[0]['id'], cover_url='old.jpg')
        with self.assertLogs('uvicorn.error', level='ERROR'), patch.object(downloader, 'fetch_cover', side_effect=ValueError('bad image')):
            with self.assertRaises(HTTPException):
                main.refresh_song_cover(songs[0]['id'])
        with closing(self.connect()) as conn:
            self.assertEqual(db.get_song(conn, songs[0]['id'])['cover_url'], 'old.jpg')

    def test_policy_update_accepts_strings_and_retains_manual_choices(self):
        _, songs, collection = self.import_parts()
        updated = main.collection_update(collection['id'], CollectionUpdate(multipart_policy='all'))
        self.assertEqual(updated['multipart_policy'], 'all')
        main.collection_parts(collection['id'], PartSelection(song_ids=[], bvids=['BVMULTI']))
        with patch.object(importer, 'start_download') as start:
            self.import_parts('all', collection['id'])
        start.assert_not_called()

    def test_queue_rejects_foreign_ids_before_starting_workers(self):
        job, songs, _ = self.import_parts()
        with self.assertRaises(importer.ImportError):
            importer.start_download(job, song_ids=[99999])
        self.assertFalse(importer.is_job_active(job))

    def test_cover_success_updates_embedded_and_keeps_shared_original(self):
        _, songs, _ = self.import_parts()
        covers = self.root / 'covers'; covers.mkdir()
        (covers / 'old.jpg').write_bytes(b'original')
        (self.root / 'song.m4a').write_bytes(b'audio')
        with closing(self.connect()) as conn:
            db.update_song(conn, songs[0]['id'], file_path='song.m4a', cover_url='old.jpg')
            db.update_song(conn, songs[1]['id'], cover_url='old.jpg')
        with patch.object(main, 'COVER_DIR', covers), patch.object(downloader, 'COVER_DIR', covers), patch.object(downloader, 'fetch_cover', return_value=b'new cover'), patch.object(downloader, 'update_embedded_cover') as embed:
            updated = main.refresh_song_cover(songs[0]['id'])
        embed.assert_called_once_with(self.root / 'song.m4a', b'new cover')
        self.assertEqual((covers / updated['cover_url']).read_bytes(), b'new cover')
        self.assertTrue((covers / 'old.jpg').exists())
        self.assertIn(updated['cover_url'], updated['cover'])
        self.assertEqual((self.root / 'song.m4a').read_bytes(), b'audio')

    def test_embedded_cover_failure_does_not_publish_new_cache(self):
        _, songs, _ = self.import_parts()
        covers = self.root / 'covers'; covers.mkdir()
        (covers / 'old.jpg').write_bytes(b'original')
        (self.root / 'song.m4a').write_bytes(b'audio')
        with closing(self.connect()) as conn:
            db.update_song(conn, songs[0]['id'], file_path='song.m4a', cover_url='old.jpg')
        with self.assertLogs('uvicorn.error', level='ERROR'), patch.object(main, 'COVER_DIR', covers), patch.object(downloader, 'COVER_DIR', covers), patch.object(downloader, 'fetch_cover', return_value=b'new cover'), patch.object(downloader, 'update_embedded_cover', side_effect=OSError('write failure')):
            with self.assertRaises(HTTPException):
                main.refresh_song_cover(songs[0]['id'])
        self.assertEqual([p.name for p in covers.iterdir()], ['old.jpg'])
        with closing(self.connect()) as conn:
            self.assertEqual(db.get_song(conn, songs[0]['id'])['cover_url'], 'old.jpg')

    def test_cover_uses_current_favorite_picture_when_view_is_blocked(self):
        _, songs, _ = self.import_parts()
        covers = self.root / 'covers'; covers.mkdir()
        picture = 'https://i0.hdslb.com/current.jpg'
        with patch.object(main, 'COVER_DIR', covers), patch.object(downloader, 'COVER_DIR', covers), patch.object(downloader, 'fetch_cover', side_effect=[ValueError('没有封面'), b'current cover']) as fetch, patch.object(main.bilibili, 'fetch_favorites', return_value=('歌单', [{'bvid': 'BVMULTI', 'cover_url': picture}], '123')) as favorites:
            updated = main.refresh_song_cover(songs[0]['id'])
        self.assertEqual((covers / updated['cover_url']).read_bytes(), b'current cover')
        self.assertEqual(fetch.call_args.kwargs, {'cover_url': picture})
        self.assertEqual(favorites.call_args.args[0], ['123'])
