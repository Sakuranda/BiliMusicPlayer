"""MusicPlayer API — FastAPI 入口。"""
import csv
import io
import json
import logging
import os
import sqlite3
import secrets
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .bilibili import BiliError
from .config import ALLOWED_ORIGINS, API_TOKEN, COVER_DIR, COOKIE_DIR, MUSIC_DIR, SESSION_TTL_HOURS
from .db import (access_audit, add_access_session, add_song_to_playlist,
                 cached_ip_location, create_playlist, delete_playlist,
                 delete_collection, delete_song, get_collection, get_conn,
                 get_job, get_playlist, get_song, list_collection_songs,
                 list_collections, list_jobs, list_playlist_songs, list_playlists, list_songs,
                 now, reconcile_downloaded_files, recover_interrupted_downloads, remove_song_from_playlist,
                 rename_playlist, touch_access_session, update_collection, update_song)
from . import auth as auth_service, admin, bilibili, downloader, lyrics, parser
from .importer import (ImportError, is_job_active, is_song_active,
                       materialize_cached_parts, parse_and_store, refresh_collection, reserve_song_edit,
                       start_collection_scheduler, start_download,
                       stop_collection_scheduler)
from .artist_playlists import sync_artist_playlists, sync_artist_playlists_safely
from .schemas import (PartSelection, CollectionUpdate, ImportRequest, LoginRequest, SecurityUpdate,
                      PlaylistWrite, SongUpdate, StartRequest)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = get_conn()
    try:
        recovered_songs, recovered_jobs = recover_interrupted_downloads(conn)
        repaired_files = reconcile_downloaded_files(conn)
        materialize_cached_parts(conn)
        if recovered_songs or recovered_jobs:
            logging.getLogger("uvicorn.error").warning(
                "已恢复中断状态：songs=%d jobs=%d", recovered_songs, recovered_jobs
            )
        if repaired_files:
            logging.getLogger("uvicorn.error").warning(
                "已按实际音频文件修复歌曲状态：songs=%d", repaired_files
            )
    finally:
        conn.close()
    sync_artist_playlists_safely()
    start_collection_scheduler()
    try:
        yield
    finally:
        stop_collection_scheduler()


app = FastAPI(title="BiliMusicPlayer API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/captcha",
    "/api/auth/login",
    "/api/auth/logout",
}


@app.middleware("http")
async def authentication_guard(request: Request, call_next):
    """网页会话为主，API Token 作为脚本与旧客户端兼容通道。"""
    session = auth_service.verify_session(request.cookies.get(auth_service.COOKIE_NAME))
    token = request.headers.get("X-Api-Token") or request.query_params.get("token")
    token_valid = bool(API_TOKEN and token and secrets.compare_digest(token.encode(), API_TOKEN.encode()))
    request.state.auth = session
    request.state.token_authenticated = token_valid

    protected = (
        request.method != "OPTIONS"
        and request.url.path.startswith("/api")
        and request.url.path not in PUBLIC_API_PATHS
    )
    auth_required = auth_service.enabled() or bool(API_TOKEN)
    if protected and auth_required and not session and not token_valid:
        return JSONResponse({"detail": "请先登录"}, status_code=401)

    if session and request.url.path.startswith("/api") and auth_service.should_touch(session["sid"]):
        conn = get_conn()
        try:
            touch_access_session(conn, session["sid"])
        finally:
            conn.close()
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "musicplayer"}


# ---------- 登录与访问审计 ----------

@app.get("/api/auth/status")
def auth_status(request: Request):
    session = request.state.auth
    disabled = not auth_service.enabled()
    return {
        "enabled": not disabled,
        "authenticated": disabled or bool(session) or request.state.token_authenticated,
        "username": session.get("sub") if session else None,
        "two_factor_enabled": admin.settings()['two_factor_enabled'],
        "navidrome_url": 'https://' + os.environ['NAVIDROME_DOMAIN'] if os.environ.get('NAVIDROME_DOMAIN') else None,
    }


@app.get("/api/auth/captcha")
def auth_captcha():
    if not auth_service.enabled():
        raise HTTPException(404, "登录鉴权未启用")
    return auth_service.create_captcha()


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request):
    if not auth_service.enabled():
        raise HTTPException(400, "服务器未启用网页登录")
    ip = auth_service.client_ip(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
    )
    allowed, retry_after = auth_service.login_allowed(ip)
    if not allowed:
        raise HTTPException(429, f"尝试次数过多，请 {retry_after} 秒后再试")
    if not auth_service.consume_captcha(req.captcha_id, req.captcha):
        auth_service.record_failure(ip)
        raise HTTPException(400, "验证码错误或已过期，请刷新后重试")
    epoch = admin.authenticate(req.username, req.password, req.totp_code)
    if epoch is None:
        auth_service.record_failure(ip)
        raise HTTPException(401, "账户、密码或动态验证码错误")

    auth_service.clear_failures(ip)
    token, session_id = auth_service.create_session(req.username, epoch)
    conn = get_conn()
    try:
        location = cached_ip_location(conn, ip) or auth_service.lookup_location(ip)
        timestamp = now()
        add_access_session(conn, {
            "id": session_id,
            "username": req.username,
            "ip": ip,
            **location,
            "user_agent": request.headers.get("User-Agent", "")[:500],
            "login_at": timestamp,
            "last_seen": timestamp,
        })
    finally:
        conn.close()

    response = JSONResponse({"authenticated": True, "username": req.username})
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    response.set_cookie(
        auth_service.COOKIE_NAME,
        token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(auth_service.COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/access-audit")
def auth_access_audit():
    conn = get_conn()
    try:
        return access_audit(conn)
    finally:
        conn.close()


@app.get('/api/auth/settings')
def admin_settings(request: Request):
    if not request.state.auth:
        raise HTTPException(401, '请使用管理员账户登录')
    return admin.settings()


def security_change(action: str, req: SecurityUpdate, request: Request):
    session = request.state.auth
    if not session:
        raise HTTPException(401, '修改账户设置需要管理员网页登录')
    ip = auth_service.client_ip(request.client.host if request.client else None,
                                request.headers.get('X-Forwarded-For'))
    allowed, retry_after = auth_service.login_allowed(ip)
    if not allowed:
        raise HTTPException(429, f'请 {retry_after} 秒后再试')
    try:
        result = admin.change(action, req.current_password, req.code, session['epoch'], req.username, req.new_password)
    except ValueError as e:
        auth_service.record_failure(ip)
        raise HTTPException(400, str(e)) from e
    auth_service.clear_failures(ip)
    epoch = result.pop('_epoch')
    response = JSONResponse(result)
    response.headers['Cache-Control'] = 'no-store'
    if action != 'setup':
        # Rotate all other sessions, keeping this browser logged in.
        token, _ = auth_service.create_session(result['username'], epoch, session['sid'])
        response.set_cookie(auth_service.COOKIE_NAME, token, httponly=True,
                            max_age=SESSION_TTL_HOURS * 3600,
                            secure=request.url.scheme == 'https' or request.headers.get('X-Forwarded-Proto') == 'https',
                            samesite='lax', path='/')
    return response


@app.put('/api/auth/settings')
def update_admin_settings(req: SecurityUpdate, request: Request):
    return security_change('account', req, request)


@app.post('/api/auth/2fa/setup')
def setup_two_factor(req: SecurityUpdate, request: Request):
    return security_change('setup', req, request)


@app.post('/api/auth/2fa/enable')
def enable_two_factor(req: SecurityUpdate, request: Request):
    return security_change('enable', req, request)


@app.post('/api/auth/2fa/disable')
def disable_two_factor(req: SecurityUpdate, request: Request):
    return security_change('disable', req, request)


# ---------- 导入任务 ----------

@app.post("/api/jobs")
def create_job(req: ImportRequest):
    """粘贴收藏夹链接 → 解析标题 → 返回预览（不下载）。"""
    try:
        job_id = parse_and_store(
            req.url, req.cookie, req.album,
            save_collection=req.save_collection,
            auto_update=req.auto_update and req.save_collection,
            multipart_policy=req.multipart_policy,
        )
    except (ImportError, BiliError) as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        logging.getLogger("uvicorn.error").exception("导入失败 url=%r", req.url)
        raise HTTPException(502, f"解析失败：{e}") from e
    conn = get_conn()
    return {"job": get_job(conn, job_id), "songs": list_songs(conn, job_id=job_id)}


@app.get("/api/jobs")
def jobs():
    with closing(get_conn()) as conn:
        return list_jobs(conn)


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    with closing(get_conn()) as conn:
        job = get_job(conn, job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return {"job": job, "songs": list_songs(conn, job_id=job_id)}


@app.post("/api/jobs/{job_id}/start")
def job_start(job_id: str, req: StartRequest | None = None):
    """确认元数据后开始下载（可只下载勾选的 bvid）。"""
    conn = get_conn()
    if not get_job(conn, job_id):
        raise HTTPException(404, "任务不存在")
    try:
        result = start_download(
            job_id,
            req.bvids if req else None,
            req.fetch_lyrics if req else True,
            song_ids=req.song_ids if req else None,
            record_choices=bool(req and req.song_ids is not None),
        )
    except ImportError as e:
        raise HTTPException(400, str(e)) from e
    return result


@app.delete("/api/jobs/{job_id}")
def job_delete(job_id: str):
    conn = get_conn()
    if not get_job(conn, job_id):
        raise HTTPException(404, "任务不存在")
    if is_job_active(job_id):
        raise HTTPException(409, "任务正在下载，完成后才能删除")
    memberships = conn.execute(
        "SELECT song_id, is_new FROM job_songs WHERE job_id = ?", (job_id,)
    ).fetchall()
    if memberships:
        for membership in memberships:
            song = get_song(conn, membership["song_id"])
            shared = conn.execute(
                "SELECT 1 FROM job_songs WHERE song_id=? AND job_id<>? "
                "UNION ALL SELECT 1 FROM collection_songs WHERE song_id=? LIMIT 1",
                (membership["song_id"], job_id, membership["song_id"]),
            ).fetchone()
            if (membership["is_new"] and song and not song.get("file_path")
                    and not shared and not is_song_active(song["id"])):
                conn.execute("DELETE FROM songs WHERE id = ?", (song["id"],))
    else:
        # 兼容升级前尚未迁移的任务。
        for song in list_songs(conn, job_id=job_id):
            if song.get("file_path") or song["status"] == "ready":
                conn.execute("UPDATE songs SET job_id = NULL WHERE id = ?", (song["id"],))
            else:
                conn.execute("DELETE FROM songs WHERE id = ?", (song["id"],))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    return {"deleted": True}


# ---------- 已保存收藏夹 ----------

@app.get("/api/collections")
def collections():
    with closing(get_conn()) as conn:
        return list_collections(conn)


@app.get("/api/collections/{collection_id}/songs")
def collection_songs(collection_id: int, multipart_only: bool = False):
    with closing(get_conn()) as conn:
        if not get_collection(conn, collection_id):
            raise HTTPException(404, "收藏夹不存在")
        songs = list_collection_songs(conn, collection_id)
        return [song for song in songs if len(song.get("parts") or []) > 1] if multipart_only else songs


@app.patch("/api/collections/{collection_id}")
def collection_update(collection_id: int, req: CollectionUpdate):
    with closing(get_conn()) as conn:
        if not get_collection(conn, collection_id):
            raise HTTPException(404, "收藏夹不存在")
        fields = {
            key: int(value) if isinstance(value, bool) else value
            for key, value in req.model_dump().items() if value is not None
        }
        return update_collection(conn, collection_id, **fields)


@app.post("/api/collections/{collection_id}/refresh")
def collection_refresh(collection_id: int):
    conn = get_conn()
    if not get_collection(conn, collection_id):
        raise HTTPException(404, "收藏夹不存在")
    try:
        return refresh_collection(collection_id)
    except (ImportError, BiliError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("uvicorn.error").exception(
            "手动更新收藏夹失败 id=%s", collection_id
        )
        raise HTTPException(502, f"更新失败：{exc}") from exc


@app.post("/api/collections/{collection_id}/parts")
def collection_parts(collection_id: int, req: PartSelection):
    from .db import create_job as store_job, add_job_song
    with closing(get_conn()) as conn:
        collection = get_collection(conn, collection_id)
        if not collection:
            raise HTTPException(404, "收藏夹不存在")
        members = list_collection_songs(conn, collection_id)
        reviewed = [s for s in members if s["bvid"] in req.bvids and len(s.get("parts") or []) > 1]
        if set(req.bvids) != {s["bvid"] for s in reviewed} or not set(req.song_ids).issubset({s["id"] for s in reviewed}):
            raise HTTPException(400, "请选择该收藏夹中的多P歌曲")
        if any(is_song_active(s["id"]) for s in reviewed):
            raise HTTPException(409, "部分分P正在下载或编辑，请稍后重试")
        job_id = secrets.token_hex(6)
        store_job(conn, job_id, collection["url"], collection["media_id"], collection["title"], len(req.song_ids), collection_id)
        for position, song in enumerate(reviewed):
            add_job_song(conn, job_id, song["id"], position, False)
        if req.song_ids:
            try:
                result = start_download(job_id, fetch_lyrics=collection["fetch_lyrics"], song_ids=req.song_ids, record_choices=True)
            except ImportError as exc:
                raise HTTPException(409, str(exc)) from exc
        else:
            for song in reviewed:
                update_song(conn, song["id"], download_requested=0)
            from .db import update_job
            update_job(conn, job_id, status="done", message="已跳过所选多P视频")
            result = {"started": False, "queued": 0, "concurrency": 0, "message": "已跳过所选多P视频"}
        return {"job_id": job_id, **result}


@app.delete("/api/collections/{collection_id}")
def collection_delete(collection_id: int):
    conn = get_conn()
    if not delete_collection(conn, collection_id):
        raise HTTPException(404, "收藏夹不存在")
    return {"deleted": True, "songs_preserved": True}


# ---------- 歌曲 ----------

@app.get("/api/songs")
def songs(status: str | None = None):
    with closing(get_conn()) as conn:
        return list_songs(conn, status=status)


def _csv_cell(value):
    # Spreadsheet import must treat externally supplied song metadata as text.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


@app.get("/api/export.csv")
def export_csv():
    """导出全曲库为 CSV（含 B 站视频链接），方便备份与去重核对。"""
    with closing(get_conn()) as conn:
        all_songs = list_songs(conn)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "歌曲名", "歌手", "专辑", "bvid", "B站视频链接", "分P", "时长(秒)", "状态", "歌词来源"])
        for s in all_songs:
            w.writerow([_csv_cell(value) for value in [
                s["id"], s["title"], s["artist"], s.get("album") or "",
                s["bvid"], s.get("source_url") or f"https://www.bilibili.com/video/{s['bvid']}",
                s.get("part_index") or 1, s.get("duration") or "", s["status"],
                s.get("lyrics_source") or "",
            ]])
        # UTF-8 BOM，Excel 打开中文不乱码
        return Response(
            content=("\ufeff" + buf.getvalue()).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=musicplayer-songs.csv"},
        )


@app.get("/api/songs/{sid}")
def song_detail(sid: int):
    with closing(get_conn()) as conn:
        song = get_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        return song


@contextmanager
def _song_edit(sid: int):
    if is_song_active(sid):
        raise HTTPException(409, "歌曲正在下载或编辑，请稍后重试")
    try:
        with reserve_song_edit(sid):
            yield
    except ImportError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.patch("/api/songs/{sid}")
def song_update(sid: int, req: SongUpdate):
    with _song_edit(sid):
        with closing(get_conn()) as conn:
            song = get_song(conn, sid)
            if not song:
                raise HTTPException(404, "歌曲不存在")
            fields = {k: v for k, v in req.model_dump().items() if v is not None}
            if "cid" in fields and fields["cid"] != song.get("cid"):
                raise HTTPException(400, "每个分P已独立保存，请在导入页面选择对应歌曲")
            elif any(key in fields for key in ("part_index", "part_title", "duration")):
                # 这些字段由已验证的 cid 决定，不能让客户端覆盖实际音轨时长。
                for key in ("part_index", "part_title", "duration"):
                    fields.pop(key, None)
            metadata = dict(song.get("metadata") or {})
            for key in ("title", "artist"):
                if key in fields:
                    metadata.update({key: fields[key], f"{key}_source": "manual", f"{key}_confidence": 1.0})
            if any(key in fields for key in ("title", "artist")):
                metadata["needs_review"] = any(metadata.get(f"{key}_confidence", 0) < 0.8 for key in ("title", "artist"))
                if not metadata["needs_review"]:
                    metadata["reasons"] = []
                fields["metadata"] = json.dumps(metadata, ensure_ascii=False)
            # 先写标签，失败明确返回；避免网页显示修改成功、iOS仍沿用旧信息。
            _retag_file(song, fields)
            update_song(conn, sid, **fields)
            sync_artist_playlists_safely()
            return get_song(conn, sid)


@app.post("/api/songs/{sid}/analyze")
def song_analyze(sid: int, refresh: bool = False):
    """只生成建议，不覆盖歌曲；refresh 时尽力补充 B 站标签和简介。"""
    with closing(get_conn()) as conn:
        song = get_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        known = parser.known_artists_from_songs(list_songs(conn))
        tags = song.get("tags") or []
        description = song.get("description") or ""
        warnings = []
        if refresh:
            refreshed_tags = bilibili.fetch_tags(song["bvid"])
            if refreshed_tags:
                tags = refreshed_tags
            else:
                warnings.append("未获取到新标签，使用已保存的标签")
            try:
                description = bilibili.fetch_detail(song["bvid"]).get("desc") or description
            except Exception:
                warnings.append("未获取到视频简介，使用已保存的信息")
        result = parser.parse_metadata(song["raw_title"], song["uploader"], tags, description,
                                       known_artists=known)
        return {**result, "warnings": warnings}


@app.put("/api/songs/{sid}/lyrics")
async def song_lyrics_upload(sid: int, file: UploadFile = File(...)):
    """上传 UTF-8/UTF-16/GB18030 的 .lrc 或 .txt 歌词。"""
    with _song_edit(sid):
        conn = get_conn()
        song = get_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".lrc", ".txt"):
            raise HTTPException(400, "只支持 .lrc 或 .txt 歌词文件")
        data = await file.read(1024 * 1024 + 1)
        await file.close()
        try:
            text = lyrics.decode_lyrics_file(data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        plain = lyrics.lrc_to_plain(text) or text.strip()
        update_song(
            conn,
            sid,
            lyrics=plain,
            lrc=text,
            lyrics_source="upload",
            lyrics_enabled=1,
        )
        if song.get("file_path"):
            downloader.write_lrc_sidecar(song["file_path"], text)
            downloader.update_embedded_lyrics(song["file_path"], text)
        return get_song(conn, sid)


@app.delete("/api/songs/{sid}/lyrics")
def song_lyrics_delete(sid: int):
    """删除数据库、侧车文件和音频标签中的歌词。"""
    with _song_edit(sid):
        conn = get_conn()
        song = get_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        update_song(conn, sid, lyrics=None, lrc=None, lyrics_source=None)
        if song.get("file_path"):
            downloader.remove_lrc_sidecar(song["file_path"])
            downloader.update_embedded_lyrics(song["file_path"], None)
        return get_song(conn, sid)


def _retag_file(song: dict, fields: dict) -> None:
    """把改动的元数据写回音频文件标签（Navidrome/iOS 同步）。"""
    if not song.get("file_path"):
        return
    path = MUSIC_DIR / song["file_path"]
    if not path.exists():
        return
    try:
        downloader.update_audio_metadata(
            path,
            title=fields.get("title"),
            artist=fields.get("artist"),
            album=fields.get("album"),
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("uvicorn.error").exception("写入音频标签失败 sid=%s", song["id"])
        raise HTTPException(503, "音频标签保存失败，歌曲信息尚未更新，请稍后重试") from exc


@app.delete("/api/songs/{sid}")
def song_delete(sid: int):
    with _song_edit(sid):
        conn = get_conn()
        song = delete_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        _remove_file(song)
        return {"deleted": True}


# ---------- 歌单 ----------

def _playlist_name(name: str) -> str:
    clean = " ".join(name.strip().split())
    if not clean:
        raise HTTPException(400, "歌单名称不能为空")
    if len(clean) > 80:
        raise HTTPException(400, "歌单名称不能超过 80 个字符")
    return clean


@app.get("/api/playlists")
def playlists():
    with closing(get_conn()) as conn:
        return list_playlists(conn)


@app.post("/api/playlists", status_code=201)
def playlist_create(req: PlaylistWrite):
    conn = get_conn()
    try:
        return create_playlist(conn, _playlist_name(req.name))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "已经有同名歌单") from exc


@app.patch("/api/playlists/{playlist_id}")
def playlist_rename(playlist_id: int, req: PlaylistWrite):
    conn = get_conn()
    if not get_playlist(conn, playlist_id):
        raise HTTPException(404, "歌单不存在")
    try:
        return rename_playlist(conn, playlist_id, _playlist_name(req.name))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "已经有同名歌单") from exc


@app.delete("/api/playlists/{playlist_id}")
def playlist_delete(playlist_id: int):
    conn = get_conn()
    if not delete_playlist(conn, playlist_id):
        raise HTTPException(404, "歌单不存在")
    return {"deleted": True}


@app.get("/api/playlists/{playlist_id}/songs")
def playlist_songs(playlist_id: int):
    with closing(get_conn()) as conn:
        if not get_playlist(conn, playlist_id):
            raise HTTPException(404, "歌单不存在")
        return list_playlist_songs(conn, playlist_id)


@app.post("/api/playlists/{playlist_id}/songs/{sid}")
def playlist_song_add(playlist_id: int, sid: int):
    conn = get_conn()
    if not get_playlist(conn, playlist_id):
        raise HTTPException(404, "歌单不存在")
    if not get_song(conn, sid):
        raise HTTPException(404, "歌曲不存在")
    added = add_song_to_playlist(conn, playlist_id, sid)
    return {"added": added}


@app.delete("/api/playlists/{playlist_id}/songs/{sid}")
def playlist_song_remove(playlist_id: int, sid: int):
    conn = get_conn()
    if not get_playlist(conn, playlist_id):
        raise HTTPException(404, "歌单不存在")
    removed = remove_song_from_playlist(conn, playlist_id, sid)
    return {"removed": removed}


# ---------- 媒体 ----------

@app.api_route("/api/stream/{sid}", methods=["GET", "HEAD"])
def stream(sid: int):
    conn = get_conn()
    try:
        song = get_song(conn, sid)
    finally:
        conn.close()
    if not song or not song.get("file_path"):
        raise HTTPException(404, "音频不存在")
    path = MUSIC_DIR / song["file_path"]
    if not path.exists():
        raise HTTPException(404, "音频文件丢失")
    return FileResponse(path, media_type="audio/mp4",
                        filename=f"{song['artist']} - {song['title']}.m4a",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-cache"})


@app.post("/api/artists/sync")
def artist_sync():
    try:
        return {"artists": sync_artist_playlists()}
    except Exception as exc:
        raise HTTPException(503, "歌手分类同步失败，请稍后重试") from exc


@app.post("/api/songs/{sid}/cover/refresh")
def refresh_song_cover(sid: int):
    with _song_edit(sid), closing(get_conn()) as conn:
        song = get_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        job = conn.execute("SELECT j.media_id FROM jobs j JOIN job_songs js ON js.job_id=j.id WHERE js.song_id=? LIMIT 1", (sid,)).fetchone()
        cookie = downloader.read_cookie_string(COOKIE_DIR / f"{job[0]}.txt") if job else None
        name = None
        try:
            try:
                cover_bytes = downloader.fetch_cover(song["bvid"], cookie)
            except (BiliError, ValueError):
                if not job or not job[0]:
                    raise
                _, items, _ = bilibili.fetch_favorites([job[0]], cookie)
                picture = next((item["cover_url"] for item in items if item["bvid"] == song["bvid"]), None)
                if not picture:
                    raise ValueError("收藏夹中没有该视频的封面")
                cover_bytes = downloader.fetch_cover(song["bvid"], cookie, cover_url=picture)
            name = downloader.save_cover(cover_bytes, f"song-{sid}-{secrets.token_hex(6)}")
            if not name:
                raise OSError("封面文件保存失败")
            if song.get("file_path") and (MUSIC_DIR / song["file_path"]).is_file():
                downloader.update_embedded_cover(MUSIC_DIR / song["file_path"], cover_bytes)
            update_song(conn, sid, cover_url=name)
        except Exception as exc:
            if name:
                (COVER_DIR / name).unlink(missing_ok=True)
            logging.getLogger("uvicorn.error").exception("封面更新失败 sid=%s", sid)
            raise HTTPException(502, "重新下载封面失败，原封面已保留，请稍后重试") from exc
        old = song.get("cover_url")
        if old and not str(old).startswith("http") and not conn.execute("SELECT 1 FROM songs WHERE cover_url=?", (old,)).fetchone():
            (COVER_DIR / old).unlink(missing_ok=True)
        return get_song(conn, sid)


@app.get("/api/songs/{sid}/cover")
def cover(sid: int):
    with closing(get_conn()) as conn:
        song = get_song(conn, sid)
        if not song:
            raise HTTPException(404, "歌曲不存在")
        if song.get("cover_url") and not str(song["cover_url"]).startswith("http"):
            path = COVER_DIR / song["cover_url"]
            if path.exists():
                return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})
        if song.get("cover_url"):
            return RedirectResponse(song["cover_url"])
        raise HTTPException(404, "无封面")


def _remove_file(song: dict) -> None:
    if song.get("file_path"):
        p = MUSIC_DIR / song["file_path"]
        try:
            p.unlink(missing_ok=True)
            p.with_suffix(".lrc").unlink(missing_ok=True)
        except OSError:
            pass
    sync_artist_playlists_safely()
    with closing(get_conn()) as conn:
        shared_cover = conn.execute("SELECT 1 FROM songs WHERE cover_url=?", (song.get("cover_url"),)).fetchone()
    if not shared_cover and song.get("cover_url") and not str(song["cover_url"]).startswith("http"):
        try:
            (COVER_DIR / song["cover_url"]).unlink(missing_ok=True)
        except OSError:
            pass


# 托管前端静态文件（必须在所有 /api 路由之后挂载）
WEB_DIST = os.environ.get("WEB_DIST", "")
if WEB_DIST and Path(WEB_DIST).is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True, check_dir=False), name="web")
