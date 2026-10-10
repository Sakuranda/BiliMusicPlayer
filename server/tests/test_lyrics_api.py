import asyncio
import io
import sqlite3
import unittest
from unittest.mock import patch

from starlette.datastructures import UploadFile

from app import main
from app.db import SCHEMA


class LyricsApiTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO songs (bvid, title, artist, status, created_at) VALUES (?,?,?,?,?)",
            ("BVTEST", "测试歌", "测试歌手", "ready", "2026-08-19T00:00:00Z"),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_upload_and_delete_lyrics(self):
        upload = UploadFile(
            filename="test.lrc",
            file=io.BytesIO("[00:01.00]第一句\n[00:02.00]第二句".encode()),
        )
        with patch.object(main, "get_conn", return_value=self.conn):
            song = asyncio.run(main.song_lyrics_upload(1, upload))
            self.assertEqual(song["lyrics_source"], "upload")
            self.assertEqual(song["lyrics"], "第一句\n第二句")
            self.assertTrue(song["lyrics_enabled"])

            cleared = main.song_lyrics_delete(1)
            self.assertIsNone(cleared["lrc"])
            self.assertIsNone(cleared["lyrics_source"])


if __name__ == "__main__":
    unittest.main()
