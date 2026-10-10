"""Publish performer categories to Navidrome/Amperfy without changing album tags."""
import hashlib
import json
import logging
import threading
from contextlib import closing

from . import db, downloader
from .config import MUSIC_DIR

_lock = threading.Lock()
logger = logging.getLogger(__name__)


def sync_artist_playlists() -> int:
    # Serialize the snapshot and file writes together, so concurrent downloads
    # cannot publish an older snapshot after a metadata edit.
    with _lock, closing(db.get_conn()) as conn:
        groups: dict[str, list[str]] = {}
        for song in db.list_songs(conn):
            if song.get("file_path") and (MUSIC_DIR / song["file_path"]).is_file():
                groups.setdefault(song["artist"] or "未知歌手", []).append(song["file_path"])
        folder = MUSIC_DIR / "MusicPlayer-Artists"
        folder.mkdir(parents=True, exist_ok=True)
        wanted = set()
        for artist, paths in groups.items():
            key = hashlib.sha256(artist.encode()).hexdigest()[:20]
            target = folder / f"artist-{key}.nsp"
            wanted.add(target)
            content = json.dumps({
                "name": f"歌手 · {artist}", "comment": "MusicPlayer 自动按演唱者分类，修改歌曲信息后自动更新。",
                "all": [{"is": {"missing": False}}, {"any": [{"is": {"filepath": p}} for p in paths]}],
                "sort": "title",
            }, ensure_ascii=False, indent=2) + "\n"
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                downloader._atomic_write(target, lambda temp: temp.write_text(content, encoding="utf-8"))
        # Empty old categories before removal by the user in their client. Deleting
        # the source alone can leave an imported playlist with stale members.
        for target in folder.glob("artist-*.nsp"):
            if target not in wanted:
                old = json.loads(target.read_text(encoding="utf-8"))
                old["all"] = [{"is": {"filepath": ""}}]
                content = json.dumps(old, ensure_ascii=False, indent=2) + "\n"
                if target.read_text(encoding="utf-8") != content:
                    downloader._atomic_write(target, lambda temp: temp.write_text(content, encoding="utf-8"))
        return len(groups)


def sync_artist_playlists_safely() -> None:
    try:
        sync_artist_playlists()
    except Exception:
        logger.exception("客户端歌手分类同步失败，下次启动或手动同步可重试")
