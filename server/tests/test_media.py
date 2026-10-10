import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from mutagen.mp4 import MP4
from app import downloader
from app.media import fragmented, needs_normalization, normalize_m4a


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires ffmpeg')
class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'fragmented.m4a'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=2', '-c:a', 'aac',
                        '-movflags', '+frag_keyframe+empty_moov+default_base_moof',
                        str(self.path)], check=True)
        audio = MP4(self.path)
        audio['\xa9nam'] = ['保留歌名']
        audio['\xa9lyr'] = ['[00:00.00]保留歌词']
        audio.save()

    def test_remux_preserves_packets_tags_and_becomes_seekable(self):
        self.assertTrue(fragmented(self.path))
        self.assertEqual(MP4(self.path).info.length, 0)
        duration = normalize_m4a(self.path)
        self.assertAlmostEqual(duration, 2, delta=.1)
        self.assertFalse(fragmented(self.path))
        self.assertEqual(MP4(self.path)['\xa9nam'], ['保留歌名'])
        self.assertEqual(MP4(self.path)['\xa9lyr'], ['[00:00.00]保留歌词'])
        data = self.path.read_bytes()
        self.assertEqual(normalize_m4a(self.path), duration)
        self.assertEqual(data, self.path.read_bytes())

    def test_failed_remux_does_not_replace_original(self):
        original = self.path.read_bytes()
        with patch('app.media.subprocess.run', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                normalize_m4a(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob('.normalize-*')))

    def test_truncated_later_fragment_is_rejected_without_replacement(self):
        # The old atom walker stopped at the first moof and never saw a broken
        # atom later in the file; ffmpeg could salvage a partial song as success.
        data = self.path.read_bytes() + b'\x00\x00\x00\x40mdatshort'
        self.path.write_bytes(data)
        with self.assertRaises(ValueError):
            normalize_m4a(self.path)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertFalse(list(self.path.parent.glob('.normalize-*')))

    def test_nonfragmented_slowstart_is_also_normalized(self):
        path = self.path.with_name('slowstart.m4a')
        subprocess.run(['ffmpeg', '-v', 'error', '-i', str(self.path),
                        '-c:a', 'copy', str(path)], check=True)
        self.assertFalse(fragmented(path))
        self.assertTrue(needs_normalization(path))
        normalize_m4a(path)
        self.assertFalse(needs_normalization(path))

    def test_valid_short_audio_does_not_pass_source_duration_validation(self):
        normalize_m4a(self.path)
        self.assertFalse(needs_normalization(self.path))
        with self.assertRaisesRegex(RuntimeError, '下载音频不完整'):
            downloader._validate_download({'file': self.path}, 214.037)
        self.assertTrue(self.path.is_file())

    def test_source_duration_rounding_is_tolerated_and_actual_duration_returned(self):
        result = downloader._validate_download({'file': self.path}, 3.0)
        self.assertAlmostEqual(result['duration'], 2.0, delta=.1)
        self.assertFalse(needs_normalization(self.path))

    def test_metadata_edits_preserve_existing_release_and_grouping_tags(self):
        normalize_m4a(self.path)
        audio = MP4(self.path)
        preserved = {
            'aART': ['原专辑歌手'], 'cpil': False,
            'soar': ['Artist sort'], 'soaa': ['Album artist sort'],
            'soal': ['Album sort'], 'sonm': ['Title sort'],
            '----:com.apple.iTunes:ARTISTS': [b'Original performer'],
            '----:com.apple.iTunes:ALBUMARTISTS': [b'Original album artist'],
            '----:com.apple.iTunes:MusicBrainz Album Id': [b'original-album-id'],
            '----:com.apple.iTunes:MusicBrainz Album Artist Id': [b'original-artist-id'],
            '\xa9day': ['2025'],
            '----:com.apple.iTunes:RELEASEDATE': [b'2025-01-01'],
            '----:com.apple.iTunes:ALBUMVERSION': [b'Live'],
            '----:com.apple.iTunes:MusicBrainz Album Comment': [b'Original release'],
        }
        audio.tags.update(preserved)
        audio.save()
        downloader.update_audio_metadata(self.path, title='歌名', artist='演唱者 A', album='Music Player')
        audio = MP4(self.path)
        self.assertEqual(audio['\xa9ART'], ['演唱者 A'])
        for key, value in preserved.items():
            self.assertEqual(audio[key], value, key)
        self.assertFalse(downloader.metadata_needs_update(self.path, '歌名', '演唱者 A', 'Music Player'))
        downloader.update_audio_metadata(self.path, album='')
        audio = MP4(self.path)
        self.assertNotIn('\xa9alb', audio)
        for key, value in preserved.items():
            self.assertEqual(audio[key], value, key)

    def test_tag_and_store_does_not_introduce_compilation_tags(self):
        music = self.path.parent / 'library'
        with patch.object(downloader, 'MUSIC_DIR', music):
            rel = downloader.tag_and_store(self.path, '歌名', '歌手', 'Music Player', None, 'BVTEST')
        audio = MP4(music / rel)
        self.assertNotIn('aART', audio)
        self.assertNotIn('cpil', audio)
        self.assertEqual(audio['\xa9ART'], ['歌手'])

    def test_tag_edit_does_not_mutate_file_being_streamed(self):
        normalize_m4a(self.path)
        original = self.path.read_bytes()
        with self.path.open('rb') as active_stream:
            downloader.update_audio_metadata(self.path, title='新歌', artist='歌手', album='Music Player')
            self.assertEqual(active_stream.read(), original)
        self.assertEqual(MP4(self.path)['\xa9nam'], ['新歌'])
        self.assertFalse(list(self.path.parent.glob('.metadata-*')))

    def test_partial_metadata_save_does_not_damage_original(self):
        original = self.path.read_bytes()

        class BrokenAudio:
            tags = {}

            def __init__(self, path):
                self.path = Path(path)

            def save(self):
                self.path.write_bytes(b'partial failed write')
                raise OSError('disk full')

        with patch.object(downloader, 'MP4', BrokenAudio):
            with self.assertRaises(OSError):
                downloader.update_audio_metadata(self.path, artist='新歌手')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob('.metadata-*')))

    def test_concurrent_lyrics_and_metadata_updates_are_both_retained(self):
        normalize_m4a(self.path)
        barrier = threading.Barrier(2)

        def metadata():
            barrier.wait(timeout=5)
            downloader.update_audio_metadata(self.path, artist='演唱者', album='Music Player')

        def lyrics():
            barrier.wait(timeout=5)
            downloader.update_embedded_lyrics(self.path.name, '[00:01]新歌词')

        with patch.object(downloader, 'MUSIC_DIR', self.path.parent):
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(metadata), pool.submit(lyrics)]
                for future in pending:
                    future.result(timeout=10)
        audio = MP4(self.path)
        self.assertEqual(audio['\xa9ART'], ['演唱者'])
        self.assertEqual(audio['\xa9lyr'], ['[00:01]新歌词'])
        self.assertNotIn('cpil', audio)

    def test_failed_cross_filesystem_copy_preserves_existing_song(self):
        music = self.path.parent / 'library'
        album = music / 'Music Player'
        album.mkdir(parents=True)
        destination = album / '歌手 - 歌名 [BVTEST].m4a'
        original = b'previous playable song'
        destination.write_bytes(original)

        def fail(src, target):
            Path(target).write_bytes(b'partial copy')
            raise OSError('disk full')

        with (patch.object(downloader, 'MUSIC_DIR', music),
              patch.object(downloader.shutil, 'copyfile', side_effect=fail)):
            with self.assertRaises(OSError):
                downloader.tag_and_store(self.path, '歌名', '歌手', 'Music Player', None, 'BVTEST')
        self.assertEqual(destination.read_bytes(), original)
        self.assertTrue(self.path.exists())
        self.assertFalse(list(album.glob('.musicplayer-*')))

    def test_dot_album_cannot_escape_music_directory(self):
        music = self.path.parent / 'library'
        with patch.object(downloader, 'MUSIC_DIR', music):
            rel = downloader.tag_and_store(self.path, '歌名', '歌手', '..', None, 'BVTEST')
        target = (music / rel).resolve()
        self.assertTrue(target.is_relative_to(music.resolve()))
        self.assertEqual(target.parent.name, '未命名')
        self.assertEqual(MP4(target)['\xa9alb'], ['..'])

    def test_historical_repair_is_dry_by_default_and_backs_up_changes(self):
        from scripts.repair_metadata import repair

        normalize_m4a(self.path)
        original = self.path.read_bytes()
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE songs(id INTEGER, file_path TEXT, title TEXT, artist TEXT, album TEXT)')
        conn.execute('INSERT INTO songs VALUES(1, ?, ?, ?, ?)',
                     (self.path.name, '歌名', '演唱者', 'Music Player'))
        self.assertEqual(repair(conn, self.path.parent), (1, 0))
        self.assertEqual(self.path.read_bytes(), original)
        backup = self.path.parent / 'backup'
        self.assertEqual(repair(conn, self.path.parent, backup), (1, 0))
        self.assertEqual((backup / 'music' / self.path.name).read_bytes(), original)
        self.assertEqual(MP4(self.path)['\xa9ART'], ['演唱者'])
        self.assertNotIn('aART', MP4(self.path))
        self.assertNotIn('cpil', MP4(self.path))
        self.assertEqual(MP4(self.path)['\xa9lyr'], ['[00:00.00]保留歌词'])
        self.assertEqual(repair(conn, self.path.parent), (0, 0))


if __name__ == '__main__':
    unittest.main()

class StreamingTests(unittest.TestCase):
    def test_range_head_and_auth(self):
        from fastapi.testclient import TestClient
        from unittest.mock import Mock
        from app import main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'song.m4a'
            path.write_bytes(bytes(range(256)))
            with (patch.object(main, 'MUSIC_DIR', Path(tmp)),
                  patch.object(main, 'get_conn', return_value=Mock()),
                  patch.object(main, 'get_song', return_value={
                      'file_path': path.name, 'title': '歌', 'artist': '人'}),
                  patch.object(main, 'API_TOKEN', 'test-token'),
                  patch.object(main.auth_service, 'enabled', return_value=False)):
                client = TestClient(main.app)
                self.assertEqual(client.get('/api/stream/1').status_code, 401)
                headers = {'X-Api-Token': 'test-token'}
                response = client.get('/api/stream/1', headers={**headers, 'Range': 'bytes=0-1'})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, b'\x00\x01')
                self.assertEqual(response.headers['content-range'], 'bytes 0-1/256')
                self.assertTrue(response.headers['content-disposition'].startswith('inline'))
                response = client.get('/api/stream/1', headers={**headers, 'Range': 'bytes=256-'})
                self.assertEqual(response.status_code, 416)
                response = client.head('/api/stream/1', headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'')
                self.assertEqual(response.headers['content-length'], '256')
