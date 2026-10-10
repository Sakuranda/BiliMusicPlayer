"""全局配置。"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
MUSIC_DIR = DATA_DIR / "music"      # Navidrome 扫描此目录
COVER_DIR = DATA_DIR / "covers"     # 封面缓存
COOKIE_DIR = DATA_DIR / "cookies"   # 用户粘贴的 B 站 Cookie（Netscape 格式）
DB_PATH = DATA_DIR / "musicplayer.db"

# 常见浏览器请求头，避免 B 站接口反爬
BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """读取整数环境变量，并把危险配置限制在可接受范围内。"""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


# 下载并发是整个 API 进程共享的，不是“每个收藏夹各自拥有”。B 站在数据中心
# IP 上对高频请求较敏感，因此默认 3，且无论环境变量如何配置都不允许超过 5。
DOWNLOAD_THREADS = _bounded_int("DOWNLOAD_THREADS", 3, 1, 5)
METADATA_THREADS = _bounded_int("METADATA_THREADS", 4, 1, 8)
COVER_MAX_SIZE = _bounded_int("COVER_MAX_SIZE", 320, 160, 640)
COVER_QUALITY = _bounded_int("COVER_QUALITY", 70, 40, 85)

try:
    BILI_API_INTERVAL = max(0.25, float(os.environ.get("BILI_API_INTERVAL", "0.5")))
except ValueError:
    BILI_API_INTERVAL = 0.5

try:
    LYRICS_API_INTERVAL = max(0.1, float(os.environ.get("LYRICS_API_INTERVAL", "0.35")))
except ValueError:
    LYRICS_API_INTERVAL = 0.35

# 公网部署时建议设置 API 访问令牌（前端设置页里填同一个值）
API_TOKEN = os.environ.get("API_TOKEN", "")

# 网页管理端使用账号密码 + 图片验证码登录；API_TOKEN 继续作为脚本/应急兼容通道。
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "") or API_TOKEN
SESSION_TTL_HOURS = _bounded_int("SESSION_TTL_HOURS", 24 * 30, 1, 24 * 365)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
