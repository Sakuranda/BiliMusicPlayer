"""Run in container: python -m scripts.repair_media [--apply]."""
import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import DATA_DIR, MUSIC_DIR
from app.media import needs_normalization, normalize_m4a


def main():
    parser = argparse.ArgumentParser(description='检查并无损修复分片/不适合流播的 M4A；默认只检查')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    conn = sqlite3.connect(DATA_DIR / 'musicplayer.db')
    conn.row_factory = sqlite3.Row
    if conn.execute("SELECT 1 FROM jobs WHERE status='downloading' LIMIT 1").fetchone():
        raise SystemExit('有下载任务正在运行，请稍后重试')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = DATA_DIR / 'backups' / f'media-{stamp}'
    if args.apply:
        backup.mkdir(parents=True, exist_ok=False)
        with sqlite3.connect(backup / 'musicplayer.db') as dest:
            conn.backup(dest)
        print(f'backup={backup}', flush=True)
    count = failed = 0
    for row in conn.execute('SELECT id,file_path FROM songs WHERE file_path IS NOT NULL').fetchall():
        path = (MUSIC_DIR / row['file_path']).resolve()
        if not path.is_relative_to(MUSIC_DIR.resolve()):
            raise ValueError('音频路径超出曲库')
        try:
            if not needs_normalization(path):
                continue
            count += 1
            if args.apply:
                saved = backup / row['file_path']
                saved.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replacement leaves the backup inode and active readers intact.
                os.link(path, saved)
                duration = normalize_m4a(path)
                with conn:
                    conn.execute('UPDATE songs SET duration=? WHERE id=?', (duration, row['id']))
                print(f"repaired id={row['id']} duration={duration:.3f}", flush=True)
            else:
                print(f"fragmented id={row['id']}")
        except Exception as exc:
            failed += 1
            print(f"failed id={row['id']} error={exc}", flush=True)
    conn.close()
    print(f'fragmented={count} failed={failed} apply={args.apply}', flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
