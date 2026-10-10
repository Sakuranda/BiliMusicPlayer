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
from collections import OrderedDict
from difflib import SequenceMatcher

import httpx

from .config import LYRICS_API_INTERVAL

UA = "MusicPlayer/1.0 (https://github.com/Sakuranda/BiliMusicPlayer)"

LRCLIB_SEARCH = "https://lrclib.net/api/search"
NETEASE_SEARCH = "https://music.163.com/api/search/get/web"
NETEASE_CLOUDSEARCH = "https://music.163.com/api/cloudsearch/pc"
NETEASE_LYRIC = "https://music.163.com/api/song/lyric"
QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
QQ_LYRIC = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

_rate_lock = threading.Lock()
_last_request = 0.0
_cache_lock = threading.Lock()
_lyrics_cache: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_CACHE_LIMIT = 2048
_SUCCESS_TTL = 24 * 60 * 60
_MISS_TTL = 5 * 60


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
    # 书名号常包裹真正的歌名，不能把其中的文字一起删除。
    q = q.translate(str.maketrans("《》「」『』〖〗", "        "))
    q = re.sub(r"\([^)]*\)|（[^）]*）|【[^】]*】|\[[^\]]*\]", " ", q)
    q = re.sub(r"\bcover\b", "", q, flags=re.IGNORECASE)
    for w in ("翻唱", "歌ってみた", "カバー", "试唱", "試唱",
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
    try:
        with httpx.Client(timeout=15) as c:
            _throttle()
            r = c.get(LRCLIB_SEARCH, params=params, headers={"User-Agent": UA})
            if r.status_code != 200:
                return None
            rows = r.json()
            if not isinstance(rows, list):
                return None
            rows = [x for x in rows if isinstance(x, dict) and not x.get("instrumental")
                    and _fuzzy(track, x.get("trackName") or "") >= 0.6
                    and lrc_to_plain(x.get("syncedLyrics") or x.get("plainLyrics") or "")]
            if not rows:
                return None
            # 优先同名，再比较歌手和时间轴；翻唱时长不能作为硬过滤条件。
            best = max(rows, key=lambda x: (
                _fuzzy(track, x.get("trackName") or ""),
                _fuzzy(artist, x.get("artistName") or "") if artist else 0,
                bool(x.get("syncedLyrics")),
                -abs(float(x.get("duration") or 0) - duration) if duration else 0,
            ))
            return (
                best.get("plainLyrics") or lrc_to_plain(best.get("syncedLyrics") or ""),
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
            params = {"s": q, "type": 1, "offset": 0, "limit": 5}
            try:
                r = c.get(NETEASE_SEARCH, params=params)
                data = r.json() if r.status_code == 200 else {}
            except (httpx.HTTPError, ValueError):
                data = {}
            # 海外接口可能 code=200、abroad=true，但 result 是受限字符串。
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, dict):
                _throttle()
                r = c.post(NETEASE_CLOUDSEARCH, data=params)
                if r.status_code != 200:
                    return None
                data = r.json()
                result = data.get("result") if isinstance(data, dict) else None
                if not isinstance(result, dict):
                    return None
            songs = result.get("songs") or []
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
            if r2.status_code != 200:
                return None
            ld = r2.json()
            lrc = (ld.get("lrc") or {}).get("lyric") or ""
            if not lrc_to_plain(lrc):
                return None
            return lrc_to_plain(lrc), lrc, "netease"
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
            if not lrc_to_plain(lyric):
                return None
            return lrc_to_plain(lyric), lyric, "qq"
    except Exception:
        return None


def fetch_lyrics(title: str, artist: str = "", duration: float = 0, *, force: bool = False) -> dict:
    """成功缓存一天，未匹配缓存五分钟；手动重新匹配可强制刷新。"""
    key = (title, artist, int(round(duration or 0)))
    with _cache_lock:
        cached = _lyrics_cache.get(key)
        if cached and not force and cached[0] > time.monotonic():
            _lyrics_cache.move_to_end(key)
            return dict(cached[1])
        _lyrics_cache.pop(key, None)
    result = _fetch_lyrics_uncached(*key)
    ttl = _SUCCESS_TTL if result.get("source") else _MISS_TTL
    with _cache_lock:
        _lyrics_cache[key] = (time.monotonic() + ttl, dict(result))
        _lyrics_cache.move_to_end(key)
        while len(_lyrics_cache) > _CACHE_LIMIT:
            _lyrics_cache.popitem(last=False)
    return result


def _fetch_lyrics_uncached(title: str, artist: str, duration: int) -> dict:
    """对外入口，返回 {"plain": str, "lrc": str, "source": str}。"""
    track, art = _clean(title), _clean(artist)
    if not track:
        return {"plain": "", "lrc": "", "source": ""}

    # 多P标题常带“1 歌名”或“P2. 歌名”；仅检索时尝试去序号，保留原名优先。
    unnumbered = re.sub(r"^(?:[pP]\s*)?\d{1,3}(?:\s*[.、:：-]\s*|\s+)(?=\D)", "", track).strip()
    tracks = list(dict.fromkeys((track, unnumbered)))
    for provider in (_lrclib, _netease, _qq):
        for t in tracks:
            if not t:
                continue
            for a in dict.fromkeys((art, "")):
                hit = provider(t, a, duration) if provider is _lrclib else provider(t, a)
                if hit and (hit[0].strip() or lrc_to_plain(hit[1])):
                    return {"plain": hit[0], "lrc": hit[1], "source": hit[2]}

    return {"plain": "", "lrc": "", "source": ""}
