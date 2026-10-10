"""歌词匹配：LRCLIB（主）→ 网易云（备）→ QQ 音乐（兜底）。

翻唱曲目在数据库中常查不到，采用逐级降级：
  1. 歌名 + 歌手（带时长参数、模糊匹配、过滤纯音乐）
  2. 仅歌名（去掉 cover/翻唱 干扰词）
  3. 网易云搜索（歌名 → 歌名+歌手），可返回 LRC + 翻译
  4. QQ 音乐搜索（需 y.qq.com Referer）
均失败则返回空，前端不显示歌词即可。
"""
import re
import threading
import time
from difflib import SequenceMatcher
from functools import lru_cache

import httpx

from .config import LYRICS_API_INTERVAL

UA = "MusicPlayer/1.0 (https://github.com/Sakuranda/BiliMusicPlayer)"

LRCLIB_SEARCH = "https://lrclib.net/api/search"
NETEASE_SEARCH = "https://music.163.com/api/search/get/web"
NETEASE_LYRIC = "https://music.163.com/api/song/lyric"
QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
QQ_LYRIC = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

_rate_lock = threading.Lock()
_last_request = 0.0


def _throttle() -> None:
    """多个下载线程共享歌词 API 限速，避免大收藏夹形成请求尖峰。"""
    global _last_request
    with _rate_lock:
        wait = LYRICS_API_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def lrc_to_plain(text: str) -> str:
    """移除 LRC 元数据/时间轴，得到适合内嵌和普通展示的纯文本。"""
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        plain = re.sub(r"\[[^\]]*]", "", line).strip()
        if plain:
            lines.append(plain)
    return "\n".join(lines)


def decode_lyrics_file(data: bytes) -> str:
    """兼容常见歌词文件编码，并拒绝二进制/空文件。"""
    if not data:
        raise ValueError("歌词文件是空的")
    if len(data) > 1024 * 1024:
        raise ValueError("歌词文件不能超过 1 MB")
    encodings = ["utf-8-sig"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in data[:100]:
        encodings.append("utf-16")
    encodings.append("gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法识别歌词编码，请保存为 UTF-8 后重试")
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("歌词文件没有可用文字")
    return text + "\n"


def _clean(q: str) -> str:
    """去掉翻唱干扰词，提高匹配率。"""
    q = re.sub(r"\([^)]*\)|（[^）]*）|【[^】]*】|\[[^\]]*\]|「[^」]*」|《[^》]*》", " ", q)
    for w in ("翻唱", "歌ってみた", "cover", "Cover", "COVER", "カバー", "试唱", "試唱",
              "歌枠", "切片", "切り抜き"):
        q = q.replace(w, "")
    return re.sub(r"\s+", " ", q).strip()


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _norm(s: str) -> str:
    s = s.lower().strip()
    # 全角→半角、去标点空格
    s = s.translate(str.maketrans("！？，。・’‘“”（）【】《》", "!?,.·''\"\"()[]<>"))
    return re.sub(r"[\s\-_/·.]+", "", s)


def _lrclib(track: str, artist: str = "", duration: float = 0) -> tuple[str, str, str] | None:
    """返回 (纯文本歌词, 时间轴歌词, 来源) 或 None。"""
    params = {"track_name": track}
    if artist:
        params["artist_name"] = artist
    if duration:
        params["duration"] = int(round(duration))
    try:
        with httpx.Client(timeout=15) as c:
            _throttle()
            r = c.get(LRCLIB_SEARCH, params=params, headers={"User-Agent": UA})
            if r.status_code != 200:
                return None
            rows = r.json()
            if not rows:
                return None
            rows = [x for x in rows if not x.get("instrumental")] or rows
            best = rows[0]
            best_score = _fuzzy(track, best.get("trackName") or "")
            for row in rows[1:]:
                score = _fuzzy(track, row.get("trackName") or "")
                if row.get("syncedLyrics") and score >= best_score:
                    best, best_score = row, score
            if best_score < 0.6:
                return None
            return (
                best.get("plainLyrics") or "",
                best.get("syncedLyrics") or best.get("plainLyrics") or "",
                "lrclib",
            )
    except Exception:
        return None


def _netease(track: str, artist: str = "") -> tuple[str, str, str] | None:
    """网易云搜索 → 歌词（含翻译）。"""
    headers = {"User-Agent": UA, "Referer": "https://music.163.com/"}
    try:
        with httpx.Client(timeout=15, headers=headers) as c:
            q = f"{track} {artist}".strip()
            _throttle()
            r = c.get(NETEASE_SEARCH, params={"s": q, "type": 1, "offset": 0, "limit": 5})
            data = r.json()
            songs = (data.get("result") or {}).get("songs") or []
            if not songs:
                return None
            best_song = songs[0]
            best_score = _fuzzy(track, best_song.get("name") or "")
            for s in songs[1:]:
                score = _fuzzy(track, s.get("name") or "")
                if score > best_score:
                    best_song, best_score = s, score
            if best_score < 0.6:
                return None
            _throttle()
            r2 = c.get(NETEASE_LYRIC, params={"id": best_song["id"], "lv": 1, "kv": 1, "tv": -1})
            ld = r2.json()
            lrc = (ld.get("lrc") or {}).get("lyric") or ""
            tlrc = (ld.get("tlyric") or {}).get("lyric") or ""
            if not lrc:
                return None
            return lrc, lrc, "netease"
    except Exception:
        return None


def _qq(track: str, artist: str = "") -> tuple[str, str, str] | None:
    """QQ 音乐搜索 → 歌词（需要 y.qq.com Referer）。"""
    headers = {"User-Agent": UA, "Referer": "https://y.qq.com/"}
    try:
        with httpx.Client(timeout=15, headers=headers) as c:
            q = f"{track} {artist}".strip()
            _throttle()
            r = c.get(QQ_SEARCH, params={"w": q, "format": "json", "p": 1, "n": 5})
            data = r.json()
            songs = (data.get("data") or {}).get("song", {}).get("list") or []
            if not songs:
                return None
            best_song = songs[0]
            best_score = _fuzzy(track, best_song.get("songname") or "")
            for s in songs[1:]:
                score = _fuzzy(track, s.get("songname") or "")
                if score > best_score:
                    best_song, best_score = s, score
            if best_score < 0.6:
                return None
            _throttle()
            r2 = c.get(QQ_LYRIC, params={"songmid": best_song["songmid"], "format": "json", "nobase64": 1})
            ld = r2.json()
            if ld.get("code") != 0:
                return None
            lyric = ld.get("lyric") or ""
            if not lyric:
                return None
            return lyric, lyric, "qq"
    except Exception:
        return None


def fetch_lyrics(title: str, artist: str = "", duration: float = 0) -> dict:
    """带进程内缓存的歌词查询入口。"""
    return _fetch_lyrics_cached(title, artist, int(round(duration or 0)))


@lru_cache(maxsize=2048)
def _fetch_lyrics_cached(title: str, artist: str, duration: int) -> dict:
    """对外入口，返回 {"plain": str, "lrc": str, "source": str}。"""
    track, art = _clean(title), _clean(artist)
    if not track:
        return {"plain": "", "lrc": "", "source": ""}

    # LRCLIB：带歌手 → 仅歌名
    for t, a in ((track, art), (track, "")):
        hit = _lrclib(t, a, duration)
        if hit:
            return {"plain": hit[0], "lrc": hit[1], "source": hit[2]}

    # 网易云：带歌手 → 仅歌名
    for t, a in ((track, art), (track, "")):
        hit = _netease(t, a)
        if hit:
            return {"plain": hit[0], "lrc": hit[1], "source": hit[2]}

    # QQ 音乐：带歌手 → 仅歌名
    for t, a in ((track, art), (track, "")):
        hit = _qq(t, a)
        if hit:
            return {"plain": hit[0], "lrc": hit[1], "source": hit[2]}

    return {"plain": "", "lrc": "", "source": ""}
