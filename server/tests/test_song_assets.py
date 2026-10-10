import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch
from app import db, main, lyrics, downloader
from tests.test_data_safety import song_data

class SongAssetRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(main, 'get_conn', side_effect=self.connect))
        self.stack.enter_context(patch.object(main, 'MUSIC_DIR', self.root))
        self.stack.enter_context(patch.object(main, '_song_edit'))
        with closing(self.connect()) as conn:
            self.sid = db.insert_song(conn, song_data(bvid='BVassets',job_id=None))

    def tearDown(self):
        self.stack.close(); self.temp.cleanup()

    def connect(self):
        conn=sqlite3.connect(self.root/'songs.db'); conn.row_factory=sqlite3.Row
        conn.executescript(db.SCHEMA)
        return conn

    def test_no_match_preserves_existing_lyrics(self):
        with closing(self.connect()) as conn:
            db.update_song(conn,self.sid,lyrics='original',lrc='[00:01]original',lyrics_source='lrclib')
        with patch.object(lyrics,'fetch_lyrics',return_value={'plain':'','lrc':'','source':''}):
            result=main.refresh_song_lyrics(self.sid)
        self.assertFalse(result['matched'])
        self.assertEqual(result['song']['lyrics'],'original')

    def test_uploaded_lyrics_are_never_replaced(self):
        with closing(self.connect()) as conn:
            db.update_song(conn,self.sid,lyrics='manual',lyrics_source='upload')
        with patch.object(lyrics,'fetch_lyrics') as fetch:
            result=main.refresh_song_lyrics(self.sid)
        fetch.assert_not_called()
        self.assertEqual(result['song']['lyrics'],'manual')

    def test_match_syncs_existing_audio_without_downloading_it(self):
        (self.root/'audio.m4a').write_bytes(b'audio')
        with closing(self.connect()) as conn:
            db.update_song(conn,self.sid,file_path='audio.m4a',status='ready')
        with patch.object(lyrics,'fetch_lyrics',return_value={'plain':'line','lrc':'[00:01]line','source':'lrclib'}), patch.object(downloader,'update_embedded_lyrics') as embed, patch.object(downloader,'write_lrc_sidecar') as sidecar, patch.object(downloader,'download_audio') as download:
            result=main.refresh_song_lyrics(self.sid)
        self.assertTrue(result['matched'])
        embed.assert_called_once_with('audio.m4a','[00:01]line')
        sidecar.assert_called_once_with('audio.m4a','[00:01]line')
        download.assert_not_called()
        self.assertEqual(result['song']['lyrics_source'],'lrclib')
