"""SQLite 存储：歌曲与导入任务。"""
import json
import sqlite3
import threading
from urllib.parse import quote
from datetime import datetime, timezone
from contextlib import closing

from .config import DATA_DIR, MUSIC_DIR, COVER_DIR, DB_PATH

_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_account (
    id INTEGER PRIMARY KEY CHECK(id=1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    session_epoch TEXT NOT NULL,
    totp_secret TEXT,
    pending_secret TEXT,
    pending_since REAL,
    last_totp_counter INTEGER NOT NULL DEFAULT -1
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    media_id    TEXT,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'parsed',   -- parsed / downloading / done / error
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    collection_id INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT NOT NULL,
    job_id      TEXT,
    title       TEXT NOT NULL,          -- 歌曲名（解析结果，可编辑）
    artist      TEXT NOT NULL,          -- 歌手/UP主/标签
    album       TEXT,
    duration    REAL,
    cid         INTEGER,                -- 选中的分P cid
    part_index  INTEGER DEFAULT 1,      -- 选中的第几P
    part_title  TEXT,                   -- 选中P的标题
    parts       TEXT,                   -- JSON: 全部分P [{cid,page,part,duration}]
    source_url  TEXT,                   -- B站视频完整链接（含分P参数）
    downloaded_cid INTEGER,             -- 实际下载过的分P cid（去重用）
    raw_title   TEXT,
    uploader    TEXT,
    tags        TEXT,                   -- JSON 数组
    description TEXT,                  -- 视频简介，保留识别证据
    metadata    TEXT,                  -- JSON: 识别来源、置信度与待核对原因
    cover_url   TEXT,
    file_path   TEXT,                   -- 相对 MUSIC_DIR 的路径
    lyrics      TEXT,                   -- 纯文本歌词
    lrc         TEXT,                   -- 逐行时间轴歌词
    lyrics_source TEXT,
    lyrics_enabled INTEGER NOT NULL DEFAULT 1, -- 是否自动匹配歌词
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/ready/error
    error       TEXT,
    download_requested INTEGER,        -- NULL: 多P待选择，0: 不下载，1: 下载
    created_at  TEXT NOT NULL,
    UNIQUE (bvid, part_index)
);

CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_songs (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    song_id     INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (playlist_id, song_id)
);

CREATE INDEX IF NOT EXISTS idx_playlist_songs_order
ON playlist_songs (playlist_id, position, added_at);

CREATE TABLE IF NOT EXISTS collections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id        TEXT UNIQUE NOT NULL,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    album           TEXT,
    auto_update     INTEGER NOT NULL DEFAULT 0,
    fetch_lyrics    INTEGER NOT NULL DEFAULT 1,
    multipart_policy TEXT NOT NULL DEFAULT 'ask',
    last_checked_at TEXT,
    last_updated_at TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_songs (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    song_id       INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (collection_id, song_id)
);

CREATE INDEX IF NOT EXISTS idx_collection_songs_order
ON collection_songs (collection_id, position);

CREATE TABLE IF NOT EXISTS job_songs (
    job_id   TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    song_id  INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    is_new   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, song_id)
);

CREATE INDEX IF NOT EXISTS idx_job_songs_order
ON job_songs (job_id, position);

CREATE TABLE IF NOT EXISTS access_sessions (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    ip            TEXT NOT NULL,
    country       TEXT,
    region        TEXT,
    city          TEXT,
    user_agent    TEXT,
    login_at      TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_access_sessions_login
ON access_sessions (login_at DESC);

CREATE INDEX IF NOT EXISTS idx_access_sessions_ip
ON access_sessions (ip);
"""

# 旧库迁移：补充分P相关列
MIGRATIONS = [
    "ALTER TABLE songs ADD COLUMN download_requested INTEGER",
    "ALTER TABLE collections ADD COLUMN multipart_policy TEXT NOT NULL DEFAULT 'ask'",
    "ALTER TABLE songs ADD COLUMN cid INTEGER",
    "ALTER TABLE songs ADD COLUMN part_index INTEGER DEFAULT 1",
    "ALTER TABLE songs ADD COLUMN part_title TEXT",
    "ALTER TABLE songs ADD COLUMN parts TEXT",
    "ALTER TABLE songs ADD COLUMN source_url TEXT",
    "ALTER TABLE songs ADD COLUMN downloaded_cid INTEGER",
    "ALTER TABLE songs ADD COLUMN lyrics_enabled INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE jobs ADD COLUMN collection_id INTEGER",
    "ALTER TABLE songs ADD COLUMN description TEXT",
    "ALTER TABLE songs ADD COLUMN metadata TEXT",
]


def get_conn() -> sqlite3.Connection:
    global _initialized
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # 独立连接共享 WAL，写入冲突时最多等待 30 秒，而不是立即抛 database is locked。
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        with _lock:
            if not _initialized:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(SCHEMA)
                for stmt in MIGRATIONS:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise
                        # 幂等迁移：仅忽略确定的“列已存在”，锁库/磁盘等错误必须暴露。
                migrate_multipart(conn)
                # 旧版用 songs.job_id 表示任务成员；迁移到多对多关系后，同一首歌
                # 可同时属于多个历史任务和收藏夹，不再因重复导入被重新归属。
                conn.execute(
                    "INSERT OR IGNORE INTO job_songs (job_id, song_id, position, is_new) "
                    "SELECT job_id, id, id, 0 FROM songs WHERE job_id IS NOT NULL "
                    "AND EXISTS (SELECT 1 FROM jobs WHERE jobs.id = songs.job_id)"
                )
                conn.commit()
                _initialized = True
    except Exception:
        conn.close()
        raise
    return conn


def migrate_multipart(conn: sqlite3.Connection) -> None:
    """Remove the legacy BV-only unique constraint while preserving IDs and relations."""
    indexes = conn.execute("PRAGMA index_list(songs)").fetchall()
    legacy = any(row[2] and [r[2] for r in conn.execute(f"PRAGMA index_info('{row[1]}')")] == ["bvid"] for row in indexes)
    if not legacy:
        return
    conn.commit()
    # A snapshot precedes the only table-rebuild migration. No media is moved.
    backup_path = DB_PATH.with_suffix(".pre-multipart.bak")
    if not backup_path.exists():
        with closing(sqlite3.connect(backup_path)) as backup:
            conn.backup(backup)
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='songs'").fetchone()
    high_water = sequence[0] if sequence else 0
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        table_sql = SCHEMA.split("CREATE TABLE IF NOT EXISTS songs (", 1)[1].split(";", 1)[0]
        conn.execute("CREATE TABLE songs_multipart (" + table_sql)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(songs)")]
        names = ",".join('"' + name + '"' for name in columns)
        conn.execute(f"INSERT INTO songs_multipart ({names}) SELECT {names} FROM songs")
        conn.execute("UPDATE songs_multipart SET part_index=1 WHERE part_index IS NULL")
        conn.execute("DROP TABLE songs")
        conn.execute("ALTER TABLE songs_multipart RENAME TO songs")
        conn.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='songs'", (high_water,))
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise sqlite3.IntegrityError("多P迁移后歌曲关系校验失败")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def recover_interrupted_downloads(conn: sqlite3.Connection) -> tuple[int, int]:
    """服务重启后把无法继续的内存任务标成错误，避免永久显示“下载中”。"""
    song_cur = conn.execute(
        "UPDATE songs SET status = 'error', "
        "error = '服务重启中断了下载，请重新导入或再次开始任务' "
        "WHERE status = 'downloading'"
    )
    job_cur = conn.execute(
        "UPDATE jobs SET status = 'error', "
        "message = '服务重启中断了下载，请重新开始' "
        "WHERE status = 'downloading'"
    )
    conn.commit()
    return song_cur.rowcount, job_cur.rowcount


def reconcile_downloaded_files(conn: sqlite3.Connection) -> int:
    """以磁盘文件为准修复旧任务留下的 pending/error 状态。"""
    repaired = 0
    rows = conn.execute(
        "SELECT id, file_path, status, error FROM songs WHERE file_path IS NOT NULL"
    ).fetchall()
    for row in rows:
        if (MUSIC_DIR / row["file_path"]).is_file() and (
            row["status"] != "ready" or row["error"] is not None
        ):
            conn.execute(
                "UPDATE songs SET status='ready', error=NULL WHERE id = ?", (row["id"],)
            )
            repaired += 1
    if repaired:
        conn.commit()
    return repaired


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _song_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("tags", "parts", "metadata"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                d[key] = {} if key == "metadata" else []
    d["cover"] = f"/api/songs/{d['id']}/cover?v={quote(str(d['cover_url']), safe='')}" if d.get("cover_url") else None
    d["lyrics_enabled"] = bool(d.get("lyrics_enabled", 1))
    return d


# ---------- jobs ----------

def create_job(conn: sqlite3.Connection, job_id: str, url: str, media_id: str,
               title: str, total: int, collection_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO jobs (id, url, media_id, title, status, total, collection_id, created_at) "
        "VALUES (?,?,?,?, 'parsed', ?, ?, ?)",
        (job_id, url, media_id, title, total, collection_id, now()),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def update_job(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))
    conn.commit()


# ---------- songs ----------

def insert_song(conn: sqlite3.Connection, data: dict) -> int:
    """插入歌曲；同 BV 同分P 已存在时更新元数据，保留音频文件与歌词（重新导入不丢数据）。"""
    data = dict(data)
    data["part_index"] = data.get("part_index") or 1
    if data.get("cid") is None:
        existing = get_song_by_bvid(conn, data["bvid"])
        if existing:
            data["part_index"] = existing.get("part_index") or 1
    cur = conn.execute(
        "INSERT INTO songs "
        "(bvid, job_id, title, artist, album, duration, cid, part_index, part_title, parts, "
        " source_url, raw_title, uploader, tags, description, metadata, cover_url, download_requested, status, created_at) "
        "VALUES (:bvid, :job_id, :title, :artist, :album, :duration, :cid, :part_index, "
        ":part_title, :parts, :source_url, :raw_title, :uploader, :tags, :description, :metadata, :cover_url, :download_requested, 'pending', :created_at) "
        "ON CONFLICT(bvid, part_index) DO UPDATE SET "
        "job_id=songs.job_id, title=songs.title, artist=songs.artist, "
        "album=songs.album, duration=COALESCE(excluded.duration, songs.duration), "
        "cid=COALESCE(excluded.cid, songs.cid), "
        "part_index=CASE WHEN excluded.cid IS NULL THEN songs.part_index ELSE excluded.part_index END, "
        "part_title=CASE WHEN excluded.cid IS NULL THEN songs.part_title ELSE excluded.part_title END, "
        "parts=COALESCE(excluded.parts, songs.parts), "
        "source_url=CASE WHEN excluded.cid IS NULL AND songs.source_url IS NOT NULL "
        "THEN songs.source_url ELSE excluded.source_url END, raw_title=excluded.raw_title, "
        "uploader=excluded.uploader, tags=excluded.tags, "
        "description=COALESCE(excluded.description, songs.description), "
        "cover_url=CASE "
        "WHEN songs.cover_url IS NOT NULL AND songs.cover_url NOT LIKE 'http%' "
        "THEN songs.cover_url ELSE excluded.cover_url END",
        {"description": None, "metadata": None, "download_requested": None, **data, "created_at": now()},
    )
    conn.commit()
    # SQLite 在 ON CONFLICT UPDATE 时 lastrowid 可能是 0 或上一条插入的 id。
    # 始终按 BV 与分P 回查，保证调用者拿到真实歌曲。
    return conn.execute("SELECT id FROM songs WHERE bvid = ? AND part_index = ?", (data["bvid"], data.get("part_index") or 1)).fetchone()[0]


def get_song(conn: sqlite3.Connection, sid: int) -> dict | None:
    row = conn.execute("SELECT * FROM songs WHERE id = ?", (sid,)).fetchone()
    return _song_row(row) if row else None


def list_songs(conn: sqlite3.Connection, job_id: str | None = None,
               status: str | None = None) -> list[dict]:
    q = "SELECT songs.* FROM songs"
    conds, args = [], []
    if job_id:
        has_memberships = conn.execute(
            "SELECT 1 FROM job_songs WHERE job_id = ? LIMIT 1", (job_id,)
        ).fetchone()
        if has_memberships:
            q += " JOIN job_songs js ON js.song_id = songs.id"
            conds.append("js.job_id = ?")
        else:
            conds.append("songs.job_id = ?")
        args.append(job_id)
    if status:
        conds.append("songs.status = ?")
        args.append(status)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY " + (
        "js.position, songs.id" if job_id and has_memberships else "songs.id"
    )
    rows = conn.execute(q, args).fetchall()
    return [_song_row(r) for r in rows]


def get_song_by_bvid(conn: sqlite3.Connection, bvid: str) -> dict | None:
    row = conn.execute("SELECT * FROM songs WHERE bvid = ? ORDER BY part_index, id LIMIT 1", (bvid,)).fetchone()
    return _song_row(row) if row else None


def add_job_song(conn: sqlite3.Connection, job_id: str, song_id: int,
                 position: int, is_new: bool) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO job_songs (job_id, song_id, position, is_new) VALUES (?,?,?,?)",
        (job_id, song_id, position, int(is_new)),
    )
    conn.commit()


def list_job_songs(conn: sqlite3.Connection, job_id: str,
                   new_only: bool = False) -> list[dict]:
    q = (
        "SELECT s.* FROM job_songs js JOIN songs s ON s.id = js.song_id "
        "WHERE js.job_id = ?"
    )
    if new_only:
        q += " AND js.is_new = 1"
    q += " ORDER BY js.position, s.id"
    return [_song_row(row) for row in conn.execute(q, (job_id,)).fetchall()]


def update_song(conn: sqlite3.Connection, sid: int, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE songs SET {sets} WHERE id = ?", (*fields.values(), sid))
    conn.commit()


def delete_song(conn: sqlite3.Connection, sid: int) -> dict | None:
    row = conn.execute("SELECT * FROM songs WHERE id = ?", (sid,)).fetchone()
    if row:
        conn.execute("DELETE FROM songs WHERE id = ?", (sid,))
        conn.commit()
        return _song_row(row)
    return None


# ---------- playlists ----------

def list_playlists(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT p.*, COUNT(ps.song_id) AS song_count "
        "FROM playlists p LEFT JOIN playlist_songs ps ON ps.playlist_id = p.id "
        "GROUP BY p.id ORDER BY p.created_at, p.id"
    ).fetchall()
    return [dict(row) for row in rows]


def create_playlist(conn: sqlite3.Connection, name: str) -> dict:
    cur = conn.execute(
        "INSERT INTO playlists (name, created_at) VALUES (?, ?)",
        (name, now()),
    )
    conn.commit()
    return dict(conn.execute(
        "SELECT p.*, 0 AS song_count FROM playlists p WHERE id = ?",
        (cur.lastrowid,),
    ).fetchone())


def get_playlist(conn: sqlite3.Connection, playlist_id: int) -> dict | None:
    row = conn.execute(
        "SELECT p.*, COUNT(ps.song_id) AS song_count "
        "FROM playlists p LEFT JOIN playlist_songs ps ON ps.playlist_id = p.id "
        "WHERE p.id = ? GROUP BY p.id",
        (playlist_id,),
    ).fetchone()
    return dict(row) if row else None


def rename_playlist(conn: sqlite3.Connection, playlist_id: int, name: str) -> dict | None:
    conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))
    conn.commit()
    return get_playlist(conn, playlist_id)


def delete_playlist(conn: sqlite3.Connection, playlist_id: int) -> bool:
    cur = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    conn.commit()
    return cur.rowcount > 0


def list_playlist_songs(conn: sqlite3.Connection, playlist_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.* FROM playlist_songs ps "
        "JOIN songs s ON s.id = ps.song_id "
        "WHERE ps.playlist_id = ? ORDER BY ps.position, ps.added_at, s.id",
        (playlist_id,),
    ).fetchall()
    return [_song_row(row) for row in rows]


def add_song_to_playlist(conn: sqlite3.Connection, playlist_id: int, song_id: int) -> bool:
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_songs WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT OR IGNORE INTO playlist_songs "
        "(playlist_id, song_id, position, added_at) VALUES (?, ?, ?, ?)",
        (playlist_id, song_id, position, now()),
    )
    conn.commit()
    return cur.rowcount > 0


def remove_song_from_playlist(conn: sqlite3.Connection, playlist_id: int, song_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM playlist_songs WHERE playlist_id = ? AND song_id = ?",
        (playlist_id, song_id),
    )
    conn.commit()
    return cur.rowcount > 0


# ---------- saved Bilibili collections ----------

def upsert_collection(conn: sqlite3.Connection, *, media_id: str, url: str,
                      title: str, album: str | None, auto_update: bool,
                      fetch_lyrics: bool = True, multipart_policy: str = "ask") -> dict:
    timestamp = now()
    conn.execute(
        "INSERT INTO collections "
        "(media_id, url, title, album, auto_update, fetch_lyrics, multipart_policy, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(media_id) DO UPDATE SET url=excluded.url, title=excluded.title, "
        "album=COALESCE(excluded.album, collections.album), "
        "auto_update=excluded.auto_update, fetch_lyrics=excluded.fetch_lyrics, multipart_policy=excluded.multipart_policy, "
        "updated_at=excluded.updated_at",
        (media_id, url, title, album, int(auto_update), int(fetch_lyrics), multipart_policy, timestamp, timestamp),
    )
    conn.commit()
    return get_collection_by_media_id(conn, media_id)


def _collection_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["auto_update"] = bool(result["auto_update"])
    result["fetch_lyrics"] = bool(result["fetch_lyrics"])
    return result


def list_collections(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT c.*, COUNT(cs.song_id) AS song_count, "
        "SUM(CASE WHEN s.file_path IS NOT NULL THEN 1 ELSE 0 END) AS downloaded_count, COUNT(DISTINCT CASE WHEN s.download_requested IS NULL AND CASE WHEN json_valid(s.parts) THEN json_array_length(s.parts) ELSE 0 END>1 THEN s.bvid END) AS pending_multipart_count, COUNT(DISTINCT CASE WHEN CASE WHEN json_valid(s.parts) THEN json_array_length(s.parts) ELSE 0 END>1 THEN s.bvid END) AS multipart_count "
        "FROM collections c LEFT JOIN collection_songs cs ON cs.collection_id = c.id "
        "LEFT JOIN songs s ON s.id = cs.song_id GROUP BY c.id ORDER BY c.created_at, c.id"
    ).fetchall()
    return [_collection_row(row) for row in rows]


def get_collection(conn: sqlite3.Connection, collection_id: int) -> dict | None:
    row = conn.execute(
        "SELECT c.*, COUNT(cs.song_id) AS song_count, "
        "SUM(CASE WHEN s.file_path IS NOT NULL THEN 1 ELSE 0 END) AS downloaded_count, COUNT(DISTINCT CASE WHEN s.download_requested IS NULL AND CASE WHEN json_valid(s.parts) THEN json_array_length(s.parts) ELSE 0 END>1 THEN s.bvid END) AS pending_multipart_count, COUNT(DISTINCT CASE WHEN CASE WHEN json_valid(s.parts) THEN json_array_length(s.parts) ELSE 0 END>1 THEN s.bvid END) AS multipart_count "
        "FROM collections c LEFT JOIN collection_songs cs ON cs.collection_id = c.id "
        "LEFT JOIN songs s ON s.id = cs.song_id WHERE c.id = ? GROUP BY c.id",
        (collection_id,),
    ).fetchone()
    return _collection_row(row) if row else None


def get_collection_by_media_id(conn: sqlite3.Connection, media_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM collections WHERE media_id = ?", (media_id,)).fetchone()
    return _collection_row(row) if row else None


def update_collection(conn: sqlite3.Connection, collection_id: int, **fields) -> dict | None:
    if fields:
        fields["updated_at"] = now()
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE collections SET {sets} WHERE id = ?",
            (*fields.values(), collection_id),
        )
        conn.commit()
    return get_collection(conn, collection_id)


def delete_collection(conn: sqlite3.Connection, collection_id: int) -> bool:
    cur = conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    conn.commit()
    return cur.rowcount > 0


def replace_collection_songs(conn: sqlite3.Connection, collection_id: int,
                             song_ids: list[int]) -> None:
    timestamp = now()
    keep = set(song_ids)
    existing = {
        row[0] for row in conn.execute(
            "SELECT song_id FROM collection_songs WHERE collection_id = ?",
            (collection_id,),
        )
    }
    for position, song_id in enumerate(song_ids):
        conn.execute(
            "INSERT INTO collection_songs (collection_id, song_id, position, added_at) "
            "VALUES (?,?,?,?) ON CONFLICT(collection_id, song_id) "
            "DO UPDATE SET position=excluded.position",
            (collection_id, song_id, position, timestamp),
        )
    removed = existing - keep
    if removed:
        marks = ",".join("?" for _ in removed)
        conn.execute(
            f"DELETE FROM collection_songs WHERE collection_id = ? AND song_id IN ({marks})",
            (collection_id, *removed),
        )
    conn.commit()


def list_collection_songs(conn: sqlite3.Connection, collection_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.* FROM collection_songs cs JOIN songs s ON s.id = cs.song_id "
        "WHERE cs.collection_id = ? ORDER BY cs.position, s.part_index, s.id",
        (collection_id,),
    ).fetchall()
    return [_song_row(row) for row in rows]


# ---------- successful access audit ----------

def add_access_session(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        "INSERT INTO access_sessions "
        "(id, username, ip, country, region, city, user_agent, login_at, last_seen) "
        "VALUES (:id, :username, :ip, :country, :region, :city, :user_agent, :login_at, :last_seen)",
        data,
    )
    conn.commit()


def touch_access_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "UPDATE access_sessions SET last_seen = ?, request_count = request_count + 1 WHERE id = ?",
        (now(), session_id),
    )
    conn.commit()


def cached_ip_location(conn: sqlite3.Connection, ip: str) -> dict | None:
    row = conn.execute(
        "SELECT country, region, city FROM access_sessions "
        "WHERE ip = ? AND country IS NOT NULL ORDER BY login_at DESC LIMIT 1",
        (ip,),
    ).fetchone()
    return dict(row) if row else None


def access_audit(conn: sqlite3.Connection, limit: int = 100) -> dict:
    summary = conn.execute(
        "SELECT COUNT(*) AS successful_sessions, COUNT(DISTINCT ip) AS unique_ip_count, "
        "MAX(last_seen) AS latest_access FROM access_sessions"
    ).fetchone()
    rows = conn.execute(
        "SELECT id, username, ip, country, region, city, user_agent, "
        "login_at, last_seen, request_count FROM access_sessions "
        "ORDER BY login_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return {**dict(summary), "entries": [dict(row) for row in rows]}
