"""音频下载（yt-dlp）与元数据写入（mutagen）。

流程：yt-dlp 抽取原始 AAC/M4A（不重新编码）→ 嵌入元数据/压缩封面/歌词 → 曲库。
数据中心 IP 易触发 B 站 412，策略：注入真实 buvid 指纹 Cookie + 自动重试。
"""
import io
import logging
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

import httpx
import mutagen
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image, ImageOps

from .media import atomic_media_copy, media_lock, normalize_m4a
from . import bilibili, cdn
from .config import (BILI_HEADERS, COVER_DIR, COVER_MAX_SIZE, COVER_QUALITY,
                     MUSIC_DIR)

logger = logging.getLogger(__name__)


class _DashDownloadError(RuntimeError):
    def __init__(self, expected_duration: float):
        super().__init__("DASH CDN 地址均下载失败或音频不完整")
        self.expected_duration = expected_duration


def _safe(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # A filename component is limited in bytes, not characters (Chinese UTF-8
    # uses three bytes per character). Leave room for the bvid and extension.
    name = name[:max_len].encode("utf-8")[:200].decode("utf-8", errors="ignore")
    return name.strip(" .") or "未命名"


def write_cookie_file(cookie_str: str, path: Path) -> Path:
    """把浏览器复制的 Cookie 字符串转成 yt-dlp 用的 Netscape 格式文件。"""
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{k.strip()}\t{v.strip()}")
    path.write_text("\n".join(lines) + "\n")
    # 私密收藏夹自动更新依赖服务端持久化 Cookie；文件只允许容器用户读取。
    path.chmod(0o600)
    return path


def download_audio(bvid: str, cookie_file: Path | None = None, progress_hook=None,
                   cid: int | None = None, part_index: int = 1) -> dict:
    """返回 file、duration、cover、tmp_dir；调用方在 finally 清理 tmp_dir。

    cid/part_index 指定下载哪一分P（多P视频）；默认 P1。
    策略：直连 DASH 音频（view→cid→playurl→CDN，不抓网页，绕开 412 网页风控）
         → 失败再回退 yt-dlp（带 buvid 指纹 + 重试）。
    """
    # Same video can be retried or downloaded for another part concurrently.
    # Successful callers own tmp_dir; failures are cleaned up here.
    tmp = Path(tempfile.mkdtemp(prefix="musicplayer-ytdl-"))
    try:
        result = _download_audio(bvid, cookie_file, progress_hook, cid, part_index, tmp)
        result["tmp_dir"] = tmp
        return result
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _download_audio(bvid, cookie_file, progress_hook, cid, part_index, tmp) -> dict:

    cookie = None
    if cookie_file and cookie_file.exists():
        cookie = cookie_file.read_text(encoding="utf-8", errors="ignore")
        # 取原始 Cookie 串（我们写入时保留了 "k=v; k=v" 无法还原，直接传文件内容
        # 供 httpx 用需要还原格式，见 _cookie_str_from_netscape）
        cookie = _cookie_str_from_netscape(cookie)

    # 1) 直连 DASH（JSON API 路线，服务器 IP 已被验证可用）
    #    限流/风控类错误逐步拉长等待时间再重试
    last_err: Exception | None = None
    expected_duration = 0.0
    _retry_sleeps = (4, 12, 25)
    for attempt, wait in enumerate(_retry_sleeps):
        try:
            attempt_dir = tmp / f"dash-{attempt}"
            attempt_dir.mkdir()
            result = _direct_dash(bvid, cookie, attempt_dir, cid)
            expected_duration = float(result.get("duration") or expected_duration)
            return _validate_download(result, expected_duration)
        except Exception as e:  # noqa: BLE001
            last_err = e
            # Preserve the selected CID's duration even if every CDN candidate
            # fails validation, so the webpage fallback cannot accept a shorter
            # file based on its own incomplete duration metadata.
            expected_duration = float(getattr(e, "expected_duration", 0) or expected_duration)
            if not _transient(e):
                break  # 不是限流问题，直接走 yt-dlp 回退
            time.sleep(wait)

    # 2) 回退 yt-dlp（网页路线，带指纹 Cookie，应对 412）
    from yt_dlp.utils import DownloadError

    for attempt, wait in enumerate((8, 20)):
        try:
            attempt_dir = tmp / f"fallback-{attempt}"
            attempt_dir.mkdir()
            result = _ytdlp_download(bvid, cookie_file, progress_hook, attempt_dir, attempt, part_index)
            # Prefer the exact cid's playurl duration if the direct route reached
            # validation. A fallback must not accept the same incomplete audio.
            return _validate_download(result, expected_duration or float(result.get("duration") or 0))
        except DownloadError as e:
            last_err = e
            time.sleep(wait)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(wait)

    raise RuntimeError(f"下载失败（直连与备用路线均失败）: {last_err}")


def _validate_download(result: dict, expected_duration: float) -> dict:
    """A valid M4A container can still contain only part of the source audio."""
    actual = normalize_m4a(Path(result["file"]))
    if math.isfinite(expected_duration) and expected_duration > 0:
        # Bilibili's timelength is precise, while DASH duration can be rounded.
        # Allow encoder padding / short endpoint differences, not missing songs.
        tolerance = max(2.0, min(5.0, expected_duration * 0.01))
        if actual + tolerance < expected_duration:
            raise RuntimeError(
                f"下载音频不完整：实际 {actual:.2f} 秒，所选分P应约 {expected_duration:.2f} 秒"
            )
    result["duration"] = actual
    return result


def _transient(e: Exception) -> bool:
    """判断是否为 B 站限流/风控类瞬时错误（值得等待重试）。"""
    s = str(e)
    return any(k in s for k in ("bad parameter", "-412", "412", "请求", "risk", "限流", "风控", "timeout", "timed out"))


def _cookie_str_from_netscape(text: str) -> str | None:
    """把 Netscape 格式 Cookie 文件还原成 k=v; k=v 字符串（bilibili 域）。"""
    parts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and "bilibili" in fields[0]:
            parts.append(f"{fields[5]}={fields[6]}")
    return "; ".join(parts) or None


def read_cookie_string(path: Path) -> str | None:
    if not path.exists():
        return None
    return _cookie_str_from_netscape(path.read_text(encoding="utf-8", errors="ignore"))


def _direct_dash(bvid: str, cookie: str | None, tmp: Path, cid: int | None = None) -> dict:
    """直连 DASH：view → playurl → 把 CDN 音频直链交给 yt-dlp 下载。

    只走 JSON API 拿地址（不走网页，绕开 412），下载用 yt-dlp 的
    generic extractor 处理直链：断点续传、自动重试、限速也稳。
    cid 指定分P；为空取 P1。
    """
    import yt_dlp

    # 已从收藏夹/数据库拿到 cid 时，view API 只是补封面和时长，不应成为
    # 下载前置条件。数据中心 IP 常见的 412 恰好主要发生在这个详情请求。
    detail = {}
    try:
        detail = bilibili.fetch_detail(bvid, cookie)
    except Exception:
        if cid is None:
            raise
    pages = detail.get("pages") or []
    if cid is not None:
        page = next((p for p in pages if p.get("cid") == cid), None)
    else:
        page = pages[0] if pages else None
    if page is not None:
        cid_used = page.get("cid") or detail.get("cid")
    else:
        cid_used = cid or detail.get("cid")
    if not cid_used:
        raise RuntimeError("视频没有可用的 cid")
    play = bilibili.fetch_playurl(bvid, cid_used, cookie)
    dash = play.get("dash") or {}
    # These describe the selected cid, even when the detail API is blocked.
    # Never use the multi-part video's total duration as a completeness target.
    dur = (float(play.get("timelength") or 0) / 1000
           or float(dash.get("duration") or 0)
           or float((page or {}).get("duration") or 0))
    audios = dash.get("audio") or []
    if not audios:
        raise RuntimeError("该视频没有 DASH 音频流")
    audio = max(audios, key=lambda a: a.get("id", 0))  # 30280 = 192k
    urls = cdn.audio_urls(audio)
    if not urls:
        raise RuntimeError("音频地址为空")

    ydl_opts = {
        "outtmpl": str(tmp / f"{bvid}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 2,
        "fragment_retries": 2,
        "socket_timeout": 15,
        "logger": cdn.SafeDownloadLogger(),
        "continuedl": True,
        "http_headers": {
            "User-Agent": BILI_HEADERS["User-Agent"],
            "Referer": "https://www.bilibili.com/",
        },
    }
    path = None
    for index, url in enumerate(cdn.ordered_urls(urls)):
        # Different endpoints must never resume a partial file from one another.
        endpoint_tmp = tmp / f"cdn-{index}"
        endpoint_tmp.mkdir()
        opts = {**ydl_opts, "outtmpl": str(endpoint_tmp / f"{bvid}.%(ext)s")}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                candidate = Path(ydl.prepare_filename(info))
                if not candidate.exists():
                    candidates = [p for p in endpoint_tmp.glob(f"{bvid}*")
                                  if p.suffix in (".m4a", ".m4s", ".mp3", ".webm")]
                    if not candidates:
                        raise RuntimeError("未找到下载的音频文件")
                    candidate = candidates[0]
            # With backup endpoints, an incomplete download must try another
            # CDN before falling back to the blocked video webpage. The outer
            # download pipeline also validates the accepted result, including
            # streams with just one address.
            if len(urls) > 1:
                _validate_download({"file": candidate}, float(dur or 0))
            path = candidate
            break
        except Exception:
            cdn.failed(url)
            logger.warning("DASH CDN 候选 %d 下载或完整性校验失败，尝试备用地址", index + 1)
    if path is None:
        raise _DashDownloadError(float(dur or 0))

    cover = None
    pic = detail.get("pic")
    if pic:
        try:
            r = httpx.get(pic, headers=BILI_HEADERS, timeout=15)
            if r.status_code == 200:
                cover = r.content
        except Exception:
            cover = None
    return {"file": path, "duration": float(dur or 0), "cover": cover}


def _ytdlp_download(bvid: str, cookie_file: Path | None, progress_hook, tmp: Path,
                    attempt: int, part_index: int = 1) -> dict:
    import yt_dlp

    # 合并用户 Cookie + 每次全新获取的 buvid 指纹（应对数据中心 IP 风控）
    merged = _merged_cookie_file(cookie_file, tmp, attempt)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(tmp / "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "0",
            }
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        # 批量下载时温柔一点，避免触发 B 站 412
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "sleep_requests": 0.5,
        "http_headers": {
            "User-Agent": BILI_HEADERS["User-Agent"],
            "Referer": "https://www.bilibili.com/",
        },
    }
    if merged:
        ydl_opts["cookiefile"] = str(merged)
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        url = f"https://www.bilibili.com/video/{bvid}"
        if part_index > 1:
            url += f"?p={part_index}"
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        m4a = path.with_suffix(".m4a")
        if not m4a.exists():  # 某些情况下后缀不同
            candidates = [p for p in tmp.glob(f"{info['id']}*") if p.suffix in (".m4a", ".mp3", ".webm", ".m4s")]
            if not candidates:
                raise RuntimeError("未找到下载的音频文件")
            m4a = candidates[0]
        cover = None
        thumb = info.get("thumbnail")
        if thumb:
            try:
                r = httpx.get(thumb, headers=BILI_HEADERS, timeout=15)
                if r.status_code == 200:
                    cover = r.content
            except Exception:
                cover = None
        return {"file": m4a, "duration": float(info.get("duration") or 0), "cover": cover}


def _merged_cookie_file(cookie_file: Path | None, tmp: Path, attempt: int) -> Path | None:
    """用户 Cookie（若有）与 buvid 指纹合并为 Netscape 格式 Cookie 文件。"""
    buvids = bilibili.fetch_buvid()
    if cookie_file and cookie_file.exists():
        user_lines = cookie_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    else:
        user_lines = []
    lines = ["# Netscape HTTP Cookie File"] + user_lines
    for k, v in buvids.items():
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{k}\t{v}")
    if len(lines) == 1:
        return None
    p = tmp / f"cookies-{attempt}.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    p.chmod(0o600)
    return p


def metadata_needs_update(path: Path, title: str, artist: str, album: str) -> bool:
    """Read-only check used by the historical metadata repair command."""
    tags = MP4(path).tags or {}
    return any((
        tags.get("\xa9nam") != [title],
        tags.get("\xa9ART") != [artist],
        tags.get("\xa9alb", []) != ([album] if album else []),
    ))


def tag_and_store(
    src: Path,
    title: str,
    artist: str,
    album: str,
    cover_bytes: bytes | None,
    bvid: str,
    lyrics_text: str | None = None,
) -> str:
    """Write a complete tagged file, then atomically publish it to the library."""
    normalize_m4a(src)
    audio = MP4(str(src))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags["\xa9nam"] = title
    tags["\xa9ART"] = artist
    if album:
        tags["\xa9alb"] = album
    else:
        tags.pop("\xa9alb", None)
    if cover_bytes:
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
    if lyrics_text:
        tags["\xa9lyr"] = lyrics_text
    else:
        tags.pop("\xa9lyr", None)
    audio.save()

    album_dir = MUSIC_DIR / _safe(album or "未分类")
    album_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe(f"{artist} - {title}")
    final = album_dir / f"{filename} [{bvid}].m4a"
    # Copy across filesystems into an unscannable sibling first: shutil.move
    # directly onto an existing song truncates it while clients are streaming.
    _atomic_write(final, lambda target: shutil.copyfile(src, target))
    try:
        src.unlink(missing_ok=True)
    except OSError:
        logger.warning("临时音频清理失败: %s", src, exc_info=True)
    return str(final.relative_to(MUSIC_DIR))


def _atomic_write(path: Path, writer, mode: int = 0o644) -> None:
    with media_lock(path):
        fd, name = tempfile.mkstemp(prefix=".musicplayer-", suffix=".tmp", dir=path.parent)
        os.close(fd)
        target = Path(name)
        try:
            writer(target)
            target.chmod(mode)
            with target.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(target, path)
        finally:
            target.unlink(missing_ok=True)


def optimize_cover(cover_bytes: bytes | None) -> bytes | None:
    """封面统一缩成小尺寸 JPEG，避免保存 B 站原始大图。"""
    if not cover_bytes:
        return None
    try:
        with Image.open(io.BytesIO(cover_bytes)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((COVER_MAX_SIZE, COVER_MAX_SIZE), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=COVER_QUALITY, optimize=True, progressive=True)
            return buf.getvalue()
    except (OSError, ValueError):
        return None


def write_lrc_sidecar(rel_path: str, lrc: str) -> None:
    """在音频旁写同名 .lrc 歌词文件（Navidrome/Amperfy 会读取）。"""
    if not lrc:
        return
    path = MUSIC_DIR / rel_path
    sidecar = path.with_suffix(".lrc")
    try:
        _atomic_write(sidecar, lambda target: target.write_text(lrc, encoding="utf-8"))
    except OSError:
        logger.warning("歌词侧车保存失败: %s", rel_path, exc_info=True)


def remove_lrc_sidecar(rel_path: str) -> None:
    """删除音频旁的歌词文件。"""
    try:
        (MUSIC_DIR / rel_path).with_suffix(".lrc").unlink(missing_ok=True)
    except OSError:
        pass


def update_embedded_lyrics(rel_path: str, text: str | None) -> None:
    """同步 M4A 内嵌歌词；失败不影响数据库与侧车歌词。"""
    path = MUSIC_DIR / rel_path
    if not path.exists():
        return
    try:
        with atomic_media_copy(path) as target:
            audio = MP4(str(target))
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
            if text:
                tags["\xa9lyr"] = text
            else:
                tags.pop("\xa9lyr", None)
            audio.save()
    except (OSError, mutagen.MutagenError):
        logger.warning("内嵌歌词保存失败: %s", rel_path, exc_info=True)


def update_audio_metadata(path: Path, title: str | None = None,
                          artist: str | None = None,
                          album: str | None = None) -> None:
    """更新 M4A 标签；空专辑会真正移除旧标签。"""
    with atomic_media_copy(path) as target:
        audio = MP4(str(target))
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        if title is not None:
            tags["\xa9nam"] = title
        if artist is not None:
            tags["\xa9ART"] = artist
        if album is not None:
            if album:
                tags["\xa9alb"] = album
            else:
                tags.pop("\xa9alb", None)
        audio.save()


def save_cover(cover_bytes: bytes, bvid: str) -> str | None:
    """缓存封面文件，返回文件名。"""
    if not cover_bytes:
        return None
    name = f"{bvid}.jpg"
    try:
        _atomic_write(COVER_DIR / name, lambda target: target.write_bytes(cover_bytes))
    except OSError:
        logger.warning("封面保存失败: %s", bvid, exc_info=True)
        return None
    return name


def fetch_cover(bvid: str, cookie: str | None = None, cover_url: str | None = None) -> bytes:
    from . import bilibili
    url = cover_url or bilibili.fetch_detail(bvid, cookie).get("pic")
    if not url:
        raise ValueError("B站视频没有可下载的封面")
    if url.startswith("//"):
        url = "https:" + url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not (parsed.hostname or "").endswith(".hdslb.com"):
        raise ValueError("B站返回的封面地址无效")
    with httpx.Client(headers=BILI_HEADERS, timeout=15) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 10 * 1024 * 1024:
                    raise ValueError("封面图片过大")
    cover = optimize_cover(bytes(data))
    if not cover:
        raise ValueError("封面图片无法读取")
    return cover


def update_embedded_cover(path: Path, cover: bytes) -> None:
    with atomic_media_copy(path) as target:
        audio = MP4(str(target))
        if audio.tags is None:
            audio.add_tags()
        audio.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
