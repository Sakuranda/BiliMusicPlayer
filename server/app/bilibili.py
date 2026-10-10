"""B 站接口封装：收藏夹列表 / 视频详情 / 标签。

公开收藏夹无需登录；私密收藏夹需携带 SESSDATA 等 Cookie。
"""
import hashlib
import re
import threading
import time
from typing import Optional
from urllib.parse import urlencode, urlsplit

import httpx

from .config import BILI_API_INTERVAL, BILI_HEADERS

FAV_API = "https://api.bilibili.com/x/v3/fav/resource/list"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PAGES_API = "https://api.bilibili.com/x/player/pagelist"
TAGS_API = "https://api.bilibili.com/x/tag/archive/tags"
SPI_API = "https://api.bilibili.com/x/frontend/finger/spi"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"
WBI_PLAYURL_API = "https://api.bilibili.com/x/player/wbi/playurl"
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
_WBI_KEY_TTL = 3600
_WBI_FAILURE_COOLDOWN = 60
_wbi_lock = threading.Lock()
_wbi_cache = {"key": None, "expires": 0.0, "retry_at": 0.0}
_WBI_MIXIN = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,
              29,28,14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,
              25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]

# 简单全局限速：避免持续高频请求触发 412 反爬
_rate_lock = threading.Lock()
_last_call = 0.0
_MIN_INTERVAL = BILI_API_INTERVAL


def _throttle():
    global _last_call
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


class BiliError(Exception):
    pass


def parse_fav_url(url: str) -> list[str]:
    """从收藏夹链接中提取候选 media_id 列表（依次尝试）。

    支持:
    - https://space.bilibili.com/{mid}/favlist?fid={x}
      不同版本网页的 fid 含义不同：有的 fid 就是 media_id，有的
      media_id = fid + UP主 uid 后两位。两种候选都返回，哪个有数据用哪个。
    - https://www.bilibili.com/medialist/detail/ml{mid} → mid 即 media_id
    - https://b23.tv/xxxx（分享短链，自动跟随跳转）
    """
    url = url.strip()

    # 短链：跟随跳转拿到真实地址再解析
    if "b23.tv" in url or "bili2233.cn" in url:
        try:
            with httpx.Client(headers=BILI_HEADERS, timeout=15, follow_redirects=True) as c:
                r = c.get(url)
                url = str(r.url)
        except Exception as e:  # noqa: BLE001
            raise BiliError(f"短链展开失败：{e}，请直接复制浏览器地址栏的完整链接") from e

    m = re.search(r"medialist/detail/ml(\d+)", url)
    if m:
        return [m.group(1)]
    m_mid = re.search(r"space\.bilibili\.com/(\d+)", url)
    m_fid = re.search(r"[?&]fid=(\d+)", url)
    if m_mid and m_fid:
        mid, fid = m_mid.group(1), m_fid.group(1)
        candidates = [fid, f"{fid}{mid[-2:]}"]
        # 去重保序
        seen, out = set(), []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out
    if "favlist" in url and m_mid:
        raise BiliError(
            "链接里缺少收藏夹编号（fid）。请在 B 站打开【我的收藏】→ 点进那个收藏夹，"
            "然后复制浏览器地址栏形如 space.bilibili.com/xxx/favlist?fid=xxx 的完整链接"
        )
    if re.search(r"bilibili\.com/list/ml\d+", url):
        raise BiliError("这看起来是 B 站「合集/列表」链接。请确认用的是【收藏夹】：头像 → 我的收藏 → 点进收藏夹复制地址栏链接")
    raise BiliError("无法从链接中识别收藏夹 ID，请提供形如 …/favlist?fid=xxx 或 …/medialist/detail/mlxxx 的链接")


def _client(cookie: Optional[str] = None) -> httpx.Client:
    headers = dict(BILI_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    return httpx.Client(headers=headers, timeout=30, follow_redirects=True)


def _response_json(response: httpx.Response, context: str) -> dict:
    """将网络层错误统一转换成可读的 BiliError。"""
    if response.status_code != 200:
        raise BiliError(f"{context}失败：B 站返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise BiliError(f"{context}失败：B 站返回了无法解析的响应") from exc
    if not isinstance(data, dict):
        raise BiliError(f"{context}失败：B 站返回的数据格式异常")
    return data


def fetch_favorites(media_ids, cookie: Optional[str] = None, page_size: int = 20):
    """依次尝试候选 media_id，返回 (收藏夹标题, [视频信息...], 成功的 media_id)。

    私密收藏夹带 Cookie 才能读到；未带 Cookie 时 B 站返回空数据（code=0）。
    """
    if isinstance(media_ids, str):
        media_ids = [media_ids]
    saw_empty, saw_denied = False, False

    with _client(cookie) as client:
        for media_id in media_ids:
            pn, items, info = 1, [], None
            failed = False
            while True:
                _throttle()
                r = client.get(
                    FAV_API,
                    params={"media_id": media_id, "pn": pn, "ps": page_size, "platform": "web"},
                )
                data = _response_json(r, "获取收藏夹")
                if data.get("code") != 0 or not data.get("data"):
                    if pn > 1:
                        # 不完整快照绝不能交给收藏夹关系替换逻辑，否则会误删后续页成员。
                        raise BiliError(f"获取收藏夹第 {pn} 页失败，请稍后重试（已保留原收藏夹）")
                    if pn == 1:
                        failed = True
                        code = data.get("code")
                        if code == 0:
                            saw_empty = True
                        elif code in (-403, -101):
                            saw_denied = True
                    break  # 该候选无效或翻到末页
                info = data["data"]["info"]
                medias = data["data"].get("medias") or []
                if not medias and data["data"].get("has_more"):
                    raise BiliError(f"收藏夹第 {pn} 页数据不完整，请稍后重试")
                for m in medias:
                    if m.get("type") != 2:  # 只要视频（跳过音频/合集等）
                        continue
                    items.append(
                        {
                            "bvid": m["bvid"],
                            "raw_title": m["title"],
                            "cover_url": m.get("cover", ""),
                            "duration": m.get("duration", 0),
                            "page_count": m.get("page") or 1,
                            "uploader": (m.get("upper") or {}).get("name", ""),
                            "uploader_mid": (m.get("upper") or {}).get("mid", 0),
                            # 收藏夹接口通常直接带首 P cid。详情接口受风控时也能用它
                            # 请求 playurl，避免整条直连路线依赖 view API。
                            "cid": (m.get("ugc") or {}).get("first_cid"),
                        }
                    )
                if not data["data"].get("has_more"):
                    break
                pn += 1
                time.sleep(0.3)

            if not failed and info is not None:
                # 收藏夹在翻页期间可能改变顺序，跨页重复视频只保留首次出现。
                unique = {}
                for item in items:
                    unique.setdefault(item["bvid"], item)
                return info.get("title", "B站收藏夹"), list(unique.values()), media_id

    if saw_empty and not saw_denied:
        raise BiliError(
            "B 站返回空数据：该收藏夹大概率是私密的。请在导入页展开「粘贴 B 站 Cookie」"
            "填入浏览器 Cookie 后重试"
        )
    if saw_denied:
        raise BiliError("该收藏夹为私密或不存在，请检查链接，或在导入时粘贴你的 B 站 Cookie")
    raise BiliError("获取收藏夹失败：收藏夹不存在或链接有误")


def fetch_buvid() -> dict[str, str]:
    """获取 B 站设备指纹 Cookie（buvid3/buvid4）。

    数据中心 IP 下载时 B 站常要求指纹 Cookie，否则 412。
    """
    try:
        _throttle()
        with _client() as client:
            r = client.get(SPI_API)
            d = _response_json(r, "获取设备指纹").get("data") or {}
        out = {}
        if d.get("b_3"):
            out["buvid3"] = d["b_3"]
        if d.get("b_4"):
            out["buvid4"] = d["b_4"]
        return out
    except Exception:
        return {}


def fetch_tags(bvid: str, cookie: Optional[str] = None) -> list[str]:
    """获取视频标签（虚拟主播翻唱常把歌手名放在标签里）。"""
    try:
        _throttle()
        with _client(cookie) as client:
            r = client.get(TAGS_API, params={"bvid": bvid})
            data = _response_json(r, f"获取视频 {bvid} 标签")
            if data.get("code") == 0:
                return [t.get("tag_name", "") for t in data.get("data") or []]
    except Exception:
        pass
    return []


def fetch_detail(bvid: str, cookie: Optional[str] = None) -> dict:
    """获取视频详情（cid/封面/时长，playurl 需要 cid）。"""
    _throttle()
    with _client(cookie) as client:
        try:
            r = client.get(VIEW_API, params={"bvid": bvid})
            data = _response_json(r, f"获取视频 {bvid} 详情")
            if data.get("code") != 0 or not isinstance(data.get("data"), dict):
                raise BiliError(f"获取视频 {bvid} 详情失败: {data.get('message')}")
            return data["data"]
        except (BiliError, httpx.HTTPError) as exc:
            original_error = exc
        # Datacenter IPs can lose view access while the official page-list
        # endpoint still works. Partial detail must not invent a cover or credits.
        try:
            _throttle()
            r = client.get(PAGES_API, params={"bvid": bvid})
            data = _response_json(r, f"获取视频 {bvid} 分P")
            pages = data.get("data")
            if data.get("code") == 0 and isinstance(pages, list) and pages and all(
                isinstance(page, dict) and page.get("cid") and page.get("page") for page in pages
            ):
                return {"cid": pages[0]["cid"], "pages": pages}
        except (BiliError, httpx.HTTPError):
            pass
        raise BiliError(str(original_error)) from original_error


def _wbi_failed():
    with _wbi_lock:
        now = time.monotonic()
        if now >= _wbi_cache['retry_at']:
            _wbi_cache.update(key=None, expires=0.0, retry_at=now + _WBI_FAILURE_COOLDOWN)


def _wbi_key(client: httpx.Client) -> str:
    # The same public signing keys serve all cookies; do not cache credentials.
    with _wbi_lock:
        now = time.monotonic()
        if now < _wbi_cache['retry_at']:
            raise BiliError('WBI接口暂不可用，请稍后重试')
        if _wbi_cache['key'] and now < _wbi_cache['expires']:
            return _wbi_cache['key']
        _throttle()
        response = _response_json(client.get(NAV_API), '获取WBI签名信息')
        # Anonymous nav normally reports code=-101 but still supplies wbi_img.
        data = response.get('data')
        images = data.get('wbi_img') if isinstance(data, dict) else None
        if not isinstance(images, dict):
            raise BiliError('B站未返回有效WBI签名信息，请稍后重试')
        parts = []
        for field in ('img_url', 'sub_url'):
            url = images.get(field)
            if not isinstance(url, str):
                raise BiliError('B站未返回有效WBI签名信息，请稍后重试')
            filename = urlsplit(url).path.rsplit('/', 1)[-1].split('.', 1)[0]
            if not re.fullmatch(r'[0-9a-fA-F]{32}', filename):
                raise BiliError('B站未返回有效WBI签名信息，请稍后重试')
            parts.append(filename)
        material = ''.join(parts)
        key = ''.join(material[index] for index in _WBI_MIXIN)[:32]
        _wbi_cache.update(key=key, expires=time.monotonic() + _WBI_KEY_TTL, retry_at=0.0)
        return key


def _sign_wbi(params: dict, key: str) -> dict:
    # Matches yt-dlp's BilibiliBaseIE._sign_wbi; never log signed parameters.
    values = {**params, 'wts': round(time.time())}
    values = {name: ''.join(char for char in str(value) if char not in "!'()*")
              for name, value in sorted(values.items())}
    values['w_rid'] = hashlib.md5((urlencode(values) + key).encode()).hexdigest()
    return values


def _playurl_json(client: httpx.Client, endpoint: str, params: dict) -> dict:
    _throttle()
    response = _response_json(client.get(endpoint, params=params), '获取音频播放地址')
    code = response.get('code')
    if code != 0 or not isinstance(response.get('data'), dict):
        safe_code = code if isinstance(code, int) else '未知'
        raise BiliError(f'获取音频播放地址失败（code={safe_code}），请稍后重试')
    return response['data']


def fetch_playurl(bvid: str, cid: int, cookie: Optional[str] = None) -> dict:
    """Prefer the existing API; use signed WBI only after an actual failure.

    Neither route needs view when CID is known. Every network request uses the
    existing global throttle and client cookie/referer, with deterministic close.
    """
    params = {'bvid': bvid, 'cid': cid, 'fnval': 16, 'fourk': 1, 'platform': 'pc'}
    with _client(cookie) as client:
        try:
            return _playurl_json(client, PLAYURL_API, params)
        except (BiliError, httpx.HTTPError) as error:
            old_error = str(error) if isinstance(error, BiliError) else '获取音频播放地址请求失败'
        try:
            key = _wbi_key(client)
            return _playurl_json(client, WBI_PLAYURL_API, _sign_wbi({**params, 'fnval': 4048}, key))
        except (BiliError, httpx.HTTPError, ValueError) as error:
            _wbi_failed()
            new_error = str(error) if isinstance(error, BiliError) else 'WBI播放地址请求失败'
            # HTTP errors may contain cookies or signed URLs; never expose them
            # in job notices or exception chains consumed by download workers.
            raise BiliError(f'旧播放接口与WBI备用接口均失败：{old_error}；{new_error}') from None
