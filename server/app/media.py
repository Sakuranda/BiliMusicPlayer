"""Normalize downloaded DASH containers without re-encoding their AAC packets."""
import os
import shutil
import struct
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from mutagen.mp4 import MP4


_FILE_LOCKS = tuple(threading.RLock() for _ in range(64))


def media_lock(path: Path):
    """Serialize mutations of a file without retaining an unbounded lock cache."""
    return _FILE_LOCKS[hash(str(path.resolve())) % len(_FILE_LOCKS)]


@contextmanager
def atomic_media_copy(path: Path):
    """Edit a private copy; existing streams keep reading the complete old inode."""
    with media_lock(path):
        fd, name = tempfile.mkstemp(prefix='.metadata-', suffix='.tmp', dir=path.parent)
        os.close(fd)
        target = Path(name)
        try:
            shutil.copyfile(path, target)
            target.chmod(path.stat().st_mode & 0o777)
            yield target
            with target.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(target, path)
        finally:
            target.unlink(missing_ok=True)


def _container_flags(path: Path) -> tuple[bool, bool]:
    """Validate every top-level atom, including atoms after the first fragment."""
    has_fragment = False
    moov_offset = media_offset = None
    size = path.stat().st_size
    with path.open('rb') as stream:
        while stream.tell() < size:
            offset = stream.tell()
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError('不完整的 MP4 文件头')
            length, kind = struct.unpack('>I4s', header)
            header_size = 8
            if length == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    raise ValueError('不完整的 MP4 扩展头')
                length = struct.unpack('>Q', extended)[0]
                header_size = 16
            if length == 0:
                length = size - offset
            if length < header_size or offset + length > size:
                raise ValueError('不完整的 MP4 数据')
            if kind == b'moof':
                has_fragment = True
            if kind == b'moov' and moov_offset is None:
                moov_offset = offset
            if kind == b'mdat' and media_offset is None:
                media_offset = offset
            stream.seek(offset + length)
    faststart = moov_offset is not None and media_offset is not None and moov_offset < media_offset
    return has_fragment, faststart


def fragmented(path: Path) -> bool:
    return _container_flags(path)[0]


def needs_normalization(path: Path) -> bool:
    has_fragment, faststart = _container_flags(path)
    return has_fragment or not faststart or MP4(path).info.length <= 0


def normalize_m4a(path: Path) -> float:
    """Atomic remux; preserve all Mutagen tags, cover and lyrics, fail closed."""
    with media_lock(path):
        return _normalize_m4a(path)


def _normalize_m4a(path: Path) -> float:
    original = MP4(path)
    has_fragment, faststart = _container_flags(path)
    if not has_fragment and faststart and original.info.length > 0:
        return original.info.length
    tags = dict(original.tags or {})
    fd, name = tempfile.mkstemp(prefix='.normalize-', suffix='.tmp', dir=path.parent)
    os.close(fd)
    target = Path(name)
    try:
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-y', '-i', str(path),
            '-map', '0:a:0', '-c:a', 'copy', '-map_metadata', '-1',
            '-movflags', '+faststart', '-f', 'ipod', str(target),
        ], check=True, capture_output=True, timeout=180)
        normalized = MP4(target)
        duration = normalized.info.length
        if duration <= 0 or fragmented(target):
            raise ValueError('封装后没有有效的音频时长')
        # Compare packet count AND compressed packet hashes, including the last
        # fragment. A successful ffmpeg exit alone does not prove completeness.
        def packets(file: Path) -> bytes:
            result = subprocess.run([
                'ffprobe', '-v', 'error', '-select_streams', 'a:0',
                '-show_packets', '-show_entries', 'packet=data_hash',
                '-show_data_hash', 'sha256', '-of', 'csv=p=0', str(file),
            ], check=True, capture_output=True, timeout=180)
            if result.stderr.strip():
                raise ValueError('音频包校验报错，保留原文件')
            return result.stdout
        before = packets(path)
        if not before.strip() or before != packets(target):
            raise ValueError('封装前后音频包不一致，保留原文件')
        if normalized.tags is None:
            normalized.add_tags()
        normalized.tags.update(tags)
        normalized.save()
        target.chmod(path.stat().st_mode & 0o777)
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(target, path)
        return duration
    finally:
        target.unlink(missing_ok=True)
