# Bilibili 收藏夹 → 音频 → 歌词 Pipeline: Research Report (verified live, 2025)

> **Method note.** Every endpoint below was *live-tested from a residential IP on 2026-08-14* (curl + Python + a real yt-dlp 2026.07.04 run). Where docs and live behavior differ, both are stated. The canonical docs project [SocialSisterYi/bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) was **shut down on 2026-01-28 after a legal notice from Bilibili**, so I used the [GitHub Pages mirror](https://wuziqian211.github.io/bilibili-API-collect/) and the [gitea mirror](https://gitea.s1f.ren/shiran/bilibili-API-collect) of its docs, plus [DeepWiki of a fork](https://deepwiki.com/afiuh/bilibili-api-collect/).

---

## 0. Executive summary (the verdicts you asked for)

| Question | Verdict (live-verified 2025) |
|---|---|
| Can public fav lists be fetched without cookies? | **Yes.** `GET https://api.bilibili.com/x/v3/fav/resource/list` works with **no cookies, no WBI, no login** — browser UA + `Referer: https://www.bilibili.com/` is enough. Even bare curl (no headers) worked from our IP. |
| Private lists | Require login. Add cookie `SESSDATA=...` (see §1.5). Without it you get `code: -403 "访问权限不足"` (or `data: null`). |
| 192k audio without login? | **Yes in our tests** (2 different videos): DASH `id=30280` (192k AAC m4a) returned and downloadable with only Referer+UA. Note: Bilibili's max standard audio is **192k — there is no 320k** for UGC. VIP-only extras: Dolby (30250), Hi-Res lossless (30251). |
| yt-dlp for bilibili in 2025? | **Yes — it works and is the recommended path.** Verified end-to-end: `yt-dlp -f ba` downloaded 30280 m4a without cookies, no ffmpeg needed (already m4a). Current master uses WBI-signed `/x/player/wbi/playurl` and sets a fake `buvid3` cookie itself. |
| Referer mandatory? | **For CDN media downloads: yes.** Same audio URL → 206 with Referer, **403 without Referer** (verified). For api.bilibili.com JSON endpoints: Referer strongly recommended, not strictly required. |
| 412 anti-crawl | Not triggered in our bursts (25 rapid API calls, 200s). 412 is triggered by: flagged/datacenter IPs, missing/stale `buvid3` fingerprint cookie (mandatory since 2022-08-24 for WBI endpoints), or sustained rate abuse. Mitigations in §4. |
| WBI signing | **Required only for the `wbi/` endpoint variants.** The old non-WBI endpoints (`/x/v3/fav/resource/list`, `/x/web-interface/view`, `/x/player/playurl`) still work unsigned (verified). But treat WBI as the "front-end of the future" and implement it (§4.1) if you want to be robust. |
| Lyrics for JP/CN songs | **LRCLIB (verified, JP+CN OK)** → **NetEase (verified, best JP/CN catalog incl. covers)** → **QQ Music (verified, needs y.qq.com Referer)**. For VTuber covers: none is reliable → fuzzy fallback strategy in §3.4. |

---

## 1. Bilibili favorite list (收藏夹) API

### 1.1 Endpoint: get favorite-list contents

```
GET https://api.bilibili.com/x/v3/fav/resource/list
```

**Parameters** (all query-string; `GET`):

| Param | Type | Required | Meaning |
|---|---|---|---|
| `media_id` | int | **yes** | The 收藏夹 mlid (the *full* id, see §1.3) |
| `pn` | int | no (default 1) | Page number |
| `ps` | int | no (default 20) | Page size — docs say domain 1–20; **live test: ps=21 returned 21 items**, so it's not hard-capped, but stay ≤20 |
| `platform` | str | no | `web` (recommended; affects list item type) — live test: works without it too |
| `tid` | int | no | Filter by partition tid (0 = all) |
| `keyword` | str | no | Search within the list |
| `order` | str | no | `mtime` (by fav time) / `view` (by plays) / `pubtime` (by upload) |
| `type` | int | no | `0` = this folder only (default) / `1` = all folders of the user |

**Headers:** `User-Agent` (browser string) + `Referer: https://www.bilibili.com/` (cookies only for private lists).

**Response JSON field paths** (root `{code, message, ttl, data}`):

```
data.info                     — folder metadata
  .id          = media_id (mlid)
  .fid         = folder raw id
  .mid         = creator uid
  .title       = folder name
  .media_count = number of items
  .upper.name  = creator nickname
data.medias[]                 — the videos (array)
  .bvid / .bv_id   = BV id            ← use this for view/playurl
  .id              = avid
  .type            = 2 video, 12 audio, 21 video-collection (filter type==2)
  .title           = video title      ← song-name parsing source #1
  .cover           = cover URL
  .intro           = description
  .duration        = seconds
  .upper.name      = uploader nickname
  .upper.mid       = uploader uid
  .ctime / .pubtime / .fav_time = timestamps (unix seconds)
  .page            = number of parts (分P)
  .attr            = 0 ok / 9 up deleted / 1 removed
data.has_more   — bool, false on last page
data            — **null when page is out of range** (verified: pn=99 → data:null). Stop paginating then.
```

> **No tags in this response.** Video tags are *not* included here; you must call the tag API (§1.6) per video.

**Example (Python):**

```python
import requests
S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
})

def fetch_fav(media_id: int):
    videos = []
    pn = 1
    while True:
        r = S.get("https://api.bilibili.com/x/v3/fav/resource/list",
                  params={"media_id": media_id, "pn": pn, "ps": 20, "platform": "web"})
        d = r.json()
        if d.get("code") != 0 or not d.get("data"):
            break                      # -403 private, or past the last page
        for m in d["data"]["medias"]:
            if m.get("type") == 2:     # video only
                videos.append({
                    "bvid": m["bvid"], "avid": m["id"], "title": m["title"],
                    "duration": m["duration"], "cover": m["cover"],
                    "uploader": m["upper"]["name"], "uploader_mid": m["upper"]["mid"],
                    "fav_time": m.get("fav_time"), "ctime": m.get("ctime"),
                })
        if not d["data"]["has_more"]:
            break
        pn += 1
        time.sleep(0.5)                # be gentle, see §4.3
    return videos
```

### 1.2 Folder metadata (optional pre-flight)

```
GET https://api.bilibili.com/x/v3/fav/folder/info?media_id={media_id}
```
Returns the `info` object from above. `code: 11010` = folder doesn't exist, `-403` = private/no permission.

```
GET https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={mid}
```
Lists all folders a user created (`data.list[].{id,fid,title,media_count,attr}`). Needed for the "default favlist" case (§1.3) and to resolve `fid → media_id`.

### 1.3 Parsing favorite URLs → `media_id` (mlid)

The mlid (media_id) is a composite: **`fid` + last 2 digits of creator's `mid`**. Verified: 籽岷 `mid=686127`, `fid=10526220` → `media_id = 1052622027` (matches the live API response and the docs).

**URL form 1 — space page:**
```
https://space.bilibili.com/{mid}/favlist?fid={fid}
```
```python
import re
url = "https://space.bilibili.com/686127/favlist?fid=10526220"
mid = int(re.search(r"space\.bilibili\.com/(\d+)", url).group(1))
fid = int(re.search(r"[?&]fid=(\d+)", url).group(1))
media_id = int(f"{fid}{str(mid)[-2:]}")     # 1052622027
```

**URL form 2 — medialist detail page:**
```
https://www.bilibili.com/medialist/detail/ml{media_id}
```
```python
media_id = int(re.search(r"medialist/detail/ml(\d+)", url).group(1))   # direct, no math
```

**URL form 3 — no `fid` in the space URL (default 收藏夹):** call `folder/created/list-all?up_mid={mid}` and pick the folder whose title is 默认收藏夹 (or the one you want), then apply the `fid+mid[-2:]` formula.

> The web front-end's gateway API (`/x/v3/medialist/gateway/base/detail?mlist_id=...`) **returned an HTML page in our live test** — do not rely on it; use `fav/resource/list` directly.

### 1.4 Private vs public lists

- **Public:** fully readable with zero auth (verified). Note that "public" includes most people's default 收藏夹.
- **Private (`attr` bit set):** requires a logged-in cookie. The minimal cookie is **`SESSDATA`** (the session cookie). In practice the browser also sends `buvid3`/`buvid4` (fingerprint) and `DedeUserID`, but for GET read APIs `SESSDATA` is what authorizes access. Without it: `{"code": -403, "message": "访问权限不足"}`.
- Where to get SESSDATA: from the user's browser cookies (user pastes it, or you use `--cookies-from-browser` in yt-dlp / export a Netscape cookies.txt).

### 1.5 Video detail API (bvid → metadata + cid)

```
GET https://api.bilibili.com/x/web-interface/view?bvid={bvid}
# or: ...?aid={avid}
```
- **Live-verified: works without cookies, without WBI, with UA+Referer.** (Docs list the WBI variant `/x/web-interface/wbi/view` as the "new" endpoint; the old one still works.)
- Response `data` field paths you need:

```
data.bvid, data.aid, data.cid     # cid is REQUIRED for playurl
data.title, data.desc             # title = song-name parsing source #2
data.pic                          # cover
data.duration                     # total seconds (all parts)
data.owner.mid / data.owner.name  # uploader
data.pubdate / data.ctime         # unix seconds
data.tname / data.tid             # partition name/id (音乐 partition = 3)
data.pages[]                      # per-part {cid, page, part, duration}
data.staff[]                      # featured artists/co-singers (some covers credit the original artist here)
data.stat.view / .like / .danmaku # counts
```
Error codes: `-400` bad request, `-404` video gone, `62002` 稿件不可见, `62012` only-UP-visible.

### 1.6 Video tag API (song-name parsing source #3)

**Old (verified working, no auth):**
```
GET https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}
# data[] = [{tag_id, tag_name, cover, ...}]
```

**New (docs; also returns BGM tags):**
```
GET https://api.bilibili.com/x/web-interface/view/detail/tag?bvid={bvid}&cid={cid}
# data[] = [{tag_id, tag_name, tag_type: "old_channel"|"topic"|"bgm",
#            music_id: "MA..." (only bgm), jump_url}]
```
The `bgm`-type tag is gold for this project: for videos where Bilibili's music recognition ran, it gives the **exact Bilibili Music track id** (`MA...`), which you can map to artist/title via the music detail page URL in `jump_url`. The old tag API gives plain tags like `翻唱`/`歌ってみた`/`虚拟主播`.

---

## 2. Audio extraction on a headless Linux server

### 2.1 Recommended: yt-dlp (verified end-to-end)

yt-dlp **fully supports Bilibili in 2025/2026** (extractor actively maintained; see the [412 fix PR #16889](https://github.com/yt-dlp/yt-dlp/pull/16889) and [issue #12013](https://github.com/yt-dlp/yt-dlp/issues/12013)). Current master:
- Calls WBI-signed `https://api.bilibili.com/x/player/wbi/playurl` with `fnval=4048` (all DASH formats),
- Self-sets a `buvid3` fingerprint cookie (`{uuid}infoc`) when absent — this is why it works without browser cookies,
- Sends anti-bot `dm_*` params.

**Audio-only extraction (m4a, no conversion → no ffmpeg needed):**
```bash
yt-dlp -f ba -o "%(title)s.%(ext)s" "https://www.bilibili.com/video/BV1GJ411x7h7"
# -f ba  = best audio-only format; for bilibili that's the 30280 192k m4a
```
Verified output: `test_BV1GJ411x7h7.m4a` (5.16 MiB, `ISO Media, MP4 Base Media v5`) downloaded at ~5 MB/s **without any cookies**.

**If you want mp3 (needs ffmpeg):**
```bash
yt-dlp -f ba -x --audio-format mp3 --audio-quality 0 -o "%(title)s.%(ext)s" "URL"
```

**Batch of many videos (fav-list use case):**
```bash
yt-dlp -f ba --no-playlist \
  --sleep-requests 1 --sleep-interval 3 --max-sleep-interval 8 \
  --retries 5 --fragment-retries 5 \
  -a urls.txt -o "audio/%(uploader)s/%(title)s.%(ext)s"
```
(`-a urls.txt` = one bvid URL per line. `--sleep-requests` throttles API calls; the download itself is CDN and fast.)

**If 192k/1080p+ is missing** (you'll see `Format(s) 1080P 高码率 are missing` — that warning is about *video*; audio 30280 was still returned in our no-cookie test): add real cookies for a logged-in account:
```bash
yt-dlp --cookies cookies.txt -f ba URL          # Netscape cookies.txt
# or:  --cookies-from-browser firefox
```
Cookies also make the 720P+/1080P+ *video* formats available and reduce the chance of being throttled.

**Common pitfalls (from the [yt-dlp 412 issue #12013](https://github.com/yt-dlp/yt-dlp/issues/12013) and PR #16889):**
- **HTTP 412 Precondition Failed** on the *webpage* download: caused by missing/stale `buvid3`/`buvid4` fingerprint cookies. Current yt-dlp auto-sets a fake one; the open PR #16889 fetches real ones from `/x/frontend/finger/spi` (verified live: returns `b_3`/`b_4` without login).
- **Rate-limit block:** sustained parallel fetching can get the *IP* blocked for minutes-to-hours with 412. Key fact from #12013: **when blocked, individual BV-id downloads still work; only page/listing parsing breaks** — so list-fetching (our fav API) and downloads should be separated and throttled.
- Video downloads of VIP-only qualities (4K/HDR/120fps) need a premium account cookie; audio-only does not (max 192k).

### 2.2 Direct DASH via playurl API (no yt-dlp)

**Old endpoint (live-verified: works without WBI + without cookies):**
```
GET https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16&fourk=1
```
**New endpoint (docs; needs WBI signing):**
```
GET https://api.bilibili.com/x/player/wbi/playurl?bvid={bvid}&cid={cid}&fnval=16&fourk=1&w_rid=...&wts=...
```

Params:

| Param | Value | Meaning |
|---|---|---|
| `bvid` or `avid` | — | video id (either) |
| `cid` | int | **required** — get from `/x/web-interface/view` |
| `fnval` | `16` | DASH format; `4048` = all DASH (16\|64\|128\|256\|512\|1024\|2048) |
| `qn` | `80` (1080p) etc. | ignored under DASH (DASH returns every quality) |
| `fourk` | `1` | allow 4K |
| `platform` | `pc` | web playback; **implies Referer 防盗链 check on media URLs** |
| `fnver` | `0` | version, always 0 |

**Response path for audio:**
```
data.dash.audio[]   = array of audio streams
  .id        = 30216 (64k) | 30232 (132k) | 30280 (192k) | 30250 (Dolby, VIP) | 30251 (Hi-Res, VIP)
  .baseUrl   = direct m4s URL  (also .base_url, .backupUrl/.backup_url)
  .mimeType  = "audio/mp4"
  .codecs    = "mp4a.40.2" (AAC-LC)
  .bandwidth = bytes/s
data.dash.flac / data.dash.dolby = lossless / atmos (VIP)
```
URLs are valid for **120 minutes** — re-fetch playurl for each video at download time, don't cache URLs.

**Required headers for the media GET** (verified live):
```http
Referer: https://www.bilibili.com/     ← WITHOUT this → HTTP 403 (verified)
User-Agent: Mozilla/5.0 (...) Chrome/...  ← browser UA
Range: bytes=0-          ← recommended for resumable downloads
```
Verified: same audio URL → `206 Partial Content` with Referer, **403 without Referer**.

**Why 192k and no more:** 30280 = 192kbps AAC is the top *standard* audio tier. There is **no 320k** for UGC videos. VIP grants 30250 (Dolby Atmos) and 30251 (Hi-Res lossless).

### 2.3 Rate limiting for dozens/hundreds of videos

Budget per video: 1× `view` (get cid) + 1× `playurl` + 1× CDN audio GET (+ optionally 1× tag). That's ~3 API calls per video → 300 calls for 100 videos. Practical rules:
- **API calls:** ≤ 1–2 req/s with ±0.5s jitter; our burst test (25 calls in a few seconds) drew no 412, but [#12013](https://github.com/yt-dlp/yt-dlp/issues/12013) reports IP blocks under sustained abuse.
- **Media downloads:** CDN (upos-hdslb.com etc.) tolerates parallelism; still cap at 2–4 concurrent with 3 retries + exponential backoff.
- **On HTTP 412 / code -412 / code -352:** sleep 60–300s and retry; if it persists, rotate IP or pause entirely.
- **Separate the two phases:** fetch+parse the whole fav list first (few requests), then queue downloads. If you get blocked mid-download, the parsed list is already safe.

---

## 3. Lyrics sources (no-login HTTP APIs)

### 3.1 LRCLIB — [lrclib.net](https://lrclib.net) ([API docs](https://lrclib.net/docs))

Open crowd-sourced LRC database. **No auth, no rate limit documented, CORS-enabled.** Live-verified.

**Search (fuzzy, all params optional):**
```
GET https://lrclib.net/api/search?track_name={...}&artist_name={...}&album_name={...}&duration={seconds}
```
**Exact-match (get one):**
```
GET https://lrclib.net/api/get?artist_name={...}&track_name={...}&album_name={...}&duration={seconds}
GET https://lrclib.net/api/get/{id}
```
**Response (array for /search; object for /get):**
```json
[{
  "id": 34493031,
  "name": "周杰伦 - 告白气球",
  "trackName": "告白气球",
  "artistName": "周杰伦",
  "albumName": "...",
  "duration": 216.0,
  "instrumental": false,
  "plainLyrics": "...",       // plain text
  "syncedLyrics": "[00:03.26] 告白氣球\n..."   // LRC — what you want
}]
```
Live verification: `track_name=告白气球` → 20 hits; `track_name=夜に駆ける&artist_name=YOASOBI` → hits with synced LRC. **Good JP and CN coverage for mainstream songs; instrumental flag useful for filtering.** `duration` is a float in seconds — pass your video's duration to rank/verify matches.

### 3.2 NetEase Cloud Music (网易云) — best JP/CN catalog, works without login (verified)

Two dead-simple legacy web endpoints (still live, no auth, no WBI-equivalent):

**Search:**
```
GET https://music.163.com/api/search/get/web?s={query}&type=1&offset=0&limit=10
```
```json
{"code": 200, "result": {"songs": [{
  "id": 2716020167,
  "name": "告白气球（Cover）",
  "artists": [{"name": "Xai小爱", "id": ...}],
  "duration": 199000,        // ms
  "album": {"name": "..."}
}]}}
```
(verified: returned `code:200` incl. **cover versions** — useful when searching a VTuber cover title.)

**Lyrics:**
```
GET https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1
```
```json
{"code": 200, "lrc": {"lyric": "[00:00.000] 作词 : ...\n[00:04.000] ..."}, "tlyric": {"lyric": "..."}}
```
Verified: works with **no Referer, no UA** (even default curl UA). `tlyric` = translation (JP→CN) when available. `lyric` is LRC format. NetEase's catalog of Japanese/Chinese songs (including many 翻唱/歌ってみた uploads) is the deepest of the three, which matters for VTuber covers.

> Related: the community project [NeteaseCloudMusicApi](https://github.com/bubdm/NeteaseCloudMusicApi) wraps these and adds search-by-keyword; the raw endpoints above are all you need.

### 3.3 QQ Music — works without login but pickier (verified)

**Search:**
```
GET https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w={query}&format=json&p=1&n=5&cr=1&g_tk=5381
```
```json
{"code": 0, "data": {"song": {"list": [{
  "songmid": "003OUlho2HcRHC",
  "songname": "告白气球",
  "singer": [{"name": "周杰伦"}]
}]}}}
```
(verified: works with plain browser UA, no referer needed for search.)

**Lyrics:**
```
GET https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid={songmid}&format=json&nobase64=1
```
- **Requires `Referer: https://y.qq.com/`** — verified: without it you get `{"retcode":-1310,"code":-1310}`; with it, `code:0` and `lyric` (LRC; when `nobase64` is omitted it's base64-encoded).
- Newer songs / VIP tracks may return an empty lyric without a login cookie (`uin`). Community docs (e.g., [Rain120/qq-music-api](https://deepwiki.com/Rain120/qq-music-api/4-api-endpoints-reference), [Lyricify-Lyrics-Helper](https://deepwiki.com/WXRIW/Lyricify-Lyrics-Helper/6.3-chinese-music-service-apis)) note the login dependency for some content. Use it as a third fallback, not primary.

### 3.4 Which is most reliable for JP/CN songs — and the VTuber-cover reality

Order for a JP/CN cover-song pipeline: **LRCLIB → NetEase → QQ** (LRCLIB is cleanest API; NetEase has the deepest catalog incl. covers; QQ as tiebreaker).

**The hard truth:** VTuber 翻唱/歌ってみた videos are *covers of existing songs*; the titles are like:
- `【歌ってみた】夜に駆ける / 歌い手名`
- `【翻唱】ロキ cover`
- `「God knows...」歌ってみた【cover】`
- `[中文cover] 告白气球 - 虚拟主播xxx`

The *underlying song* IS in the databases; the *cover video* is usually not (unless the singer also uploaded to NetEase). So:

1. **Parse the song identity from the Bilibili title** before querying lyrics:
   - Strip bracket blocks: `【…】`, `「…」`, `[...]` (they hold 歌ってみた/翻唱/cover/虚拟主播 tags).
   - Split on separators ` / `, `-`, `—` and take the part that isn't the singer name (keep the title part).
   - If a `bgm` tag exists from `/x/web-interface/view/detail/tag`, use its `music_id` as ground truth.
2. **Query lyrics by song-name only first** (no artist) — cover artists won't match the original artist, but the title will. LRCLIB `track_name` alone; NetEase `s={title}`; QQ `w={title}`.
3. **Fuzzy match** results: normalize (lowercase, strip punctuation/whitespace/full-width→half-width), then `difflib.SequenceMatcher` (stdlib) or `rapidfuzz`; accept ratio ≥ 0.8. Optionally cross-check `duration` within ±10 s (LRCLIB gives `duration`, NetEase gives ms).
4. **Handle empty gracefully** — expected often for niche covers: return `{found: false, reason: "not_in_db"}`; store the parsed title/artist guess anyway; let the user search manually or contribute lyrics later. Never crash the pipeline on a miss.
5. Prefer **synced LRC** (LRCLIB `syncedLyrics`, NetEase `lrc.lyric`, QQ `lyric`); fall back to plain text; if only translation exists, mark accordingly.

---

## 4. Bilibili API pitfalls 2025 — WBI, 412, rate limits (per-endpoint matrix)

### 4.1 WBI signing

- Introduced March 2023; adopted by "most query-type APIs" over time ([DeepWiki WBI](https://deepwiki.com/afiuh/bilibili-api-collect/2.1-wbi-signature-system)). It adds `w_rid` (MD5) + `wts` (unix seconds) query params.
- **Algorithm (from the docs, Python):**
  1. Get `img_key`/`sub_key` from `GET https://api.bilibili.com/x/web-interface/nav` → `data.wbi_img.img_url/sub_url` filename stems (keys rotate ~daily; cache and refresh).
  2. `mixin_key = permute(img_key+sub_key, MIXIN_KEY_ENC_TAB)[:32]` (the 64-entry table is in the docs/demo below).
  3. `w_rid = md5(urlencode(sorted(params + wts)) + mixin_key)` — **uppercase percent-encoding, `%20` for spaces**, strip `!'()*` from values.
- **Per-endpoint WBI status (live + docs):**

| Endpoint | WBI needed? | Live test result (no cookies, UA+Referer) |
|---|---|---|
| `GET /x/v3/fav/resource/list` | **No** | ✅ `code:0`, full data |
| `GET /x/web-interface/view` | No (docs list `/x/web-interface/wbi/view` as the new one) | ✅ `code:0` |
| `GET /x/tag/archive/tags` | No | ✅ `code:0` |
| `GET /x/player/playurl` (old) | **No** | ✅ `code:0`, DASH incl. 30280 |
| `GET /x/player/wbi/playurl` | **Yes** (docs) | Signed → ✅ DASH; unsigned → degraded (480p mp4/flv, no DASH) |
| `GET /x/web-interface/nav` | No (it *provides* the keys) | ✅ |
| `GET /x/frontend/finger/spi` | No | ✅ returns `b_3`/`b_4` |
| `/x/v3/medialist/gateway/base/detail` | (unreliable) | returned an HTML page — avoid |

**Recommendation:** for a hobby-scale tool, plain requests to the old endpoints work today. For robustness, implement WBI once (it's ~30 lines) and call the `wbi/` variants — that's exactly what [yt-dlp does](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py).

### 4.2 The 412 anti-crawl and fingerprint cookies

- `HTTP 412 Precondition Failed` / `code: -412 "请求被拦截"` comes from: **missing or stale `buvid3` fingerprint cookie** (mandatory for WBI requests since 2022-08-24), **IP-based rate limiting**, or invalid WBI. `-352` = 风控校验失败.
- Fix: set `buvid3` (+`buvid4`) cookies from `GET /x/frontend/finger/spi` (verified: `b_3`, `b_4` without login), send browser UA + Referer, throttle. This is exactly what yt-dlp PR [#16889](https://github.com/yt-dlp/yt-dlp/pull/16889) automates.
- **Our live burst test: 25 rapid fav/playurl calls → all 200.** 412 is not trigger-happy from a clean residential IP; it bites flagged datacenter IPs and abusers.

### 4.3 Plain requests/curl verdict

| Call | Works with plain curl + browser UA + Referer? |
|---|---|
| fav/resource/list | ✅ (even bare curl worked) |
| web-interface/view | ✅ |
| tag/archive/tags | ✅ |
| player/playurl (old) | ✅ |
| DASH media download | ✅ **only with Referer** (403 otherwise) |
| private list / logged-in things | ❌ needs `SESSDATA` cookie |
| wbi/ endpoints | ❌ need `w_rid`/`wts` |

---

## 5. End-to-end pipeline sketch (Python)

```python
import time, re, requests
from difflib import SequenceMatcher

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
})
# optional: S.cookies.set("SESSDATA", "xxxx", domain=".bilibili.com")   # for private fav lists
# optional: fetch fresh fingerprint:
#   spi = S.get("https://api.bilibili.com/x/frontend/finger/spi").json()["data"]
#   S.cookies.set("buvid3", spi["b_3"], domain=".bilibili.com")

def media_id_from_url(url: str) -> int:
    if m := re.search(r"medialist/detail/ml(\d+)", url):
        return int(m.group(1))
    mid = int(re.search(r"space\.bilibili\.com/(\d+)", url).group(1))
    fid = int(re.search(r"[?&]fid=(\d+)", url).group(1))
    return int(f"{fid}{str(mid)[-2:]}")

def get_cid(bvid: str) -> int:
    return S.get("https://api.bilibili.com/x/web-interface/view",
                 params={"bvid": bvid}).json()["data"]["cid"]

def best_audio_url(bvid: str, cid: int) -> str:
    d = S.get("https://api.bilibili.com/x/player/playurl",
              params={"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1}).json()["data"]
    audio = d["dash"]["audio"]
    return max(audio, key=lambda a: a["id"])["baseUrl"]   # 30280 = 192k m4a

def download_audio(bvid: str, dest: str):
    cid = get_cid(bvid)
    with S.get(best_audio_url(bvid, cid), stream=True, headers={"Range": "bytes=0-"}) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)

def find_lyrics(title: str, duration: float) -> dict:
    # 1) LRCLIB by title only
    r = S.get("https://lrclib.net/api/search", params={"track_name": title, "duration": int(duration)}).json()
    r = [x for x in r if not x.get("instrumental")]
    if r and SequenceMatcher(None, title.lower(), r[0]["trackName"].lower()).ratio() >= 0.6:
        return {"source": "lrclib", "synced": r[0]["syncedLyrics"], "plain": r[0]["plainLyrics"]}
    # 2) NetEase by title
    songs = S.get("https://music.163.com/api/search/get/web",
                  params={"s": title, "type": 1, "limit": 5}).json()["result"]["songs"]
    for s in songs:
        lrc = S.get("https://music.163.com/api/song/lyric",
                    params={"id": s["id"], "lv": 1, "kv": 1, "tv": -1}).json()
        if lrc.get("lrc", {}).get("lyric"):
            return {"source": "netease", "synced": lrc["lrc"]["lyric"],
                    "translation": lrc.get("tlyric", {}).get("lyric")}
    return {"source": None, "reason": "not_in_db"}   # handle gracefully

# usage
url = "https://space.bilibili.com/686127/favlist?fid=10526220"
videos = fetch_fav(media_id_from_url(url))           # see §1.1
for v in videos:
    try:
        download_audio(v["bvid"], f'{v["title"]}.m4a')
        ly = find_lyrics(v["title"], v["duration"])
        time.sleep(0.6)
    except Exception as e:
        print("skip", v["bvid"], e)
```

---

## Sources

- Bilibili API docs mirror (GitHub Pages of the archived project): [收藏夹内容 fav/resource/list](https://wuziqian211.github.io/bilibili-API-collect/docs/fav/list.html) · [收藏夹基本信息](https://wuziqian211.github.io/bilibili-API-collect/docs/fav/info.html) · [视频基本信息](https://wuziqian211.github.io/bilibili-API-collect/docs/video/info.html) · [视频流地址 playurl/DASH](https://wuziqian211.github.io/bilibili-API-collect/docs/video/videostream_url.html) · [视频TAG](https://wuziqian211.github.io/bilibili-API-collect/docs/video/tags.html) · [WBI 签名](https://wuziqian211.github.io/bilibili-API-collect/docs/misc/sign/wbi.html)
- Archived project notice + gitea mirror: [SocialSisterYi/bilibili-API-collect (deprecated)](https://github.com/SocialSisterYi/bilibili-API-collect) · [gitea mirror](https://gitea.s1f.ren/shiran/bilibili-API-collect)
- DeepWiki (fork docs, browsable): [Video Information & Metadata](https://deepwiki.com/afiuh/bilibili-api-collect/3.1-video-information-and-metadata) · [Video Streaming & Playback](https://deepwiki.com/afiuh/bilibili-api-collect/3.2-video-streaming-and-playback) · [WBI Signature System](https://deepwiki.com/afiuh/bilibili-api-collect/2.1-wbi-signature-system)
- yt-dlp bilibili: [PR #16889 412 fix (buvid fingerprint)](https://github.com/yt-dlp/yt-dlp/pull/16889) · [Issue #12013 412 circumvention & rate limits](https://github.com/yt-dlp/yt-dlp/issues/12013) · [extractor source](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py) · [B站下载指南](https://ytkz.tech/2025/06/20/bilibili-xia-zai-zhi-nan/)
- LRCLIB: [API docs](https://lrclib.net/docs) · [FreePublicAPIs listing](https://www.freepublicapis.com/crowd-sourced-lyrics-api)
- NetEase: [NeteaseCloudMusicApi](https://github.com/bubdm/NeteaseCloudMusicApi) · [网易云音乐简单API](https://www.daimadog.com/1020.html)
- QQ Music: [qq-music-api (copws)](https://raw.githubusercontent.com/copws/qq-music-api/master/README.md) · [Rain120/qq-music-api endpoints](https://deepwiki.com/Rain120/qq-music-api/4-api-endpoints-reference) · [Lyricify-Lyrics-Helper Chinese Music APIs](https://deepwiki.com/WXRIW/Lyricify-Lyrics-Helper/6.3-chinese-music-service-apis)

*All live-test timestamps: 2026-08-14; yt-dlp version 2026.07.04.*
