"""Choose between the signed CDN URLs returned for one DASH audio stream.

Only a small Range is sampled; host scores, never signed URLs, are cached.
This does not change the API request rate, source IP or download concurrency.
"""
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

from .config import BILI_HEADERS

PROBE_BYTES = 128 * 1024
PROBE_SECONDS = 4.0
CACHE_SECONDS = 300.0
_scores: OrderedDict = OrderedDict()
_lock = threading.Lock()


def audio_urls(audio: dict) -> list[str]:
    urls = []
    backups = audio.get("backupUrl") or audio.get("backup_url") or []
    if isinstance(backups, str):
        backups = [backups]
    for url in [audio.get("baseUrl") or audio.get("base_url"), *backups]:
        if not isinstance(url, str) or url in urls:
            continue
        parsed = urlsplit(url)
        if parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username:
            urls.append(url)
    return urls


def _probe(url: str) -> float:
    started = time.monotonic()
    received = 0
    try:
        with httpx.stream(
            "GET", url, headers={**BILI_HEADERS, "Range": f"bytes=0-{PROBE_BYTES - 1}",
                                  "Accept-Encoding": "identity"},
            timeout=httpx.Timeout(2.0), follow_redirects=False,
        ) as response:
            # Never consume a whole song if a server ignores the bounded Range.
            match = re.fullmatch(r"bytes 0-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if response.status_code != 206 or not match or int(match[1]) >= PROBE_BYTES:
                return 0.0
            for chunk in response.iter_raw():
                received += len(chunk)
                if received >= int(match[1]) + 1 or time.monotonic() - started >= PROBE_SECONDS:
                    break
        elapsed = time.monotonic() - started
        return received / max(elapsed, 0.001) if received else 0.0
    except Exception:
        # Exceptions may contain the signed URL; do not log or propagate them.
        return 0.0


def ordered_urls(urls: list[str]) -> list[str]:
    hosts = tuple(sorted({urlsplit(url).hostname for url in urls}))
    if len(hosts) < 2:
        return urls
    # Serialize cold probes so concurrent songs do not sample the same hosts.
    with _lock:
        now = time.monotonic()
        cached = _scores.get(hosts)
        if cached and cached[0] > now:
            _scores.move_to_end(hosts)
            scores = cached[1]
        else:
            scores = {}
            for url in urls:
                host = urlsplit(url).hostname
                if host not in scores and len(scores) < 3:
                    scores[host] = _probe(url)
            _scores[hosts] = (time.monotonic() + CACHE_SECONDS, scores)
            _scores.move_to_end(hosts)
            while len(_scores) > 32:
                _scores.popitem(last=False)
        return sorted(urls, key=lambda url: scores.get(urlsplit(url).hostname, 0), reverse=True)


def failed(url: str) -> None:
    host = urlsplit(url).hostname
    with _lock:
        for _, scores in _scores.values():
            if host in scores:
                scores[host] = 0.0


class SafeDownloadLogger:
    """Suppress extractor messages that can expose signed URLs in job logs."""
    def debug(self, *_args):
        pass

    def warning(self, *_args):
        pass

    def error(self, *_args):
        pass
