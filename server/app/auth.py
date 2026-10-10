"""网页登录、图片验证码、签名会话与访问来源解析。"""
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import random
import secrets
import threading
import time
from collections import defaultdict, deque

import httpx
from PIL import Image, ImageDraw, ImageFont
from . import admin

from .config import (ADMIN_PASSWORD, ADMIN_USERNAME, AUTH_SECRET,
                     SESSION_TTL_HOURS)

COOKIE_NAME = "musicplayer_session"
CAPTCHA_TTL = 300
FAIL_WINDOW = 600
FAIL_LIMIT = 5
LOCK_SECONDS = 900

_secret = (AUTH_SECRET or secrets.token_hex(32)).encode()
_captchas: dict[str, tuple[str, float]] = {}
_failures: dict[str, deque[float]] = defaultdict(deque)
_locked_until: dict[str, float] = {}
_last_touch: dict[str, float] = {}
_lock = threading.Lock()


def enabled() -> bool:
    row = admin.account()
    return bool(row or (ADMIN_USERNAME and ADMIN_PASSWORD))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session(username: str, epoch: str | None = None, session_id: str | None = None) -> tuple[str, str]:
    session_id = session_id or secrets.token_urlsafe(18)
    row = admin.account()
    payload = _b64(json.dumps({
        "sub": username,
        "sid": session_id,
        "epoch": epoch if epoch is not None else (row['session_epoch'] if row else 'initial'),
        "exp": int(time.time()) + SESSION_TTL_HOURS * 3600,
    }, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_secret, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}", session_id


def verify_session(token: str | None) -> dict | None:
    if not token or len(token) > 4096 or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    expected = _b64(hmac.new(_secret, payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature.encode(), expected.encode()):
        return None
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exp"), (int, float)):
        return None
    if data.get("exp", 0) < time.time() or not data.get("sid") or not data.get("sub"):
        return None
    row = admin.account()
    if data.get('epoch') != (row['session_epoch'] if row else 'initial'):
        return None
    return data


def create_captcha() -> dict:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    answer = "".join(secrets.choice(alphabet) for _ in range(5))
    challenge_id = secrets.token_urlsafe(18)
    now_ts = time.time()
    with _lock:
        for key, (_, expires) in list(_captchas.items()):
            if expires < now_ts:
                _captchas.pop(key, None)
        if len(_captchas) >= 512:
            _captchas.pop(next(iter(_captchas)))
        _captchas[challenge_id] = (answer, now_ts + CAPTCHA_TTL)

    image = Image.new("RGB", (150, 52), (33, 31, 27))
    draw = ImageDraw.Draw(image)
    for _ in range(6):
        color = random.choice(((217, 119, 87), (105, 100, 89), (75, 71, 63)))
        draw.line((random.randrange(150), random.randrange(52), random.randrange(150), random.randrange(52)), fill=color, width=1)
    font = ImageFont.load_default(size=28)
    for index, char in enumerate(answer):
        draw.text((11 + index * 27, random.randrange(7, 15)), char, font=font, fill=(240, 238, 230))
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return {
        "id": challenge_id,
        "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
        "expires_in": CAPTCHA_TTL,
    }


def consume_captcha(challenge_id: str, answer: str) -> bool:
    with _lock:
        stored = _captchas.pop(challenge_id, None)
    return bool(stored and stored[1] >= time.time() and hmac.compare_digest(stored[0].encode(), answer.strip().upper().encode()))


def login_allowed(ip: str) -> tuple[bool, int]:
    now_ts = time.time()
    with _lock:
        locked = _locked_until.get(ip, 0)
        if locked > now_ts:
            return False, int(locked - now_ts) + 1
        attempts = _failures[ip]
        while attempts and attempts[0] < now_ts - FAIL_WINDOW:
            attempts.popleft()
        return True, 0


def record_failure(ip: str) -> None:
    now_ts = time.time()
    with _lock:
        attempts = _failures[ip]
        while attempts and attempts[0] < now_ts - FAIL_WINDOW:
            attempts.popleft()
        attempts.append(now_ts)
        if len(attempts) >= FAIL_LIMIT:
            _locked_until[ip] = now_ts + LOCK_SECONDS
            attempts.clear()


def clear_failures(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)
        _locked_until.pop(ip, None)


def credentials_valid(username: str, password: str) -> bool:
    row = admin.account()
    if row:
        return hmac.compare_digest(username.encode(), row['username'].encode()) and admin.password_valid(password, row['password_hash'])
    return hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode()) and hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())


def client_ip(peer: str | None, forwarded_for: str | None) -> str:
    peer = peer or "unknown"
    try:
        trusted_proxy = ipaddress.ip_address(peer).is_private or ipaddress.ip_address(peer).is_loopback
    except ValueError:
        trusted_proxy = False
    if trusted_proxy and forwarded_for:
        candidate = forwarded_for.split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return peer


def lookup_location(ip: str) -> dict:
    try:
        address = ipaddress.ip_address(ip)
        if address.is_private or address.is_loopback:
            return {"country": "本地网络", "region": None, "city": None}
    except ValueError:
        return {"country": None, "region": None, "city": None}
    try:
        response = httpx.get(
            f"https://ipwho.is/{ip}",
            params={"fields": "success,country,region,city"},
            # 香港机房到免费 GeoIP 端点的 TLS + 响应实测偶尔超过 2.5 秒。
            # 仅新 IP 首次成功登录会查询，后续均走 SQLite 缓存。
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("success"):
            return {"country": data.get("country"), "region": data.get("region"), "city": data.get("city")}
    except (httpx.HTTPError, ValueError):
        pass
    return {"country": None, "region": None, "city": None}


def should_touch(session_id: str) -> bool:
    now_ts = time.time()
    with _lock:
        if now_ts - _last_touch.get(session_id, 0) < 60:
            return False
        _last_touch[session_id] = now_ts
        return True
