"""Persistent administrator credentials and optional RFC 6238 TOTP."""
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from contextlib import closing
from urllib.parse import quote, urlencode

import qrcode

from .config import ADMIN_USERNAME, ADMIN_PASSWORD
from .db import get_conn


def account():
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM admin_account WHERE id=1").fetchone()
        return dict(row) if row else None


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()
    return f'pbkdf2-sha256$600000${salt}${digest}'


def password_valid(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2-sha256' or not 600_000 <= int(rounds) <= 2_000_000:
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


def totp(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + '=' * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % (10 ** digits)
    return str(value).zfill(digits)


def matching_counter(secret: str, code: str, last_counter: int = -1):
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    current = int(time.time()) // 30
    for counter in (current, current - 1, current + 1):
        if counter > last_counter and hmac.compare_digest(totp(secret, counter), code):
            return counter
    return None


def authenticate(username: str, password: str, code: str):
    with closing(get_conn()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM admin_account WHERE id=1').fetchone()
        if not row:
            valid = hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode()) and hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode())
            return 'initial' if valid and ADMIN_PASSWORD else None
        if not hmac.compare_digest(username.encode(), row['username'].encode()) or not password_valid(password, row['password_hash']):
            return None
        if row['totp_secret']:
            counter = matching_counter(row['totp_secret'], code, row['last_totp_counter'])
            if counter is None:
                return None
            conn.execute('UPDATE admin_account SET last_totp_counter=? WHERE id=1', (counter,))
        conn.commit()
        return row['session_epoch']


def settings():
    row = account()
    return {'username': row['username'] if row else ADMIN_USERNAME,
            'two_factor_enabled': bool(row and row['totp_secret'])}


def change(action: str, password: str, code: str, epoch: str,
           username: str | None = None, new_password: str | None = None):
    """Reauthenticate and mutate in one transaction; never return active secrets."""
    with closing(get_conn()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM admin_account WHERE id=1').fetchone()
        if row:
            row = dict(row)
            if row['session_epoch'] != epoch or not password_valid(password, row['password_hash']):
                raise ValueError('当前密码错误或会话已失效')
        else:
            if epoch != 'initial' or not ADMIN_PASSWORD or not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
                raise ValueError('当前密码错误或账户未配置')
            row = {'username': ADMIN_USERNAME, 'password_hash': hash_password(ADMIN_PASSWORD),
                   'session_epoch': 'initial', 'totp_secret': None, 'pending_secret': None,
                   'pending_since': None, 'last_totp_counter': -1}
        counter = row['last_totp_counter']
        if row['totp_secret']:
            counter = matching_counter(row['totp_secret'], code, counter)
            if counter is None:
                raise ValueError('动态验证码错误或已使用，请等待下一组验证码')
        result = {}
        if action == 'account':
            if username is not None:
                username = username.strip()
                if not username or len(username) > 80:
                    raise ValueError('用户名需为 1–80 字')
                row['username'] = username
            if new_password:
                if not 12 <= len(new_password) <= 256:
                    raise ValueError('新密码需为 12–256 字')
                row['password_hash'] = hash_password(new_password)
            row['session_epoch'] = secrets.token_hex(24)
        elif action == 'setup':
            if row['totp_secret']:
                raise ValueError('已开启 2FA；更换验证器请先关闭')
            row['pending_secret'] = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
            row['pending_since'] = time.time()
            uri = 'otpauth://totp/' + quote('BiliMusicPlayer:' + row['username'], safe='') + '?' + urlencode({
                'secret': row['pending_secret'], 'issuer': 'BiliMusicPlayer', 'algorithm': 'SHA1', 'digits': 6, 'period': 30})
            image = qrcode.make(uri)
            output = io.BytesIO()
            image.save(output, format='PNG')
            result = {'secret': row['pending_secret'], 'uri': uri,
                      'qr_image': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()}
        elif action == 'enable':
            if not row['pending_secret'] or time.time() - (row['pending_since'] or 0) > 600:
                raise ValueError('绑定已过期，请重新获取二维码')
            counter = matching_counter(row['pending_secret'], code)
            if counter is None:
                raise ValueError('动态验证码错误，请核对验证器和服务器时间')
            row['totp_secret'] = row['pending_secret']
            row['pending_secret'] = row['pending_since'] = None
            row['session_epoch'] = secrets.token_hex(24)
        elif action == 'disable':
            row['totp_secret'] = row['pending_secret'] = row['pending_since'] = None
            counter = -1
            row['session_epoch'] = secrets.token_hex(24)
        else:
            raise ValueError('不支持的操作')
        row['last_totp_counter'] = counter
        conn.execute('INSERT INTO admin_account (id,username,password_hash,session_epoch,totp_secret,pending_secret,pending_since,last_totp_counter) '
                     'VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                     'username=excluded.username,password_hash=excluded.password_hash,session_epoch=excluded.session_epoch,'
                     'totp_secret=excluded.totp_secret,pending_secret=excluded.pending_secret,'
                     'pending_since=excluded.pending_since,last_totp_counter=excluded.last_totp_counter',
                     tuple(row[key] for key in ('username', 'password_hash', 'session_epoch', 'totp_secret', 'pending_secret', 'pending_since', 'last_totp_counter')))
        conn.commit()
        return {**result, 'username': row['username'], 'two_factor_enabled': bool(row['totp_secret']), '_epoch': row['session_epoch']}
