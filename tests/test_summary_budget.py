import unittest

from core import new_document, summary_chunks, transcript_text
from local_worker import budget_summary_chunks, pack_summary_partials


class SummaryBudgetTests(unittest.TestCase):
    def test_merge_uses_whole_context_instead_of_repeated_pairwise_loss(self):
        partials = ['first topic', 'middle topic', 'final topic']
        self.assertEqual(pack_summary_partials(partials, lambda _: True), [partials])

    def test_merge_groups_keep_all_topics_and_obey_budget(self):
        partials = ['first topic', 'middle topic', 'another topic', 'final topic']
        groups = pack_summary_partials(partials, lambda text: len(text) <= 28)
        self.assertEqual([part for group in groups for part in group], partials)
        self.assertTrue(all(len('\n\n'.join(group)) <= 28 for group in groups))
        self.assertLess(len(groups), len(partials))

    def test_merge_cannot_loop_when_no_pair_fits(self):
        with self.assertRaises(ValueError): pack_summary_partials(['1234', '5678'], lambda text: len(text) <= 4)

    def document(self, text, count=1):
        doc = new_document('fixture.wav')
        doc['speakers'] = {'0': 'Speaker'}
        doc['segments'] = [{'start_ms': i * 1000, 'end_ms': (i + 1) * 1000,
                            'speaker': '0', 'text': text + str(i)} for i in range(count)]
        return doc

    def test_english_uses_larger_context_without_changing_material(self):
        doc = self.document('A clear English sentence about the project. ' * 8, 100)
        chunks = budget_summary_chunks(doc, lambda text: len(text.split()) <= 4000)
        self.assertLess(len(chunks), len(summary_chunks(doc, 2400)))
        self.assertEqual(''.join(chunks), transcript_text(doc))
        self.assertTrue(all(len(text.split()) <= 4000 for text in chunks))

    def test_dense_chinese_is_split_to_actual_budget_without_losing_tail(self):
        doc = self.document('中文材料逐字保存。' * 200, 10)
        chunks = budget_summary_chunks(doc, lambda text: len(text) <= 3500)
        self.assertTrue(all(len(text) <= 3500 for text in chunks))
        self.assertEqual(''.join(chunks), transcript_text(doc))
        self.assertIn('[00:00:09]', chunks[-1])

    def test_single_long_line_and_unicode_are_not_dropped(self):
        doc = self.document('😀 continuous text ' * 2000)
        chunks = budget_summary_chunks(doc, lambda text: len(text.encode('utf-8')) <= 1700)
        self.assertEqual(''.join(chunks), transcript_text(doc))
        self.assertTrue(all(len(text.encode('utf-8')) <= 1700 for text in chunks))

    def test_cannot_fit_any_material_fails_without_silent_truncation(self):
        with self.assertRaisesRegex(ValueError, '上下文'):
            budget_summary_chunks(self.document('retained text'), lambda _: False)

    def test_empty_transcript_has_no_material(self):
        self.assertEqual(budget_summary_chunks(new_document('empty.wav'), lambda _: True), [])

    def test_small_tails_are_packed_with_following_material(self):
        doc = self.document('x' * 950, 30)
        chunks = budget_summary_chunks(doc, lambda text: len(text) <= 10000)
        self.assertLessEqual(len(chunks), 4)
        self.assertEqual(''.join(chunks), transcript_text(doc))


if __name__ == '__main__':
    unittest.main()
