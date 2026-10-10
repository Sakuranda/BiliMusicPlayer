"""Synthetic regression cases, not a substitute for the private 40-song fixture."""
import json
import unittest

from app.parser import known_artists_from_songs, parse_metadata, parse_title


class ParserTests(unittest.TestCase):
    def test_quoted_song_words_are_not_deleted_as_noise(self):
        for song in ("空心", "烟花", "回忆", "温柔", "恋爱", "Cover Me", "Cover", "音乐"):
            with self.subTest(song=song):
                hit = parse_metadata(f"《{song}》【小雾uya】", "剪辑号")
                self.assertEqual((hit['title'], hit['artist']), (song, '小雾uya'))

    def test_uploader_is_never_assumed_to_be_the_singer(self):
        for uploader in ('某某切片', '录播存档', '普通上传账号', '周杰伦'):
            with self.subTest(uploader=uploader):
                hit = parse_metadata('《晴天》', uploader)
                self.assertEqual(hit['artist'], '未知歌手')
                self.assertTrue(hit['needs_review'])
                self.assertEqual(hit['artist_source'], 'unknown')

    def test_original_creator_tags_do_not_become_performers(self):
        hit = parse_metadata('《晴天》【翻唱】', '剪辑号', ['周杰伦', '翻唱'])
        self.assertEqual(hit['artist'], '未知歌手')
        self.assertEqual(hit['title'], '晴天')

    def test_explicit_original_credit_excludes_unfamiliar_creator(self):
        hit = parse_metadata('《远方》', '剪辑号', ['新原唱'], '原唱：新原唱', ['新原唱'])
        self.assertEqual(hit['artist'], '未知歌手')

    def test_explicit_performance_can_be_by_a_known_original_artist(self):
        hit = parse_metadata('《晴天》', '剪辑号', ['周杰伦'], '演唱：周杰伦')
        self.assertEqual(hit['artist'], '周杰伦')
        self.assertGreaterEqual(hit['artist_confidence'], .9)

    def test_description_separates_original_composer_and_performer(self):
        hit = parse_metadata('《晴天》', '剪辑号', ['周杰伦'], '原唱：周杰伦\n作词：作者\n翻唱：小雾uya')
        self.assertEqual(hit['artist'], '小雾uya')
        self.assertEqual(hit['artist_source'], 'description_credit')

    def test_multiple_credits_on_one_line(self):
        for separator in (' ', '，', '；', '\n'):
            with self.subTest(separator=separator):
                title = separator.join(('歌曲：晴天', '演唱：小雾uya', '原唱：周杰伦'))
                hit = parse_metadata(title, '剪辑号')
                self.assertEqual((hit['title'], hit['artist']), ('晴天', '小雾uya'))

    def test_lyrics_title_uses_unique_tag_after_trusted_artist_removed(self):
        for title in ('永远爱你是我说过的承诺', '那一天你是否还记得我', '这句太好听了'):
            with self.subTest(title=title):
                hit = parse_metadata(title, '切片号', ['我们俩', '郭顶', '小雾uya', '翻唱'], known_artists=['小雾uya'])
                self.assertEqual((hit['title'], hit['artist']), ('我们俩', '小雾uya'))
                self.assertEqual(hit['title_source'], 'tag_candidate')

    def test_unknown_tags_are_not_sorted_by_length_to_guess_song(self):
        hit = parse_metadata('永远爱你是我说过的承诺', '切片号', ['短歌名', '很长的未知主播名字', '翻唱'])
        self.assertEqual(hit['title_source'], 'raw_title')
        self.assertEqual(hit['artist'], '未知歌手')
        self.assertTrue(hit['needs_review'])

    def test_single_unknown_tag_artist_is_explicitly_low_confidence(self):
        hit = parse_metadata('《晴天》', '切片号', ['流行音乐'])
        self.assertTrue(hit['needs_review'])
        self.assertLess(hit['artist_confidence'], .8)
        self.assertEqual(known_artists_from_songs([{'artist': hit['artist'], 'metadata': hit}]), [])

    def test_known_artist_matches_case_and_unicode_with_explicit_aliases(self):
        hit = parse_metadata('《星光》', '切片号', ['ａｋｉ'], known_artists={'aki': 'Aki'})
        self.assertEqual(hit['artist'], 'Aki')
        self.assertEqual(hit['artist_source'], 'library_tag')

    def test_no_substring_alias_or_fuzzy_identity_guessing(self):
        for tag in ('阿米娅', 'Akiya', '我想念阿米'):
            hit = parse_metadata('《星光》', '切片号', [tag], known_artists=['阿米', 'Aki'])
            self.assertNotIn(hit['artist'], ('阿米', 'Aki'))
            self.assertTrue(hit['needs_review'])

    def test_duet_is_preserved_and_latin_x_is_not_a_separator(self):
        for credit, expected in (('呜米x咩栗', '呜米 / 咩栗'), ('呜米 × 咩栗', '呜米 / 咩栗'), ('Alex & Max', 'Alex / Max'), ('Alex', 'Alex')):
            with self.subTest(credit=credit):
                self.assertEqual(parse_metadata(f'【{credit}】《烟花》')['artist'], expected)

    def test_duet_tags_corroborate_the_whole_credit(self):
        hit = parse_metadata('【呜米x咩栗】《烟花》', tags=['呜米', '咩栗'], known_artists=['呜米', '咩栗'])
        self.assertEqual(hit['artist'], '呜米 / 咩栗')
        self.assertFalse(hit['needs_review'])

    def test_credit_words_inside_song_names_are_not_performer_evidence(self):
        for song in ('Stand by Me', 'Cover by Night', '演唱：世界'):
            with self.subTest(song=song):
                hit = parse_metadata(f'《{song}》')
                self.assertEqual(hit['title'], song)
                self.assertEqual(hit['artist'], '未知歌手')

    def test_square_bracket_song_with_trusted_performer_tag(self):
        hit = parse_metadata('【下雨天】', tags=['下雨天', '阿梓'], known_artists=['阿梓'])
        self.assertEqual((hit['title'], hit['artist']), ('下雨天', '阿梓'))

    def test_unconfirmed_brackets_and_residuals_remain_reviewable(self):
        for raw in ('【阿晴歌】《星空》', '【阿晴翻唱】《星空》', '【精修晴】《星空》', '甜美女声翻唱《星空》 -Aki'):
            with self.subTest(raw=raw):
                hit = parse_metadata(raw)
                self.assertLess(hit['artist_confidence'], .8)
                self.assertTrue(hit['needs_review'])
                self.assertEqual(known_artists_from_songs([{'artist': hit['artist'], 'metadata': hit}]), [])

    def test_production_and_genre_brackets_are_not_performers(self):
        for label in ('双女', '双女BE', '原创说唱', '二次元爵士 | 重制', '女声 / 翻唱'):
            with self.subTest(label=label):
                hit = parse_metadata(f'【{label}】《星空》', tags=[label, '星空'])
                self.assertEqual(hit['artist'], '未知歌手')
                self.assertTrue(hit['needs_review'])

    def test_independent_tag_or_trusted_name_supports_a_bracket(self):
        for kwargs in ({'tags': ['Aki']}, {'known_artists': ['Aki']}, {'description': '演唱：Aki'}):
            with self.subTest(kwargs=kwargs):
                hit = parse_metadata('【Aki】《星空》', **kwargs)
                self.assertEqual(hit['artist'], 'Aki')
                self.assertGreaterEqual(hit['artist_confidence'], .8)
                self.assertFalse(hit['needs_review'])

    def test_unconfirmed_role_suffix_is_not_blindly_removed(self):
        hit = parse_metadata('【阿晴翻唱】《星空》', tags=['阿晴Queen'])
        self.assertNotEqual(hit['artist'], '阿晴')
        self.assertLess(hit['artist_confidence'], .8)
        self.assertTrue(hit['needs_review'])

    def test_decorative_song_brackets_and_fullwidth_separator(self):
        for opening, closing in (('〖', '〗'), ('《', '》'), ('「', '」')):
            with self.subTest(opening=opening):
                hit = parse_metadata(f'{opening}星空{closing}｜你在远方想念谁～【Aki】', tags=['Aki'])
                self.assertEqual((hit['title'], hit['artist']), ('星空', 'Aki'))
                self.assertFalse(hit['needs_review'])
        hit = parse_metadata('在雨天唱一首〖星空〗｜Aki', known_artists=['Aki'])
        self.assertEqual((hit['title'], hit['artist']), ('星空', 'Aki'))
        self.assertGreaterEqual(hit['artist_confidence'], .9)

    def test_fullwidth_separator_terminates_credits(self):
        hit = parse_metadata('〖星空〗｜covered by Aki｜2026现场')
        self.assertEqual((hit['title'], hit['artist']), ('星空', 'Aki'))
        hit = parse_metadata('演唱：Aki〖星空〗')
        self.assertEqual((hit['title'], hit['artist']), ('星空', 'Aki'))

    def test_common_title_layouts(self):
        cases = [
            ('【翻唱】夜に駆ける / YOASOBI【沙花叉クロヱ】', '夜に駆ける', '沙花叉クロヱ'),
            ('早稻叽 - One Last Kiss', 'One Last Kiss', '早稻叽'),
            ('恋人-小雾uya', '恋人', '小雾uya'),
            ('雾氧Uo《说好的幸福呢》', '说好的幸福呢', '雾氧Uo'),
            ('《晴天》 cover by Alex', '晴天', 'Alex'),
            ('《晴天》by Alex', '晴天', 'Alex'),
            ('周杰伦 - 晴天', '晴天', '周杰伦'),
        ]
        for raw, title, artist in cases:
            with self.subTest(raw=raw):
                self.assertEqual(parse_title(raw, '切片号'), (title, artist))

    def test_song_punctuation_and_by_word_are_preserved(self):
        for title in ('Anti-Hero', 'Stand by Me', '我们俩', '说好的幸福呢', 'One Last Kiss'):
            with self.subTest(title=title):
                hit = parse_metadata(title)
                self.assertEqual(hit['title'], title)
                self.assertEqual(hit['artist'], '未知歌手')

    def test_long_uncredited_chinese_body_is_not_a_confirmed_song_name(self):
        phrases = (
            '想念变成空气在叹息 多么需要躺在你怀里~',
            '月亮照进清澈的河水远处山林传来悠扬钟声',
            '月亮照进清澈的河水 远处传来钟声',
            '月亮照进清澈的河水～',
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                hit = parse_metadata(f'{phrase}【Aki】', tags=['Aki', '流行音乐'])
                self.assertEqual(hit['title'], phrase)
                self.assertLess(hit['title_confidence'], .8)
                self.assertTrue(hit['needs_review'])

    def test_clear_song_evidence_overrides_long_body_uncertainty(self):
        title = '月亮照进清澈的河水远处山林传来悠扬钟声'
        for raw, tags in ((f'《{title}》【Aki】', ['Aki']), (f'{title}【Aki】', ['Aki', title])):
            with self.subTest(raw=raw):
                hit = parse_metadata(raw, tags=tags)
                self.assertEqual(hit['title'], title)
                self.assertGreaterEqual(hit['title_confidence'], .9)
                self.assertFalse(hit['needs_review'])

    def test_long_english_title_keeps_existing_confidence(self):
        title = 'The Beautiful Song About A Thousand Stars Beyond The Horizon'
        hit = parse_metadata(f'{title}【Aki】', tags=['Aki'])
        self.assertEqual(hit['title'], title)
        self.assertGreaterEqual(hit['title_confidence'], .8)
        self.assertFalse(hit['needs_review'])

    def test_promotional_residual_and_brackets_are_not_singers(self):
        for title in ('《烟花》这句太好听了', '【高能现场】《烟花》', '《烟花》开口惊艳所有人'):
            with self.subTest(title=title):
                self.assertEqual(parse_metadata(title)['artist'], '未知歌手')

    def test_unconfirmed_short_residual_is_never_trusted_automatically(self):
        hit = parse_metadata('《烟花》神秘短句')
        self.assertTrue(hit['needs_review'])
        self.assertLess(hit['artist_confidence'], .8)

    def test_artist_is_not_removed_from_real_song_name(self):
        hit = parse_metadata('【阿米】《我想念阿米》')
        self.assertEqual(hit['title'], '我想念阿米')

    def test_conflicting_performer_evidence_requires_review(self):
        hit = parse_metadata('《晴天》【Alice】', tags=['Alice'], description='演唱：Betty')
        self.assertTrue(hit['needs_review'])
        self.assertLess(hit['artist_confidence'], .8)
        self.assertTrue(any('歌手存在冲突' in reason for reason in hit['reasons']))

    def test_multiple_songs_are_preserved_as_medley_with_review(self):
        hit = parse_metadata('【小雾】《悬溺》x《空心》')
        self.assertEqual(hit['title'], '悬溺 / 空心')
        self.assertTrue(hit['needs_review'])

    def test_description_song_credit_does_not_lose_quote_contents(self):
        hit = parse_metadata('今天给大家唱一首歌', description='歌名：《我们俩》\n演唱：小雾')
        self.assertEqual((hit['title'], hit['artist']), ('我们俩', '小雾'))

    def test_tag_order_has_no_effect(self):
        tags = ['小雾uya', '晴天', '周杰伦', '翻唱']
        args = ('那一天你是否还记得我', '切片号')
        self.assertEqual(parse_metadata(*args, tags, known_artists=['小雾uya']), parse_metadata(*args, list(reversed(tags)), known_artists=['小雾uya']))

    def test_empty_input_and_non_string_tags_are_safe(self):
        hit = parse_metadata('', tags=[None, 1, '', '音乐'])
        self.assertEqual((hit['title'], hit['artist']), ('未知歌曲', '未知歌手'))
        self.assertTrue(hit['needs_review'])


class TrustedArtistTests(unittest.TestCase):
    def test_legacy_and_fallback_artists_do_not_poison_library(self):
        rows = [
            {'artist': '切片UP'},
            {'artist': '切片UP', 'metadata': {'artist_source': 'uploader', 'artist_confidence': 1}},
            {'artist': '未知歌手', 'metadata': {'artist_source': 'manual'}},
            {'artist': '切片UP', 'metadata': 'invalid'},
            {'artist': '切片UP', 'metadata': '[]'},
        ]
        self.assertEqual(known_artists_from_songs(rows), [])

    def test_manual_and_strong_credits_can_teach_performer_names(self):
        hit = parse_metadata('歌词句子', description='演唱：小雾')
        hit['needs_review'] = True  # An uncertain song title must not discard a clear artist credit.
        self.assertEqual(known_artists_from_songs([
            {'artist': '小雾', 'metadata': hit},
            {'artist': 'Alice / Betty', 'metadata': json.dumps({'artist_source': 'manual'})},
        ]), ['小雾', 'Alice', 'Betty'])

    def test_conflicting_artists_are_not_reused(self):
        hit = parse_metadata('《晴天》【Alice】', tags=['Alice'], description='演唱：Betty')
        self.assertEqual(known_artists_from_songs([{'artist': hit['artist'], 'metadata': hit}]), [])

    def test_trusted_duet_members_do_not_alias_to_the_duet(self):
        index = known_artists_from_songs([{'artist': '呜米 / 咩栗', 'metadata': {'artist_source': 'manual'}}])
        hit = parse_metadata('《烟花》', tags=['呜米'], known_artists=index)
        self.assertEqual(hit['artist'], '呜米')


if __name__ == '__main__':
    unittest.main()
