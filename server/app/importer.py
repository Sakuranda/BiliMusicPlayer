"""导入流水线：收藏夹解析入库 + 后台下载编排。"""
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import bilibili, downloader, lyrics, parser, lyrics_retry
from .config import COOKIE_DIR, DOWNLOAD_THREADS, METADATA_THREADS, MUSIC_DIR
from .db import (add_job_song, create_job, get_collection, get_conn, get_job,
                 get_song_by_bvid, get_song, insert_song, list_collections, list_job_songs,
                 list_songs, now, replace_collection_songs, update_collection,
                 update_job, update_song, upsert_collection)

logger = logging.getLogger("uvicorn.error")


class ImportError(Exception):
    pass


# 所有收藏夹共享同一个下载执行器。旧实现会为每个任务新建线程池，两个收藏夹
# 同时启动时会把并发数翻倍；同一任务重复点击还可能重复下载同一首歌。
_download_executor = ThreadPoolExecutor(
    max_workers=DOWNLOAD_THREADS,
    thread_name_prefix="music-download",
)
_metadata_executor = ThreadPoolExecutor(
    max_workers=METADATA_THREADS, thread_name_prefix="music-metadata",
)
_active_lock = threading.Lock()
_active_jobs: set[str] = set()
_active_song_ids: set[int] = set()
_active_collection_ids: set[int] = set()
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None


def _cookie_file(media_id: str, cookie: str | None) -> Path | None:
    if not cookie:
        return None
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    path = COOKIE_DIR / f"{media_id}.txt"
    downloader.write_cookie_file(cookie, path)
    return path


def parse_and_store(url: str, cookie: str | None = None, album: str | None = None,
                    save_collection: bool = False, auto_update: bool = False,
                    collection_id: int | None = None,
                    fetch_lyrics: bool = True, multipart_policy: str = "ask") -> str:
    """拉取收藏夹 → 解析标题 → 入库（status=pending），返回 job_id。"""
    with closing(get_conn()) as conn:
        return _parse_and_store(conn, url, cookie, album, save_collection, auto_update,
                                collection_id, fetch_lyrics, multipart_policy)


def _parse_and_store(conn, url, cookie, album, save_collection, auto_update,
                     collection_id, fetch_lyrics, multipart_policy):
    media_ids = bilibili.parse_fav_url(url)
    fav_title, items, media_id = bilibili.fetch_favorites(media_ids, cookie)
    _cookie_file(media_id, cookie)
    if not items and collection_id is None:
        raise ImportError("收藏夹是空的")

    album = album or fav_title

    collection = None
    if save_collection or collection_id is not None:
        expected = get_collection(conn, collection_id) if collection_id is not None else None
        if collection_id is not None and (not expected or expected["media_id"] != media_id):
            raise ImportError("收藏夹链接与已保存记录不匹配")
        collection = upsert_collection(
            conn,
            media_id=media_id,
            url=url,
            title=fav_title,
            album=album,
            auto_update=auto_update,
            fetch_lyrics=fetch_lyrics,
            multipart_policy=multipart_policy,
        )

    # 新视频、缺 cid 或收藏夹列表提示增加了分P时才请求详情/标签。
    # 160 首收藏夹日更通常只新增几首，避免每天重新打 320 次 B 站 API。
    existing_by_bvid = {item["bvid"]: get_song_by_bvid(conn, item["bvid"]) for item in items}
    enrich_items = [
        item for item in items
        if existing_by_bvid[item["bvid"]] is None
        or item.get("page_count", 1) > max(1, len(existing_by_bvid[item["bvid"]].get("parts") or []))
        or (
            not existing_by_bvid[item["bvid"]].get("file_path")
            and existing_by_bvid[item["bvid"]].get("cid") is None
        )
    ]

    def enrich(item: dict) -> dict:
        item["tags"] = bilibili.fetch_tags(item["bvid"], cookie)
        # 视频详情：获取分P信息（cid、每P标题与时长）
        item["parts"] = []
        # 收藏夹列表自带的首 P cid 比额外的详情请求更抗风控；详情成功时再
        # 用完整 pages 覆盖它，失败则至少保留这个可直接请求 playurl 的 cid。
        item["cid"] = item.get("cid")
        item["part_index"] = 1
        item["part_title"] = None
        try:
            detail = bilibili.fetch_detail(item["bvid"], cookie)
            item["description"] = detail.get("desc") or ""
            pages = detail.get("pages") or []
            if pages:
                item["parts"] = [
                    {
                        "cid": p.get("cid"),
                        "page": p.get("page", i + 1),
                        "part": p.get("part") or f"P{i + 1}",
                        "duration": p.get("duration") or 0,
                    }
                    for i, p in enumerate(pages)
                ]
                first = item["parts"][0]
                item["cid"] = first["cid"]
                item["part_index"] = 1
                item["part_title"] = first["part"]
                item["duration"] = first["duration"] or item["duration"]
            elif detail.get("cid"):
                item["cid"] = detail["cid"]
                item["parts"] = [{
                    "cid": detail["cid"], "page": 1, "part": "P1",
                    "duration": detail.get("duration") or item["duration"],
                }]
                item["duration"] = detail.get("duration") or item["duration"]
                item["part_index"] = 1
                item["part_title"] = "P1"
        except Exception:
            pass  # 详情拉不到不阻塞导入，下载时再报错
        return item

    if enrich_items:
        enriched = list(_metadata_executor.map(enrich, enrich_items))
        enriched_by_bvid = {item["bvid"]: item for item in enriched}
        items = [enriched_by_bvid.get(item["bvid"], item) for item in items]

    known_artists = parser.known_artists_from_songs(list_songs(conn))
    # 先收集同批明确的演唱证据，再为只有标签的切片统一人名；不采纳低置信猜测。
    initial = [parser.parse_metadata(item["raw_title"], item["uploader"],
                                    item.get("tags") or [], item.get("description") or "",
                                    known_artists=known_artists)
               for item in enrich_items]
    known_artists = sorted(set(known_artists) | set(parser.known_artists_from_songs([
        {"artist": hit["artist"], "metadata": hit} for hit in initial
    ])))

    job_id = uuid.uuid4().hex[:12]
    create_job(
        conn, job_id, url, media_id, fav_title, len(items),
        collection_id=collection["id"] if collection else None,
    )
    existing_collection_song_ids = set()
    if collection:
        existing_collection_song_ids = {
            row[0] for row in conn.execute(
                "SELECT song_id FROM collection_songs WHERE collection_id = ?",
                (collection["id"],),
            )
        }
    collection_song_ids: list[int] = []

    position = 0
    for item in items:
        representative = existing_by_bvid[item["bvid"]]
        parts = item.get("parts") or (representative or {}).get("parts") or []
        # Materialize every P. A record is a track, while BV remains the real source ID.
        pages = parts or [{"cid": (representative or {}).get("cid") or item.get("cid"),
                           "page": (representative or {}).get("part_index") or 1,
                           "part": (representative or {}).get("part_title"),
                           "duration": item.get("duration")}]
        for part in pages:
            page = part.get("page") or 1
            row = conn.execute("SELECT id FROM songs WHERE bvid=? AND part_index=?",
                               (item["bvid"], page)).fetchone()
            existing = get_song(conn, row[0]) if row else None
            if existing:
                sid = existing["id"]
                fields = dict(raw_title=item["raw_title"], uploader=item["uploader"])
                if not existing.get("cover_url") or str(existing["cover_url"]).startswith("http"):
                    fields["cover_url"] = item["cover_url"]
                if parts:
                    fields["parts"] = json.dumps(parts, ensure_ascii=False)
                if not existing.get("cid") and part.get("cid"):
                    fields.update(cid=part["cid"], part_title=part.get("part"), duration=part.get("duration"))
                if existing.get("download_requested") is None and len(parts) > 1 and multipart_policy != "ask":
                    fields["download_requested"] = int(multipart_policy == "all" or page == 1)
                if existing.get("file_path") and len(parts) <= 1:
                    fields["download_requested"] = 1
                update_song(conn, sid, **fields)
            else:
                metadata = parser.parse_metadata(
                    item["raw_title"], item["uploader"], item.get("tags") or [],
                    item.get("description") or "", known_artists=known_artists,
                )
                if len(parts) > 1 and part.get("part"):
                    part_metadata = parser.parse_metadata(
                        part["part"], item["uploader"], item.get("tags") or [],
                        item.get("description") or "", known_artists=known_artists,
                    )
                    if part_metadata["artist_confidence"] >= 0.8:
                        for key in ("artist", "artist_source", "artist_confidence"):
                            metadata[key] = part_metadata[key]
                    metadata.update(title=part_metadata["title"], title_source=part_metadata["title_source"],
                                    title_confidence=part_metadata["title_confidence"],
                                    needs_review=metadata["needs_review"] or part_metadata["needs_review"],
                                    reasons=list(dict.fromkeys(metadata["reasons"] + part_metadata["reasons"])))
                requested = (1 if len(parts) <= 1 or multipart_policy == "all" else
                             int(page == 1) if multipart_policy == "first" else None)
                sid = insert_song(conn, {
                    "bvid": item["bvid"], "job_id": job_id,
                    "title": metadata["title"],
                    "artist": metadata["artist"] if metadata["artist_confidence"] >= 0.8 else "未知歌手",
                    "description": item.get("description"), "metadata": json.dumps(metadata, ensure_ascii=False),
                    "album": album, "duration": part.get("duration") or None,
                    "cid": part.get("cid"), "part_index": page, "part_title": part.get("part"),
                    "parts": json.dumps(parts, ensure_ascii=False) if parts else None,
                    "source_url": f"https://www.bilibili.com/video/{item['bvid']}?p={page}",
                    "raw_title": item["raw_title"], "uploader": item["uploader"],
                    "tags": json.dumps(item.get("tags") or [], ensure_ascii=False),
                    "cover_url": item["cover_url"], "download_requested": requested,
                })
            is_new = (sid not in existing_collection_song_ids) if collection else existing is None
            add_job_song(conn, job_id, sid, position, is_new)
            collection_song_ids.append(sid)
            position += 1
    update_job(conn, job_id, total=len(collection_song_ids))

    if collection:
        replace_collection_songs(conn, collection["id"], collection_song_ids)
        update_collection(
            conn, collection["id"], last_checked_at=now(), last_updated_at=now(), last_error=None
        )
    return job_id


def materialize_cached_parts(conn) -> int:
    """Expand old single-track multi-P records without touching audio or user edits."""
    added = 0
    for original in list_songs(conn):
        parts = original.get("parts") or []
        if len(parts) <= 1:
            continue
        # All existing tracks with unknown choice remain reviewable, even if downloaded.
        for part in parts:
            page = part.get("page") or 1
            if not part.get("cid") or conn.execute("SELECT 1 FROM songs WHERE bvid=? AND part_index=?", (original["bvid"], page)).fetchone():
                continue
            metadata = dict(original.get("metadata") or {})
            metadata.update(title=part.get("part") or f"P{page}", title_source="part_title", title_confidence=0.5,
                            needs_review=True, reasons=["旧记录补充分P，请核对该P歌名与演唱者"])
            data = {key: original.get(key) for key in (
                "bvid", "job_id", "artist", "album", "raw_title", "uploader", "cover_url", "description")}
            data.update(title=metadata["title"], cid=part["cid"], part_index=page, part_title=part.get("part"),
                        duration=part.get("duration"), parts=json.dumps(parts, ensure_ascii=False),
                        tags=json.dumps(original.get("tags") or [], ensure_ascii=False),
                        metadata=json.dumps(metadata, ensure_ascii=False), download_requested=None,
                        source_url=f"https://www.bilibili.com/video/{original['bvid']}?p={page}")
            sid = insert_song(conn, data)
            # Copy memberships, never media. The next full refresh will order every P.
            conn.execute("INSERT OR IGNORE INTO collection_songs (collection_id,song_id,position,added_at) SELECT collection_id,?,position,added_at FROM collection_songs WHERE song_id=?", (sid, original["id"]))
            conn.execute("INSERT OR IGNORE INTO job_songs (job_id,song_id,position,is_new) SELECT job_id,?,position,0 FROM job_songs WHERE song_id=?", (sid, original["id"]))
            conn.commit()
            added += 1
    return added


def refresh_collection(collection_id: int) -> dict:
    """同步一个已保存收藏夹，并只自动下载本次新增的成员。"""
    with _active_lock:
        if collection_id in _active_collection_ids:
            raise ImportError("该收藏夹正在更新")
        _active_collection_ids.add(collection_id)
    conn = None
    try:
        conn = get_conn()
        collection = get_collection(conn, collection_id)
        if not collection:
            raise ImportError("收藏夹不存在")
        cookie_file = COOKIE_DIR / f"{collection['media_id']}.txt"
        cookie = downloader.read_cookie_string(cookie_file)
        job_id = parse_and_store(
            collection["url"], cookie, collection.get("album"),
            save_collection=True,
            auto_update=collection["auto_update"],
            collection_id=collection_id,
            fetch_lyrics=collection["fetch_lyrics"],
            multipart_policy=collection["multipart_policy"],
        )
        # 上一次新增下载失败的成员仍需重试，不能因已存在于关系表而永久漏过。
        candidates = list_job_songs(conn, job_id)
        waiting = len({s["bvid"] for s in candidates
                       if len(s.get("parts") or []) > 1 and s.get("download_requested") is None})
        new_songs = [song for song in candidates
                     if (len(song.get("parts") or []) <= 1 or song.get("download_requested") == 1)
                     and (not song.get("file_path") or not (MUSIC_DIR / song["file_path"]).is_file())]
        with _active_lock:
            new_songs = [song for song in new_songs if song["id"] not in _active_song_ids]
        if new_songs:
            result = start_download(
                job_id,
                fetch_lyrics=collection["fetch_lyrics"],
                song_ids=[song["id"] for song in new_songs],
            )
        else:
            update_job(
                conn, job_id, status="done", total=0, done=0, failed=0,
                message="检查完成，没有新增歌曲",
            )
            result = {
                "started": False, "queued": 0, "concurrency": DOWNLOAD_THREADS,
                "message": "没有新增歌曲",
            }
        return {"job_id": job_id, "pending_multipart_count": waiting, **result}
    except Exception as exc:
        if conn is not None and get_collection(conn, collection_id):
            update_collection(conn, collection_id, last_checked_at=now(), last_error=str(exc)[:500])
        raise
    finally:
        if conn is not None:
            conn.close()
        with _active_lock:
            _active_collection_ids.discard(collection_id)


def _collection_due(collection: dict, current: datetime) -> bool:
    if not collection.get("auto_update"):
        return False
    checked = collection.get("last_checked_at")
    if not checked:
        return True
    try:
        checked_at = datetime.fromisoformat(checked)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        return checked_at <= current - timedelta(days=1)
    except ValueError:
        return True


def _scheduler_loop() -> None:
    # 启动后立即检查一次；此后每 15 分钟扫描，单个收藏夹仍严格至少间隔 24 小时。
    while not _scheduler_stop.is_set():
        conn = get_conn()
        try:
            due = [c for c in list_collections(conn) if _collection_due(c, datetime.now(timezone.utc))]
        finally:
            conn.close()
        for collection in due:
            if _scheduler_stop.is_set():
                return
            try:
                refresh_collection(collection["id"])
            except Exception as exc:  # noqa: BLE001
                logger.error("自动更新收藏夹失败 id=%s: %s", collection["id"], exc, exc_info=True)
        _scheduler_stop.wait(15 * 60)


def start_collection_scheduler() -> None:
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, daemon=True, name="collection-scheduler"
    )
    _scheduler_thread.start()


def stop_collection_scheduler() -> None:
    _scheduler_stop.set()


def _download_one(conn, song: dict, cookie_file: Path | None):
    sid = song["id"]
    result = None
    try:
        update_song(conn, sid, status="downloading", error=None)

        # 用户上传的歌词优先且永不被自动匹配覆盖。旧实现更新数据库后仍读取
        # song 旧快照，导致侧车与内嵌标签没有歌词；这里显式保留本次结果。
        lyric_plain = song.get("lyrics") or ""
        lrc_text = song.get("lrc") or ""
        lyric_source = song.get("lyrics_source") or ""
        if song.get("lyrics_enabled", True) and lyric_source != "upload":
            lyrics_retry.record_started(conn, sid)
            try:
                hit = lyrics.fetch_lyrics(song["title"], song["artist"], song["duration"] or 0)
                if hit.get("plain") or hit.get("lrc"):
                    lyric_plain = hit["plain"]
                    lrc_text = hit["lrc"]
                    lyric_source = hit["source"]
                    update_song(conn, sid, lyrics=lyric_plain, lrc=lrc_text,
                                lyrics_source=lyric_source)
                    lyrics_retry.record_success(conn, sid)
                elif not (lyric_plain or lrc_text):
                    lyrics_retry.record_failure(conn, sid, "暂未找到可用歌词，将每五分钟自动重试")
            except Exception:
                if not (lyric_plain or lrc_text):
                    lyrics_retry.record_failure(conn, sid, "歌词服务请求失败，将每五分钟自动重试")

        result = downloader.download_audio(
            song["bvid"], cookie_file,
            cid=song.get("cid"),
            part_index=song.get("part_index") or 1,
        )
        cover_bytes = downloader.optimize_cover(result["cover"])
        if not cover_bytes and str(song.get("cover_url") or "").startswith(("http://", "https://", "//")):
            try:
                cover_bytes = downloader.fetch_cover(song["bvid"], cover_url=song["cover_url"])
            except Exception:
                logger.warning("收藏夹封面下载暂时失败 sid=%s", sid)
        rel = downloader.tag_and_store(
            result["file"], song["title"], song["artist"], song["album"],
            cover_bytes, f"{song['bvid']}-{song.get('cid') or song['id']}", lrc_text or lyric_plain or None,
        )
        # 歌词侧车文件（Navidrome/Amperfy 读取同名 .lrc）
        if lrc_text:
            downloader.write_lrc_sidecar(rel, lrc_text)
        cover_name = downloader.save_cover(cover_bytes, f"{song['bvid']}-{sid}") if cover_bytes else None
        update_song(conn, sid,
                    status="ready",
                    file_path=rel,
                    duration=downloader.MP4(downloader.MUSIC_DIR / rel).info.length,
                    cover_url=cover_name or song["cover_url"],
                    downloaded_cid=song.get("cid"),
                    error=None)
        old_rel = song.get("file_path")
        if old_rel and old_rel != rel:
            old_path = downloader.MUSIC_DIR / old_rel
            try:
                old_path.unlink(missing_ok=True)
                old_path.with_suffix(".lrc").unlink(missing_ok=True)
            except OSError:
                pass
        from .artist_playlists import sync_artist_playlists_safely
        sync_artist_playlists_safely()
        return True
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:500]
        # B 站原始报错补充说明，方便排查
        if "bad parameter" in msg:
            msg = "B 站接口限流或参数被拒（瞬时可恢复）：" + msg
        logger.error("歌曲下载失败 sid=%s bvid=%s: %s", sid, song["bvid"], msg, exc_info=True)
        update_song(conn, sid, status="error", error=msg)
        return False
    finally:
        if result and result.get("tmp_dir"):
            import shutil
            shutil.rmtree(result["tmp_dir"], ignore_errors=True)


def start_download(job_id: str, bvids: list[str] | None = None,
                   fetch_lyrics: bool = True, song_ids: list[int] | None = None, record_choices: bool = False):
    """把歌曲加入进程级共享队列；可指定 bvids 只下载勾选的歌曲。

    返回本次是否真正启动及排队数量。同一任务正在执行时重复调用是幂等的，
    不会再提交一批重复 future。
    """
    with closing(get_conn()) as conn:
        return _start_download(conn, job_id, bvids, fetch_lyrics, song_ids, record_choices)


def _start_download(conn, job_id, bvids, fetch_lyrics, song_ids=None, record_choices=False):
    job = get_job(conn, job_id)
    if not job:
        raise ImportError("任务不存在")

    all_songs = list_songs(conn, job_id=job_id)
    if song_ids is not None:
        ids = set(song_ids)
        if not ids or not ids.issubset({s["id"] for s in all_songs}):
            raise ImportError("请选择该任务中的歌曲")
        selected = [s for s in all_songs if s["id"] in ids]
    elif bvids is not None:
        if not bvids:
            raise ImportError("请至少选择一首歌曲")
        bv_set = set(bvids)
        selected = [s for s in all_songs if s["bvid"] in bv_set]
    else:
        selected = all_songs

    if not selected:
        raise ImportError("所选歌曲不属于该任务")

    skipped = []
    for song in selected:
        rel = song.get("file_path")
        file_exists = bool(rel and (MUSIC_DIR / rel).is_file())
        same_or_unknown_part = (
            song.get("cid") is None
            or song.get("downloaded_cid") is None
            or song.get("downloaded_cid") == song.get("cid")
        )
        if file_exists and same_or_unknown_part:
            skipped.append(song)
            # 文件才是下载完成的最终事实。旧任务中断、重启或风控失败可能只
            # 污染 status/error，不能让已有音频因此被全量重新下载。
            if song["status"] != "ready" or song.get("error"):
                update_song(conn, song["id"], status="ready", error=None)
    songs = [s for s in selected if s not in skipped]

    with _active_lock:
        if job_id in _active_jobs:
            return {
                "started": False,
                "queued": 0,
                "concurrency": DOWNLOAD_THREADS,
                "message": "任务已在下载队列中",
            }
        conflicts = [s for s in songs if s["id"] in _active_song_ids]
        if conflicts:
            raise ImportError("部分歌曲已在另一个下载任务中，请等待其完成")
        _active_jobs.add(job_id)
        _active_song_ids.update(s["id"] for s in songs)

    if song_ids is not None and record_choices:
        try:
            for item in all_songs:
                if len(item.get("parts") or []) > 1:
                    update_song(conn, item["id"], download_requested=int(item["id"] in ids))
        except Exception:
            with _active_lock:
                _active_jobs.discard(job_id)
                _active_song_ids.difference_update(s["id"] for s in songs)
            raise

    if not songs:
        try:
            update_job(
                conn, job_id, status="done", total=len(selected), done=len(skipped),
                failed=0, message=f"全部 {len(skipped)} 首均已下载，已跳过",
            )
        finally:
            with _active_lock:
                _active_jobs.discard(job_id)
        return {
            "started": False,
            "queued": 0,
            "concurrency": DOWNLOAD_THREADS,
            "message": "所选歌曲均已下载",
        }

    try:
        song_ids = [s["id"] for s in songs]
        for sid in song_ids:
            update_song(conn, sid, status="pending", error=None,
                        lyrics_enabled=int(fetch_lyrics))
        for song in songs:
            song["lyrics_enabled"] = fetch_lyrics
        update_job(
            conn,
            job_id,
            status="downloading",
            total=len(selected),
            done=len(skipped),
            failed=0,
            message=f"已排队 {len(songs)} 首，全局并发 {DOWNLOAD_THREADS}",
        )
        cookie_file = COOKIE_DIR / f"{job['media_id']}.txt"
        if not cookie_file.exists():
            cookie_file = None

        def worker(song):
            # 每个线程用独立的数据库连接，避免并发提交事务互相冲突
            worker_conn = get_conn()
            try:
                return _download_one(worker_conn, song, cookie_file)
            finally:
                worker_conn.close()

        def run():
            conn = None
            submitted = []
            try:
                conn = get_conn()
                for song in songs:
                    submitted.append(_download_executor.submit(worker, song))
                for fut in as_completed(submitted):
                    try:
                        fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("下载 worker 异常 job=%s: %s", job_id, exc, exc_info=True)
                    _recount_selected(conn, job_id, [s["id"] for s in selected], final=False)

                # 第二批只重试“本次选择且本轮失败”的歌曲，避免把旧任务错误混进来。
                retry = []
                for sid in song_ids:
                    row = conn.execute("SELECT * FROM songs WHERE id = ?", (sid,)).fetchone()
                    if row and row["status"] == "error":
                        retry.append(dict(row))
                if retry:
                    time.sleep(15)
                    update_job(conn, job_id, message=f"自动重试 {len(retry)} 首失败的歌…")
                    retry_futures = []
                    for song in retry:
                        future = _download_executor.submit(worker, song)
                        submitted.append(future)
                        retry_futures.append(future)
                    for fut in as_completed(retry_futures):
                        try:
                            fut.result()
                        except Exception as exc:  # noqa: BLE001
                            logger.error("重试 worker 异常 job=%s: %s", job_id, exc, exc_info=True)
                        _recount_selected(conn, job_id, [s["id"] for s in selected], final=False)

                _recount_selected(conn, job_id, [s["id"] for s in selected], final=True)
            except Exception as exc:  # noqa: BLE001
                if conn is not None:
                    update_job(conn, job_id, status="error", message=f"下载调度异常：{str(exc)[:300]}")
                logger.exception("下载调度失败 job=%s", job_id)
            finally:
                # 提交/统计失败不代表其余 worker 已退出，完成前必须保留歌曲占用。
                if submitted:
                    wait(submitted)
                if conn is not None:
                    conn.close()
                with _active_lock:
                    _active_jobs.discard(job_id)
                    _active_song_ids.difference_update(song_ids)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return {
            "started": True,
            "queued": len(songs),
            "concurrency": DOWNLOAD_THREADS,
            "message": f"已排队 {len(songs)} 首",
        }

    except Exception:
        with _active_lock:
            _active_jobs.discard(job_id)
            _active_song_ids.difference_update(s["id"] for s in songs)
        raise


def _recount_selected(conn, job_id: str, song_ids: list[int], final: bool) -> None:
    """只按本次选择重算进度，避免部分下载时任务永远无法完成。"""
    if not song_ids:
        return
    marks = ",".join("?" for _ in song_ids)
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM songs WHERE id IN ({marks}) GROUP BY status",
        song_ids,
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    done = counts.get("ready", 0)
    failed = counts.get("error", 0)
    total = len(song_ids)
    status = "done" if final else "downloading"
    message = (
        f"完成：成功 {done} 首，失败 {failed} 首"
        if final
        else f"下载中：成功 {done} 首，失败 {failed} 首，全局并发 {DOWNLOAD_THREADS}"
    )
    update_job(conn, job_id, status=status, total=total, done=done, failed=failed, message=message)


def is_job_active(job_id: str) -> bool:
    with _active_lock:
        return job_id in _active_jobs


def is_song_active(song_id: int) -> bool:
    with _active_lock:
        return song_id in _active_song_ids


@contextmanager
def reserve_song_edit(song_id: int):
    """Make the read/edit/write cycle exclusive with other edits and downloads."""
    with _active_lock:
        if song_id in _active_song_ids:
            raise ImportError("歌曲正在下载或编辑，请稍后重试")
        _active_song_ids.add(song_id)
    try:
        yield
    finally:
        with _active_lock:
            _active_song_ids.discard(song_id)
