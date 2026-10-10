import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import importer
from app.db import (SCHEMA, add_job_song, create_job, insert_song,
                    list_collection_songs, list_collections,
                    replace_collection_songs, upsert_collection)


def song_data(bvid: str) -> dict:
    return {
        "bvid": bvid,
        "job_id": None,
        "title": bvid,
        "artist": "歌手",
        "album": "收藏夹",
        "duration": 120,
        "cid": 1,
        "part_index": 1,
        "part_title": "P1",
        "parts": None,
        "source_url": f"https://www.bilibili.com/video/{bvid}",
        "raw_title": bvid,
        "uploader": "UP",
        "tags": "[]",
        "cover_url": None,
    }


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "collections.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        return conn

    def test_collection_membership_is_separate_from_audio(self):
        conn = self.get_conn()
        collection = upsert_collection(
            conn,
            media_id="123",
            url="https://example.com/fav?fid=123",
            title="每日歌单",
            album="每日歌单",
            auto_update=True,
        )
        first = insert_song(conn, song_data("BVFIRST"))
        second = insert_song(conn, song_data("BVSECOND"))
        replace_collection_songs(conn, collection["id"], [second, first])

        self.assertEqual(
            [song["id"] for song in list_collection_songs(conn, collection["id"])],
            [second, first],
        )
        summary = list_collections(conn)[0]
        self.assertTrue(summary["auto_update"])
        self.assertEqual(summary["song_count"], 2)

        conn.execute("DELETE FROM collections WHERE id = ?", (collection["id"],))
        conn.commit()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0], 2)
        conn.close()

    def test_refresh_downloads_only_new_members(self):
        conn = self.get_conn()
        collection = upsert_collection(
            conn,
            media_id="123",
            url="https://example.com/fav?fid=123",
            title="每日歌单",
            album="每日歌单",
            auto_update=True,
        )
        old_id = insert_song(conn, song_data("BVOLD"))
        new_id = insert_song(conn, song_data("BVNEW"))
        create_job(conn, "job", collection["url"], "123", "每日歌单", 2, collection["id"])
        add_job_song(conn, "job", old_id, 0, False)
        add_job_song(conn, "job", new_id, 1, True)
        existing_file = Path(self.tempdir.name) / "old.m4a"
        existing_file.write_bytes(b"audio")
        conn.execute("UPDATE songs SET file_path=?, status='ready' WHERE id=?", (str(existing_file), old_id))
        conn.commit()
        conn.close()

        with (
            patch.object(importer, "get_conn", side_effect=self.get_conn),
            patch.object(importer, "parse_and_store", return_value="job"),
            patch.object(importer, "start_download", return_value={
                "started": True, "queued": 1, "concurrency": 3, "message": "已排队 1 首",
            }) as start,
        ):
            result = importer.refresh_collection(collection["id"])

        self.assertEqual(result["queued"], 1)
        start.assert_called_once_with("job", fetch_lyrics=True, song_ids=[new_id])

    def test_daily_schedule_waits_full_day(self):
        current = datetime.now(timezone.utc)
        self.assertFalse(importer._collection_due({
            "auto_update": True,
            "last_checked_at": (current - timedelta(hours=23)).isoformat(),
        }, current))
        self.assertTrue(importer._collection_due({
            "auto_update": True,
            "last_checked_at": (current - timedelta(hours=25)).isoformat(),
        }, current))
        self.assertFalse(importer._collection_due({
            "auto_update": False,
            "last_checked_at": None,
        }, current))

    def test_saved_sync_enriches_and_marks_only_new_member(self):
        conn = self.get_conn()
        collection = upsert_collection(
            conn,
            media_id="123",
            url="https://example.com/fav?fid=123",
            title="每日歌单",
            album="每日歌单",
            auto_update=True,
        )
        old_id = insert_song(conn, {**song_data("BVOLD"), "title": "用户改过的歌名"})
        conn.execute(
            "UPDATE songs SET file_path='每日歌单/old.m4a', status='ready', downloaded_cid=1 "
            "WHERE id=?",
            (old_id,),
        )
        replace_collection_songs(conn, collection["id"], [old_id])
        conn.commit()
        conn.close()

        items = [
            {"bvid": "BVOLD", "raw_title": "旧歌原标题更新", "cover_url": "old.jpg",
             "duration": 120, "uploader": "UP", "uploader_mid": 1, "cid": 1},
            {"bvid": "BVNEW", "raw_title": "新歌", "cover_url": "new.jpg",
             "duration": 180, "uploader": "UP", "uploader_mid": 1, "cid": 2},
        ]
        with (
            patch.object(importer, "get_conn", side_effect=self.get_conn),
            patch.object(importer.bilibili, "parse_fav_url", return_value=["123"]),
            patch.object(importer.bilibili, "fetch_favorites", return_value=("每日歌单", items, "123")),
            patch.object(importer.bilibili, "fetch_tags", return_value=["翻唱"]) as tags,
            patch.object(importer.bilibili, "fetch_detail", return_value={
                "pages": [{"cid": 2, "page": 1, "part": "P1", "duration": 180}],
            }) as detail,
            patch.object(importer, "_cookie_file", return_value=None),
        ):
            job_id = importer.parse_and_store(
                collection["url"], save_collection=True, auto_update=True,
                collection_id=collection["id"],
            )

        detail.assert_called_once_with("BVNEW", None)
        tags.assert_called_once_with("BVNEW", None)
        conn = self.get_conn()
        flags = conn.execute(
            "SELECT s.bvid, js.is_new FROM job_songs js JOIN songs s ON s.id=js.song_id "
            "WHERE js.job_id=? ORDER BY js.position",
            (job_id,),
        ).fetchall()
        self.assertEqual([tuple(row) for row in flags], [("BVOLD", 0), ("BVNEW", 1)])
        self.assertEqual(
            conn.execute("SELECT title FROM songs WHERE bvid='BVOLD'").fetchone()[0],
            "用户改过的歌名",
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
