"""Conservative metadata extraction for Bilibili music clips.

The uploader is a credit, not evidence of who sang. Explicit credits, title
structure and exact matches against trusted library artists provide evidence;
ambiguous tags remain suggestions that the import preview asks users to review.
Confidence scores describe heuristic signal strength, not calibrated probability.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# These are whole labels only: removing substrings corrupts titles like 烟花/空心.
NOISE_TAGS = {
    "翻唱", "翻唱版", "歌ってみた", "试唱", "歌枠", "歌回", "cover", "カバー",
    "音乐", "音楽", "music", "mv", "mmd", "切り抜き", "切片", "歌切", "录播",
    "直播", "直播回放", "完整版", "高音质", "无损", "原创", "初投稿", "投稿",
    "新作", "4k", "1080p", "60fps", "音源", "作业用", "bgm", "无修音", "修音",
    "精修", "生唱", "自弹自唱", "弹唱", "生放送", "配信", "唱见", "vup", "vtb",
    "vtuber", "虚拟主播", "虚拟歌手", "虚拟偶像", "虚拟up主", "歌势", "live",
    "official", "耐久", "自我介绍", "杂谈", "女声", "男声", "女声翻唱", "中文翻唱",
    "日文翻唱", "日语翻唱", "双女声", "女声合唱", "合唱", "治愈", "温柔", "青春",
    "可爱", "美女", "情歌", "纯爱", "甜歌", "氛围", "emo", "唱歌", "听歌", "二次元",
    "演奏", "重制", "爵士", "爵士乐", "打卡挑战", "百日挑战", "音乐分享官",
    "翻唱总动员", "竖屏音乐会", "虚拟最强音", "全能音乐挑战赛", "国语", "日语",
    "日文", "中文", "英语", "英文", "hires", "生歌枠", "翻唱的n种打开方式",
}
# Weak tags may refer to the original creator. Explicit performance credits always
# override this hint; this list is not a ban on these musicians performing.
ORIGINAL_ARTISTS = {
    "aimer", "陈奕迅", "郭顶", "周杰伦", "蔡健雅", "米津玄師", "米津玄师",
    "徐佳莹", "许嵩", "告五人", "任然", "葛东琪", "江语晨", "邹佩佩", "deca joins",
    "宇多田光", "宇多田ヒカル", "新裤子", "房东的猫", "汪苏泷", "薛之谦", "林俊杰",
    "王菲", "邓紫棋", "张杰", "李荣浩", "毛不易", "陈粒", "yoasobi", "ヨルシカ",
    "ado", "eve", "vaundy", "藤井风", "藤井風", "優里", "yama", "milet", "ikura",
    "幾田りら", "あいみょん", "zutomayo", "radwimps", "one ok rock", "king gnu",
    "official髭男dism", "泽野弘之", "梶浦由记", "vocaloid", "初音ミク", "初音未来",
    "gumi", "flower", "可不", "重音テト",
}
BOOK_RE = re.compile(r"[《「『〖]([^》」』〗\n]{1,100})[》」』〗]")
PERSON_BRACKET_RE = re.compile(r"[【\[〔]([^】\]〕\n]{1,80})[】\]〕]")
PAREN_RE = re.compile(r"[（(]([^）)\n]{1,100})[）)]")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_CAMPAIGN_RE = re.compile(r"^(?:day\s*\d+|\d+\s*天)|挑战|连唱|合集", re.I)
_CLIP_RE = re.compile(r"切片|歌切|录播|搬运|存档|字幕组|非官方|clips?|archive|fans?", re.I)
_ORIGINAL_ROLE = r"原唱|原曲|本家|作词|作曲|词曲|编曲|original(?:\s+artist)?|composer|lyricist"
_ARTIST_ROLE = r"演唱者|翻唱者|演唱|翻唱|歌手|歌い手|vocal(?:s)?|singer|cover\s+by|covered\s+by"
_SONG_ROLE = r"歌名|曲名|歌曲|曲目|song(?:\s+title)?|title"
_ALL_ROLES = rf"{_ORIGINAL_ROLE}|{_ARTIST_ROLE}|{_SONG_ROLE}"
_ROLE_RE = re.compile(
    rf"(?P<role>{_ALL_ROLES})\s*[:：]\s*"
    rf"(?P<value>[^\n\r;；|｜丨【】《》〖〗]+?)(?=[\s,，]+(?:{_ALL_ROLES})\s*[:：]|[\n\r;；|｜丨【】《》〖〗]|$)", re.I,
)
_BY_RE = re.compile(r"(?:\bcover(?:ed)?\s+by|\bby)\s+([^\n\r|｜丨【】《》〖〗()（）]+)", re.I)
_COLLAB_RE = re.compile(r"\s*(?:/|、|＆|&|×|(?<=[\u3400-\u9fff])x(?=[\u3400-\u9fff])|\s+[xX]\s+)\s*")
_SPACE_RE = re.compile(r"\s+")
_PRODUCTION_LABEL_RE = re.compile(r"(?:二次元|原创|说唱|爵士|重制|双女声?|女声|男声|合唱|翻唱|BE|RAP|版|[\s|｜丨/\\·、-])+", re.I)
_PROMO_RE = re.compile(r"高能|现场|好听|惊艳|神级|超甜|开口|天籁|震撼|收藏|点赞|神仙|破防|绝了|太.{0,5}了")


def _clean(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip(" \t\r\n-–—/|｜丨:：，,;；")


def _key(value: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _noise(value: str) -> bool:
    return _key(value) in NOISE_TAGS or bool(_CAMPAIGN_RE.search(value)) or bool(_PRODUCTION_LABEL_RE.fullmatch(value) and re.search(r"[A-Za-z\u3400-\u9fff]", value))


def _clean_body(value: str) -> str:
    # Remove separated production labels, never parts of actual song words.
    parts = re.split(r"(\s+)", value)
    return _clean("".join("" if _noise(p) else p for p in parts))


def _sentence(value: str) -> bool:
    if re.search(r"[“”\"…]|\.\.\.|[，。！？]", value):
        return True
    if not _CJK_RE.search(value):
        return len(value.split()) > 12
    particles = sum(value.count(p) for p in ("我", "你", "的", "了", "是", "着", "我们", "永远"))
    return bool(_PROMO_RE.search(value)) or len(value) > 24 or (len(value) >= 8 and particles >= 2)


def _person(value: str) -> bool:
    return bool(
        value and 1 < len(value) <= 40 and not _noise(value)
        and not re.search(r"[。！？!?“”《》〖〗|｜丨\\]|https?://|\d{6,}", value)
        and re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", value)
        and len(value.split()) <= 5
    )


def _members(value: str) -> list[str]:
    return [x for x in (_clean(p) for p in _COLLAB_RE.split(value)) if x]


def _artist_index(known_artists: Iterable[str] | Mapping[str, str] | None) -> dict[str, str]:
    index: dict[str, str] = {}
    if isinstance(known_artists, Mapping):
        pairs = known_artists.items()
    else:
        values = [known_artists] if isinstance(known_artists, str) else (known_artists or [])
        pairs = ((artist, artist) for artist in values)
    for alias, canonical in pairs:
        if not isinstance(alias, str) or not isinstance(canonical, str):
            continue
        if not _person(canonical) or canonical == "未知歌手":
            continue
        index[_key(alias)] = canonical.strip()
        index.setdefault(_key(canonical), canonical.strip())
    return index


def _canonical_artist(value: str, index: Mapping[str, str]) -> str:
    value = _clean(value).strip("@")
    if _key(value) in index:
        return index[_key(value)]
    return " / ".join(dict.fromkeys(index.get(_key(m), m) for m in _members(value)))


def known_artists_from_songs(songs: Iterable[Mapping]) -> list[str]:
    """Reuse only manually confirmed or strong, unambiguous performer evidence.

    Legacy uploader fallbacks and mere tag guesses must not train future imports.
    A duet teaches its individual performers, never aliases one as the full duet.
    """
    artists: dict[str, str] = {}
    for song in songs:
        meta = song.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (ValueError, TypeError):
                continue
        if not isinstance(meta, Mapping):
            continue
        try:
            confidence = float(meta.get("artist_confidence") or 0)
        except (ValueError, TypeError):
            continue
        source = meta.get("artist_source")
        manual = source == "manual"
        strong = confidence >= 0.9 and source in {
            "title_credit", "description_credit", "tag_credit", "library_tag", "library_title", "title_residual",
        }
        artist = song.get("artist") or meta.get("artist") or ""
        if not (manual or strong) or not isinstance(artist, str):
            continue
        for member in _members(artist):
            if _person(member) and member != "未知歌手":
                artists.setdefault(_key(member), member)
    return list(artists.values())


@dataclass(frozen=True)
class _Candidate:
    value: str
    score: float
    source: str


def _choose(candidates: list[_Candidate], fallback: str, reasons: list[str], label: str) -> _Candidate:
    if label == "歌手":
        groups = [{_key(m) for m in _members(c.value)} for c in candidates if c.score >= 0.8 and len(_members(c.value)) > 1]
        # A tag for one member corroborates a credited duet; it does not replace it.
        candidates = [c for c in candidates if not (c.source == "library_tag" and any({_key(m) for m in _members(c.value)} < group for group in groups))]
    by_value: dict[str, _Candidate] = {}
    for candidate in candidates:
        if not candidate.value:
            continue
        key = _key(candidate.value)
        if key not in by_value or candidate.score > by_value[key].score:
            by_value[key] = candidate
    ranked = sorted(by_value.values(), key=lambda c: (-c.score, _key(c.value)))
    if not ranked:
        return _Candidate(fallback, 0.0 if label == "歌手" else 0.25, "unknown" if label == "歌手" else "raw_title")
    best = ranked[0]
    if len(ranked) > 1 and ranked[1].score >= max(0.8, best.score - 0.15):
        reasons.append(f"{label}存在冲突候选：{best.value}、{ranked[1].value}")
        return _Candidate(best.value, min(best.score, 0.65), best.source)
    return best


def parse_metadata(
    raw_title: str,
    uploader: str = "",
    tags: list[str] | None = None,
    description: str = "",
    known_artists: Iterable[str] | Mapping[str, str] | None = None,
) -> dict:
    """Return title/artist plus source, heuristic confidence and review reasons.

    ``known_artists`` accepts trusted names or an explicit alias→canonical map.
    Matching is exact (case/Unicode/whitespace normalized), not substring or fuzzy
    matching. No network calls or opaque music search guesses occur here.
    """
    title = (raw_title or "").strip()[:1000]
    description = (description or "")[:12000]
    tags = list(dict.fromkeys(t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()))[:100]
    index = _artist_index(known_artists)
    artists: list[_Candidate] = []
    songs: list[_Candidate] = []
    reasons: list[str] = []
    originals: set[str] = set()
    role_values: set[str] = set()
    role_spans: dict[str, list[tuple[int, int]]] = {"title": [], "description": []}

    def add_artist(value: str, score: float, source: str) -> None:
        value = _canonical_artist(value, index)
        if _person(value):
            artists.append(_Candidate(value, score, source))

    for origin, content in [("title", title), ("description", description), *(("tag", t) for t in tags)]:
        for match in _ROLE_RE.finditer(content):
            if any(book.start() < match.start() < book.end() for book in BOOK_RE.finditer(content)):
                continue
            role = match["role"].casefold()
            # A later credit on the same line must not become part of the name.
            value = _clean(match["value"])
            role_values.add(_key(value))
            if origin in role_spans:
                role_spans[origin].append(match.span())
            if re.fullmatch(_ORIGINAL_ROLE, role, re.I):
                originals.update(_key(v) for v in _members(value))
            elif re.fullmatch(_SONG_ROLE, role, re.I):
                if value:
                    songs.append(_Candidate(value, 0.97 if origin == "title" else 0.93, f"{origin}_credit"))
            else:
                add_artist(value, 0.98 if origin == "title" else 0.96, f"{origin}_credit")
    # ``by`` in a title credits a performance; in prose it could credit an editor.
    for match in _BY_RE.finditer(title):
        if any(book.start() < match.start() < book.end() for book in BOOK_RE.finditer(title)):
            continue
        # Bare "by" can be a song word (Stand by Me). Require a separated credit.
        if not re.match(r"cover", match[0], re.I) and not BOOK_RE.search(title) and not re.search(r"[-—|｜丨]\s*$", title[:match.start()]):
            continue
        add_artist(match[1], 0.95, "title_credit")
        role_spans["title"].append(match.span())

    books = list(dict.fromkeys(_clean(x) for x in BOOK_RE.findall(title) if _clean(x) and not _CAMPAIGN_RE.search(x)))
    if books:
        if len(books) > 1:
            songs.append(_Candidate(" / ".join(books), 0.7, "title_books"))
            reasons.append("标题包含多首作品，需确认串烧或选择分P")
        else:
            songs.append(_Candidate(books[0], 0.98, "title_book"))
    # Quoted song credits in a description are valid only with a song label.
    for match in re.finditer(rf"(?:{_SONG_ROLE})\s*[:：]\s*[《「『〖]([^》」』〗]+)[》」』〗]", description, re.I):
        songs.append(_Candidate(_clean(match[1]), 0.93, "description_credit"))

    excluded_artist_tags = {_key(t) for t in ORIGINAL_ARTISTS} | originals
    for tag in tags:
        key = _key(tag)
        if key in index and key not in excluded_artist_tags:
            add_artist(index[key], 0.91, "library_tag")

    tag_keys = {_key(t) for t in tags}
    for value in PERSON_BRACKET_RE.findall(title):
        value = _clean(value)
        if _noise(value) or _PROMO_RE.search(value) or _ROLE_RE.search(value) or _key(value) in originals:
            continue
        if value in books:
            continue
        if not books and any(a.score >= 0.9 for a in artists) and _key(value) not in index and value in tags:
            songs.append(_Candidate(value, 0.9, "title_tag"))
            continue
        if _person(value) and not _sentence(value):
            members = _members(value)
            trusted = all(_key(member) in index for member in members)
            corroborated = _key(value) in tag_keys or all(_key(member) in tag_keys for member in members)
            score = 0.94 if trusted else (0.84 if corroborated else 0.7)
            add_artist(value, score, "library_title" if trusted else "title_bracket")

    body = title
    for start, end in sorted(role_spans["title"], reverse=True):
        body = body[:start] + " " + body[end:]
    body = BOOK_RE.sub(" ", body)
    body = PERSON_BRACKET_RE.sub(" ", body)
    body = PAREN_RE.sub(" ", body)
    body = _clean_body(body)
    # An entire residual name, not an arbitrary substring of a lyric sentence.
    if _key(body) in index and _key(body) not in originals:
        add_artist(index[_key(body)], 0.94, "library_title")
        body = ""
    elif books and body and _person(body) and not _sentence(body) and _key(body) not in excluded_artist_tags:
        matching_tag = any(_key(t) == _key(body) for t in tags)
        score = 0.9 if matching_tag else 0.65
        add_artist(body, score, "title_residual")
        body = ""

    artist_keys = {_key(m) for a in artists for m in _members(a.value)}
    # Keep punctuation inside English song titles (e.g. Anti-Hero).
    pieces = [p for p in (_clean_body(p) for p in re.split(r"\s+[-–—]\s+|[/|｜丨]|(?<=[\u3040-\u9fff])[-—]", body)) if p]
    title_piece = body
    structured = False
    if len(pieces) > 1:
        remaining = []
        for part in pieces:
            key = _key(part)
            if key in artist_keys or key in index and key not in originals:
                if key in index:
                    add_artist(index[key], 0.94, "library_title")
            elif key in excluded_artist_tags:
                # Artist - Song outside cover context is a conventional credit.
                if part == pieces[0] and not artists and not re.search(r"翻唱|cover", title, re.I):
                    add_artist(part, 0.82, "title_structure")
            else:
                remaining.append(part)
        if len(remaining) == 1:
            title_piece, structured = remaining[0], True
        elif len(remaining) == 2 and not books:
            left, right = remaining
            if _CJK_RE.search(left) and len(left) <= 6 and re.fullmatch(r"[A-Za-z]+(?:[ '\-][A-Za-z]+)+", right):
                add_artist(left, 0.78, "title_structure")
                title_piece, structured = right, True
            elif _CJK_RE.search(right) and re.search(r"[A-Za-z]", right) and not _sentence(right):
                add_artist(right, 0.78, "title_structure")
                title_piece, structured = left, True

    artist_keys = {_key(m) for a in artists for m in _members(a.value)}
    useful_tags = [t for t in tags if not _noise(t) and not _ROLE_RE.search(t)
                   and _key(t) not in artist_keys | excluded_artist_tags | role_values
                   and _key(t) != _key(uploader) and len(t) <= 80]
    title_matches = [t for t in useful_tags if _key(t) == _key(title_piece)]
    if title_matches:
        songs.append(_Candidate(title_matches[0], 0.91, "title_tag"))
    elif not songs and title_piece and not _sentence(title_piece) and _key(title_piece) not in artist_keys:
        songs.append(_Candidate(title_piece, 0.84 if structured else 0.8, "title_structure" if structured else "title_body"))
    elif not songs and title_piece:
        phrases = re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)+", title_piece)
        if phrases:
            songs.append(_Candidate(max(phrases, key=len), 0.72, "title_phrase"))
    if not songs or max(c.score for c in songs) < 0.8:
        if len(useful_tags) == 1:
            score = 0.84 if any(a.score >= 0.9 for a in artists) else 0.78
            songs.append(_Candidate(useful_tags[0], score, "tag_candidate"))
        elif len(useful_tags) > 1:
            reasons.append("多个标签可能是歌名或歌手，未按标签长度盲选")

    song = _choose(songs, title or "未知歌曲", reasons, "歌名")
    if song.source == "title_body":
        # A long uncredited Chinese phrase may be a lyric. Preserve it as a
        # reviewable candidate; do not replace it with an arbitrary genre tag.
        # Book titles, exact title/tag matches and explicit credits bypass this.
        han_count = len(re.findall(r"[\u3400-\u9fff]", song.value))
        clauses = re.split(r"[\s,，;；]+", song.value)
        multi_clause = sum(len(re.findall(r"[\u3400-\u9fff]", part)) >= 3 for part in clauses) >= 2
        lyrical_body = han_count >= 18 or (han_count >= 10 and multi_clause) or (han_count >= 8 and bool(re.search(r"[~～〜]\s*$", song.value)))
        if lyrical_body:
            song = _Candidate(song.value, min(song.score, 0.65), song.source)
            reasons.append("中文正文可能是歌词或宣传句，缺少独立歌名依据")
    # Tags unrecognized by the trusted library remain low confidence suggestions.
    if not artists:
        remaining = [t for t in useful_tags if _key(t) != _key(song.value) and _person(t)]
        if len(remaining) == 1 and song.score >= 0.8:
            add_artist(remaining[0], 0.58, "tag_candidate")
    # An original-only tag is never evidence of the performance in a cover clip.
    artist = _choose(artists, "未知歌手", reasons, "歌手")
    if artist.value == "未知歌手":
        reasons.append("缺少可靠演唱者信息，上传账号未作为歌手")
    if song.score < 0.8:
        reasons.append("歌名证据不足，需核对标题、标签或分P")
    if artist.score < 0.8 and artist.value != "未知歌手":
        reasons.append("演唱者来自弱信号，需人工确认")
    return {
        "title": song.value, "artist": artist.value,
        "title_source": song.source, "artist_source": artist.source,
        "title_confidence": song.score, "artist_confidence": artist.score,
        "needs_review": bool(reasons) or min(song.score, artist.score) < 0.8,
        "reasons": list(dict.fromkeys(reasons)), "parser_version": 3,
    }


def parse_title(
    raw_title: str, uploader: str = "", tags: list[str] | None = None,
    description: str = "", known_artists: Iterable[str] | Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Compatibility wrapper for callers that only need (title, artist)."""
    result = parse_metadata(raw_title, uploader, tags, description, known_artists)
    return result["title"], result["artist"]
