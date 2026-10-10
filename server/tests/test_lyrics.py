import unittest
from unittest.mock import MagicMock, patch

from app import lyrics
from app.lyrics import decode_lyrics_file, lrc_to_plain


class LyricsFileTests(unittest.TestCase):
    def test_lrc_to_plain_removes_timestamps_and_metadata(self):
        text = "[ar:歌手]\n[00:01.20]第一句\n[00:03.00][00:04.00]第二句"
        self.assertEqual(lrc_to_plain(text), "第一句\n第二句")

    def test_utf8_and_gb18030_are_supported(self):
        self.assertEqual(decode_lyrics_file("[00:01]你好".encode()), "[00:01]你好\n")
        self.assertEqual(
            decode_lyrics_file("[00:01]中文歌词".encode("gb18030")),
            "[00:01]中文歌词\n",
        )

    def test_empty_and_oversized_files_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "空"):
            decode_lyrics_file(b"")
        with self.assertRaisesRegex(ValueError, "1 MB"):
            decode_lyrics_file(b"x" * (1024 * 1024 + 1))


class LyricsMatchingTests(unittest.TestCase):
    def setUp(self):
        lyrics._lyrics_cache.clear()

    def tearDown(self):
        lyrics._lyrics_cache.clear()

    @staticmethod
    def response(data, status=200):
        result = MagicMock(status_code=status)
        result.json.return_value = data
        return result

    def client(self, responses):
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.side_effect = responses
        return client

    def test_clean_preserves_quoted_song_and_cover_substring(self):
        self.assertEqual(lyrics._clean('《有点甜》 (cover)'), '有点甜')
        self.assertEqual(lyrics._clean('Recover'), 'Recover')

    def test_empty_or_instrumental_lrclib_results_allow_fallback(self):
        client = self.client([self.response([
            {'trackName': 'Song', 'instrumental': False, 'plainLyrics': ''},
            {'trackName': 'Song', 'instrumental': True, 'plainLyrics': 'Instrumental'},
        ])])
        with patch.object(lyrics.httpx, 'Client', return_value=client), patch.object(lyrics, '_throttle'):
            self.assertIsNone(lyrics._lrclib('Song'))
        with patch.object(lyrics, '_lrclib', return_value=('', '', 'lrclib')), \
             patch.object(lyrics, '_netease', return_value=('words', '[00:01]words', 'netease')) as fallback:
            result = lyrics.fetch_lyrics('Song')
        self.assertEqual(result['source'], 'netease')
        fallback.assert_called_once_with('Song', '')

    def test_lrclib_ignores_empty_best_candidate_and_derives_plain(self):
        client = self.client([self.response([
            {'trackName': 'Song', 'plainLyrics': ''},
            {'trackName': 'Song', 'syncedLyrics': '[00:01]words'},
            {'trackName': 'unrelated', 'syncedLyrics': '[00:01]wrong'},
        ])])
        with patch.object(lyrics.httpx, 'Client', return_value=client), patch.object(lyrics, '_throttle'):
            self.assertEqual(lyrics._lrclib('Song', duration=123), ('words', '[00:01]words', 'lrclib'))
        self.assertNotIn('duration', client.get.call_args.kwargs['params'])

    def test_numbered_part_title_has_search_only_fallback(self):
        def lookup(track, artist, duration):
            return ('words', '[00:01]words', 'lrclib') if track == '有点甜' else None
        with patch.object(lyrics, '_lrclib', side_effect=lookup) as provider:
            result = lyrics.fetch_lyrics('5 有点甜', '翻唱歌手')
        self.assertEqual(result['source'], 'lrclib')
        self.assertEqual([c.args[0] for c in provider.call_args_list], ['5 有点甜', '5 有点甜', '有点甜'])
        self.assertIn(('5 有点甜', '翻唱歌手', 0), lyrics._lyrics_cache)

    def test_empty_artist_does_not_duplicate_queries(self):
        with patch.object(lyrics, '_lrclib', return_value=None) as lrclib, \
             patch.object(lyrics, '_netease', return_value=None) as netease, \
             patch.object(lyrics, '_qq', return_value=None) as qq:
            self.assertFalse(lyrics.fetch_lyrics('Song')['source'])
        self.assertEqual([p.call_count for p in (lrclib, netease, qq)], [1, 1, 1])

    def test_netease_abroad_response_uses_cloudsearch_then_lyric(self):
        client = self.client([
            self.response({'code': 200, 'abroad': True, 'result': 'not a songs object'}),
            self.response({'lrc': {'lyric': '[00:01]words'}}),
        ])
        client.post.return_value = self.response({'code': 200, 'result': {'songs': [
            {'name': 'Song', 'id': 123}, {'name': 'unrelated', 'id': 456},
        ]}})
        with patch.object(lyrics.httpx, 'Client', return_value=client), patch.object(lyrics, '_throttle') as throttle:
            self.assertEqual(lyrics._netease('Song'), ('words', '[00:01]words', 'netease'))
        self.assertEqual(throttle.call_count, 3)
        client.post.assert_called_once_with(lyrics.NETEASE_CLOUDSEARCH, data={
            's': 'Song', 'type': 1, 'offset': 0, 'limit': 5,
        })
        self.assertEqual(client.get.call_args.kwargs['params']['id'], 123)
        client.__exit__.assert_called_once()

    def test_netease_valid_old_search_does_not_request_cloudsearch(self):
        client = self.client([
            self.response({'result': {'songs': [{'name': 'Song', 'id': 123}]}}),
            self.response({'lrc': {'lyric': '[00:01]words'}}),
        ])
        with patch.object(lyrics.httpx, 'Client', return_value=client), patch.object(lyrics, '_throttle'):
            self.assertEqual(lyrics._netease('Song'), ('words', '[00:01]words', 'netease'))
        client.post.assert_not_called()

    def test_netease_cloudsearch_failure_or_unrelated_title_is_not_a_hit(self):
        for cloud_response in (self.response({}, 500), self.response({'result': 'invalid'}),
                               self.response({'result': {'songs': [{'name': 'unrelated', 'id': 456}]}})):
            with self.subTest(response=cloud_response):
                client = self.client([self.response({'result': 'abroad string'})])
                client.post.return_value = cloud_response
                with patch.object(lyrics.httpx, 'Client', return_value=client), patch.object(lyrics, '_throttle'):
                    self.assertIsNone(lyrics._netease('Song'))
                self.assertEqual(client.get.call_count, 1)

    def test_negative_cache_expires_and_manual_force_skips_it(self):
        miss = {'plain': '', 'lrc': '', 'source': ''}
        hit = {'plain': 'words', 'lrc': 'words', 'source': 'lrclib'}
        with patch.object(lyrics, '_fetch_lyrics_uncached', side_effect=[miss, miss, hit]) as lookup, \
             patch.object(lyrics.time, 'monotonic', return_value=100) as clock:
            self.assertEqual(lyrics.fetch_lyrics('Song'), miss)
            lyrics.fetch_lyrics('Song')
            self.assertEqual(lookup.call_count, 1)
            clock.return_value = 401
            self.assertEqual(lyrics.fetch_lyrics('Song'), miss)
            self.assertEqual(lyrics.fetch_lyrics('Song', force=True), hit)
            self.assertEqual(lookup.call_count, 3)

    def test_cache_is_bounded_and_return_values_cannot_mutate_it(self):
        hit = {'plain': 'words', 'lrc': 'words', 'source': 'lrclib'}
        with patch.object(lyrics, '_CACHE_LIMIT', 2), patch.object(lyrics, '_fetch_lyrics_uncached', return_value=hit):
            lyrics.fetch_lyrics('A')['plain'] = 'changed'
            self.assertEqual(lyrics.fetch_lyrics('A')['plain'], 'words')
            lyrics.fetch_lyrics('B')
            lyrics.fetch_lyrics('C')
        self.assertEqual(len(lyrics._lyrics_cache), 2)
        self.assertNotIn(('A', '', 0), lyrics._lyrics_cache)


if __name__ == "__main__":
    unittest.main()
