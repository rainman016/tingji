import unittest

from summary_validation import validate_summary


class SummaryValidationTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'speakers': {'0': 'Host', '1': 'Guest'}, 'segments': [
            {'start_ms': 10000, 'end_ms': 20000, 'speaker': '1', 'text': 'We should verify the entire project carefully.'},
            {'start_ms': 30000, 'end_ms': 40000, 'speaker': '0', 'text': 'The remaining material is also important.'}]}

    def test_key_points_report_missing_sources_without_claiming_semantic_verification(self):
        checked = validate_summary('- 核对整个项目 [00:00:10]\n- 没有来源的观点', self.doc)
        self.assertEqual(checked['key_points'], 2)
        self.assertEqual(checked['points_with_sources'], 1)
        self.assertEqual(checked['points_need_sources'], 1)

    def test_verbatim_quote_repairs_time_and_speaker_from_source(self):
        checked = validate_summary('[00:00:11] Host：We should verify the entire project carefully.', self.doc)
        self.assertEqual(checked['text'], '[00:00:10] Guest：We should verify the entire project carefully.')
        self.assertEqual(checked['corrected_quotes'], 1)

    def test_numbered_quote_is_checked_and_corrected(self):
        checked = validate_summary('2. [00:00:11] Host：We should verify the entire project carefully.', self.doc)
        self.assertEqual(checked['text'], '[00:00:10] Guest：We should verify the entire project carefully.')
        self.assertEqual(checked['corrected_quotes'], 1)

    def test_translation_is_explicitly_paraphrase_without_unverified_clickable_time(self):
        checked = validate_summary('[00:00:10-00:00:20] Guest：应该认真检查整个项目。', self.doc)
        self.assertIn('转述（时间待核对', checked['text'])
        self.assertNotIn('[00:', checked['text'])
        self.assertEqual(checked['unverified_quotes'], 1)

    def test_ambiguous_repeated_quote_does_not_invent_an_anchor(self):
        self.doc['segments'].append(dict(self.doc['segments'][0], start_ms=50000, end_ms=60000))
        checked = validate_summary('[00:00:01] Person：We should verify the entire project carefully.', self.doc)
        self.assertEqual(checked['unverified_quotes'], 1)

    def test_known_timestamp_resolves_an_otherwise_repeated_quote(self):
        self.doc['segments'].append(dict(self.doc['segments'][0], start_ms=50000, end_ms=60000))
        checked = validate_summary('[00:00:50] Guest：We should verify the entire project carefully.', self.doc)
        self.assertNotIn('时间待核对', checked['text'])

    def test_inline_unknown_time_is_flagged_and_valid_time_preserved(self):
        checked = validate_summary('- Key point [00:00:10]\n- Other point [00:00:09]', self.doc)
        self.assertIn('[00:00:10]', checked['text'])
        self.assertNotIn('[00:00:09]', checked['text'])

    def test_literal_punctuation_and_case_changes_are_safe(self):
        checked = validate_summary('- [00:00:10] Host: we SHOULD verify the entire project carefully!', self.doc)
        self.assertIn('[00:00:10] Guest', checked['text'])

    def test_quote_spanning_adjacent_segments_uses_first_source_anchor(self):
        self.doc['segments'].insert(1, {'start_ms': 20000, 'end_ms': 25000, 'speaker': '1',
                                       'text': 'Then we can publish the result.'})
        checked = validate_summary('[00:00:11] Host: We should verify the entire project carefully. Then we can publish the result.', self.doc)
        self.assertIn('[00:00:10] Guest', checked['text'])
        self.assertEqual(checked['unverified_quotes'], 0)

    def test_simultaneous_identical_words_from_two_speakers_remain_ambiguous(self):
        self.doc['segments'].append(dict(self.doc['segments'][0], speaker='0'))
        checked = validate_summary('[00:00:10] Guest: We should verify the entire project carefully.', self.doc)
        self.assertEqual(checked['unverified_quotes'], 1)

    def test_english_word_boundaries_cannot_change_the_quoted_meaning(self):
        self.doc['segments'][0]['text'] = 'The backup is now here and ready.'
        checked = validate_summary('[00:00:10] Guest: The backup is nowhere and ready.', self.doc)
        self.assertEqual(checked['unverified_quotes'], 1)

    def test_chinese_literal_substring_remains_matchable(self):
        self.doc['segments'][0]['text'] = '接下来我们应该认真检查整个项目的全部材料再确认结果。'
        checked = validate_summary('[00:00:11] Host：应该认真检查整个项目的全部材料', self.doc)
        self.assertIn('[00:00:10] Guest', checked['text'])
