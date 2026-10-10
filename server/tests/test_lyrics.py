import unittest

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


if __name__ == "__main__":
    unittest.main()
