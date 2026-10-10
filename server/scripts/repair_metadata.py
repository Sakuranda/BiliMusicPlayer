"""Run offline: python -m scripts.repair_metadata [--album NAME] [--apply]."""
import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, MUSIC_DIR
from app.downloader import metadata_needs_update, update_audio_metadata


def repair(conn: sqlite3.Connection, music_dir: Path, backup: Path | None = None,
           album: str | None = None) -> tuple[int, int]:
    """Only touch registered files; preserve paths, audio packets and lyrics."""
    sql = "SELECT id,file_path,title,artist,album FROM songs WHERE file_path IS NOT NULL"
    params = ()
    if album is not None:
        sql += " AND album=?"
        params = (album,)
    count = failed = 0
    for row in conn.execute(sql, params).fetchall():
        try:
            path = (music_dir / row["file_path"]).resolve()
            if not path.is_relative_to(music_dir.resolve()):
                raise ValueError("音频路径超出曲库")
            title, artist, album_name = row["title"] or "", row["artist"] or "", row["album"] or ""
            if not metadata_needs_update(path, title, artist, album_name):
                continue
            count += 1
            if backup is not None:
                saved = backup / "music" / path.relative_to(music_dir.resolve())
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
                update_audio_metadata(path, title=title, artist=artist, album=album_name)
            print(f"{'repaired' if backup is not None else 'needs-repair'} id={row['id']}", flush=True)
        except Exception as exc:
            failed += 1
            print(f"failed id={row['id']} error={exc}", flush=True)
    return count, failed


def main():
    parser = argparse.ArgumentParser(description="检查/修复 iOS 合辑标签；默认只检查，执行前停止 API 写入")
    parser.add_argument("--apply", action="store_true", help="备份数据库和待修复音频后写入标签")
    parser.add_argument("--album", help="仅修复此专辑名（精确匹配，默认全部）")
    args = parser.parse_args()
    db_path = DATA_DIR / "musicplayer.db"
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM jobs WHERE status IN ('parsing','downloading') LIMIT 1").fetchone():
            raise SystemExit("有导入/下载任务未完成，请稍后重试")
        backup = None
        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = DATA_DIR / "backups" / f"metadata-{stamp}"
            backup.mkdir(parents=True, exist_ok=False)
            with sqlite3.connect(backup / "musicplayer.db") as dest:
                conn.backup(dest)
            print(f"backup={backup}", flush=True)
        count, failed = repair(conn, MUSIC_DIR, backup, args.album)
        print(f"changed={count} failed={failed} apply={args.apply}", flush=True)
        if failed:
            raise SystemExit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
