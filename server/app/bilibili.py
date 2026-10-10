"""B 站接口封装：收藏夹列表 / 视频详情 / 标签。

公开收藏夹无需登录；私密收藏夹需携带 SESSDATA 等 Cookie。
"""
import re
import threading
import time
from typing import Optional

import httpx

from .config import BILI_API_INTERVAL, BILI_HEADERS

FAV_API = "https://api.bilibili.com/x/v3/fav/resource/list"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
PAGES_API = "https://api.bilibili.com/x/player/pagelist"
TAGS_API = "https://api.bilibili.com/x/tag/archive/tags"
SPI_API = "https://api.bilibili.com/x/frontend/finger/spi"
PLAYURL_API = "https://api.bilibili.com/x/player/playurl"

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


def fetch_playurl(bvid: str, cid: int, cookie: Optional[str] = None) -> dict:
    """获取 DASH 播放地址（旧接口，无需 WBI 签名）。"""
    _throttle()
    with _client(cookie) as client:
        r = client.get(
            PLAYURL_API,
            params={"bvid": bvid, "cid": cid, "fnval": 16, "fourk": 1, "platform": "pc"},
        )
        data = _response_json(r, f"获取 {bvid} 播放地址")
        if data.get("code") != 0:
            raise BiliError(f"获取 {bvid} 播放地址失败: {data.get('message')} (code={data.get('code')})")
        return data["data"]
