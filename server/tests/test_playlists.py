import sqlite3
import unittest

from app import db


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(db.SCHEMA)
        for index in range(1, 4):
            self.conn.execute(
                "INSERT INTO songs (bvid, title, artist, status, created_at) "
                "VALUES (?, ?, ?, 'ready', ?)",
                (f"BV{index}", f"歌曲 {index}", "歌手", db.now()),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_playlist_crud_and_order(self):
        playlist = db.create_playlist(self.conn, "夜间歌单")
        self.assertEqual(playlist["song_count"], 0)
        self.assertTrue(db.add_song_to_playlist(self.conn, playlist["id"], 2))
        self.assertTrue(db.add_song_to_playlist(self.conn, playlist["id"], 1))
        self.assertFalse(db.add_song_to_playlist(self.conn, playlist["id"], 2))

        songs = db.list_playlist_songs(self.conn, playlist["id"])
        self.assertEqual([song["id"] for song in songs], [2, 1])
        self.assertEqual(db.list_playlists(self.conn)[0]["song_count"], 2)

        renamed = db.rename_playlist(self.conn, playlist["id"], "通勤")
        self.assertEqual(renamed["name"], "通勤")
        self.assertTrue(db.remove_song_from_playlist(self.conn, playlist["id"], 2))
        self.assertEqual(len(db.list_playlist_songs(self.conn, playlist["id"])), 1)
        self.assertTrue(db.delete_playlist(self.conn, playlist["id"]))

    def test_song_delete_cascades_playlist_membership(self):
        playlist = db.create_playlist(self.conn, "收藏")
        db.add_song_to_playlist(self.conn, playlist["id"], 1)
        db.delete_song(self.conn, 1)
        self.assertEqual(db.list_playlist_songs(self.conn, playlist["id"]), [])


if __name__ == "__main__":
    unittest.main()
