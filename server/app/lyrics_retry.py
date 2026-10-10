"""Persistent lyric-match notices and one bounded background retry queue."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from datetime import datetime, timezone

from . import downloader, importer, lyrics
from .db import get_conn, get_song, update_song

try:
    from .maintenance import lease as maintenance_lease
except ModuleNotFoundError as exc:
    if exc.name != f'{__package__}.maintenance':
        raise
    maintenance_lease = nullcontext

logger = logging.getLogger('uvicorn.error')
RETRY_SECONDS = 300
MAX_QUEUED = 2  # One running match and one waiting; leave other songs editable.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lyrics-retry')
_lock = threading.Lock()
_queued = set()
_stop = threading.Event()
_thread = None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _has_lyrics(song):
    return bool(lyrics.lrc_to_plain(song.get('lyrics') or '').strip()
                or lyrics.lrc_to_plain(song.get('lrc') or '').strip())


def record_started(conn, song_id):
    current = time.time()
    conn.execute(
        "UPDATE lyrics_failures SET status='downloading',last_attempt_at=?,attempts=attempts+1 "
        "WHERE song_id=?", (current, song_id),
    )
    conn.commit()


def record_failure(conn, song_id, error):
    current = time.time()
    conn.execute(
        "INSERT INTO lyrics_failures "
        "(song_id,first_failed_at,last_attempt_at,next_retry_at,last_error) VALUES (?,?,?,?,?) "
        "ON CONFLICT(song_id) DO UPDATE SET status='error',last_error=excluded.last_error, "
        "next_retry_at=CASE WHEN lyrics_failures.dismissed=1 THEN NULL ELSE excluded.next_retry_at END",
        (song_id, current, current, current + RETRY_SECONDS, str(error)[:500]),
    )
    conn.commit()


def record_success(conn, song_id):
    conn.execute("UPDATE lyrics_failures SET status='ready',next_retry_at=NULL WHERE song_id=?", (song_id,))
    conn.commit()


def dismiss(song_id):
    """Skipping lyrics suppresses the notice and stops its automatic retries."""
    with closing(get_conn()) as conn:
        conn.execute("UPDATE lyrics_failures SET dismissed=1,next_retry_at=NULL WHERE song_id=?", (song_id,))
        conn.commit()
    return {'ok': True}


def list_failures():
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT n.*,s.title,s.artist FROM lyrics_failures n JOIN songs s ON s.id=n.song_id "
            "WHERE n.dismissed=0 ORDER BY n.first_failed_at DESC,n.song_id DESC"
        ).fetchall()
    return [{
        'song_id': row['song_id'], 'title': row['title'], 'artist': row['artist'],
        'error': row['last_error'], 'first_failed_at': _iso(row['first_failed_at']),
        'last_attempt_at': _iso(row['last_attempt_at']), 'next_retry_at': _iso(row['next_retry_at']),
        'status': row['status'], 'dismissed': bool(row['dismissed']), 'attempts': row['attempts'],
    } for row in rows]


def reconcile(conn):
    """Restore pending retries and discover historical ready songs without lyrics."""
    current = time.time()
    rows = conn.execute('SELECT id,status,lyrics_enabled,lyrics_source,lyrics,lrc FROM songs').fetchall()
    for row in rows:
        song = dict(row)
        if not song['lyrics_enabled'] or song['lyrics_source'] == 'upload' or _has_lyrics(song):
            conn.execute("UPDATE lyrics_failures SET status='ready',next_retry_at=NULL WHERE song_id=?", (row['id'],))
    conn.execute(
        "UPDATE lyrics_failures SET status='error',next_retry_at=CASE WHEN dismissed=1 THEN NULL "
        "ELSE COALESCE(next_retry_at,?) END WHERE status IN ('queued','downloading')",
        (current + RETRY_SECONDS,),
    )
    for row in rows:
        song = dict(row)
        if song['status'] == 'ready' and song['lyrics_enabled'] and song['lyrics_source'] != 'upload' and not _has_lyrics(song):
            conn.execute(
                "INSERT OR IGNORE INTO lyrics_failures "
                "(song_id,first_failed_at,last_attempt_at,next_retry_at,last_error) VALUES (?,?,?,?,'尚未匹配到歌词，请重试或上传歌词')",
                (row['id'], current, current, current + RETRY_SECONDS),
            )
    conn.commit()


def refresh_once(conn, song_id, *, force=True):
    """Caller owns song + deployment leases; keep old lyrics when no match is found."""
    song = get_song(conn, song_id)
    if not song:
        raise importer.ImportError('歌曲不存在')
    if song.get('lyrics_source') == 'upload' or not song.get('lyrics_enabled', True):
        record_success(conn, song_id)
        return False
    record_started(conn, song_id)
    try:
        hit = lyrics.fetch_lyrics(song['title'], song['artist'], song.get('duration') or 0, force=force)
        text = (hit.get('lrc') or hit.get('plain') or '').strip()
        plain = (hit.get('plain') or lyrics.lrc_to_plain(text)).strip()
        if not plain:
            if _has_lyrics(song):
                record_success(conn, song_id)
            else:
                record_failure(conn, song_id, '尚未匹配到歌词，请重试或上传歌词')
            return False
        if song.get('file_path'):
            downloader.update_embedded_lyrics(song['file_path'], text)
            downloader.write_lrc_sidecar(song['file_path'], text)
        update_song(conn, song_id, lyrics=plain, lrc=hit.get('lrc') or '',
                    lyrics_source=hit.get('source') or 'matched')
        record_success(conn, song_id)
        return True
    except Exception:
        logger.warning('歌词重新匹配失败 sid=%s', song_id)
        if _has_lyrics(song):
            record_success(conn, song_id)
        else:
            record_failure(conn, song_id, '歌词匹配暂时失败，请重试或上传歌词')
        return False


def retry_song(song_id, *, automatic=False):
    # Hold the deployment lease and song reservation from enqueue through worker
    # completion. Neither a download nor an uploaded lyric can race this snapshot.
    lease = maintenance_lease()
    reservation = None
    lease_entered = False
    slot = False
    submitted = False
    queued_changed = False
    try:
        lease.__enter__()
        lease_entered = True
        with _lock:
            if song_id in _queued:
                return {'started': False, 'queued': 0, 'message': '歌词已在重试队列中'}
            if len(_queued) >= MAX_QUEUED:
                return {'started': False, 'queued': 0, 'message': '歌词重试队列已满，请稍后重试'}
            _queued.add(song_id)
            slot = True
        reservation = importer.reserve_song_edit(song_id)
        try:
            reservation.__enter__()
        except importer.ImportError:
            reservation = None
            return {'started': False, 'queued': 0, 'message': '歌曲正在下载或编辑，请稍后重试歌词'}
        with closing(get_conn()) as conn:
            song = get_song(conn, song_id)
            if not song:
                raise importer.ImportError('歌曲不存在')
            if song.get('lyrics_source') == 'upload' or (automatic and (not song.get('lyrics_enabled', True) or _has_lyrics(song))):
                record_success(conn, song_id)
                return {'started': False, 'queued': 0, 'message': '已保留上传歌词或关闭的自动歌词设置'}
            notice = conn.execute('SELECT * FROM lyrics_failures WHERE song_id=?', (song_id,)).fetchone()
            current = time.time()
            if automatic and (
                not notice or notice['dismissed'] or notice['next_retry_at'] is None
                or notice['next_retry_at'] > current or song['status'] != 'ready'
            ):
                return {'started': False, 'queued': 0, 'message': '尚未到歌词自动重试时间'}
            if not notice:
                record_failure(conn, song_id, '等待重新匹配歌词')
            if not automatic:
                update_song(conn, song_id, lyrics_enabled=1)
            conn.execute(
                "UPDATE lyrics_failures SET status='queued',dismissed=0,next_retry_at=? WHERE song_id=?",
                (current + RETRY_SECONDS, song_id),
            )
            conn.commit()
            queued_changed = True

        def release():
            try:
                reservation.__exit__(None, None, None)
            finally:
                try:
                    lease.__exit__(None, None, None)
                finally:
                    with _lock:
                        _queued.discard(song_id)

        def worker():
            try:
                with closing(get_conn()) as worker_conn:
                    refresh_once(worker_conn, song_id, force=True)
            except Exception:
                logger.warning('歌词重试任务异常 sid=%s', song_id)
                try:
                    with closing(get_conn()) as recovery:
                        current_song = get_song(recovery, song_id)
                        if current_song and _has_lyrics(current_song):
                            record_success(recovery, song_id)
                        elif current_song:
                            record_failure(recovery, song_id, '歌词匹配暂时失败，请重试或上传歌词')
                except Exception:
                    logger.warning('无法保存歌词重试错误 sid=%s', song_id)
            finally:
                release()

        _executor.submit(worker)
        submitted = True
        return {'started': True, 'queued': 1, 'message': '已加入歌词重试队列'}
    except Exception:
        if queued_changed:
            try:
                with closing(get_conn()) as conn:
                    record_failure(conn, song_id, '歌词重试入队失败，请稍后重试')
            except Exception:
                logger.warning('无法恢复歌词重试入队错误 sid=%s', song_id)
        raise
    finally:
        if not submitted:
            try:
                if reservation is not None:
                    reservation.__exit__(None, None, None)
            finally:
                try:
                    if lease_entered:
                        lease.__exit__(None, None, None)
                finally:
                    if slot:
                        with _lock:
                            _queued.discard(song_id)


def retry_due():
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT n.song_id FROM lyrics_failures n JOIN songs s ON s.id=n.song_id "
            "WHERE n.status='error' AND n.dismissed=0 AND n.next_retry_at<=? AND s.status='ready' "
            "AND s.lyrics_enabled=1 AND COALESCE(s.lyrics_source,'')!='upload' "
            "ORDER BY n.next_retry_at LIMIT ?", (time.time(), MAX_QUEUED),
        ).fetchall()
    for row in rows:
        try:
            retry_song(row[0], automatic=True)
        except importer.ImportError:
            pass
        except Exception:
            logger.warning('歌词自动重试暂时未入队 sid=%s', row[0])


def _loop():
    while not _stop.wait(15):
        try:
            retry_due()
        except Exception:
            logger.warning('歌词自动重试调度失败')


def start_scheduler():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name='lyrics-retry-scheduler')
    _thread.start()


def stop_scheduler():
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
