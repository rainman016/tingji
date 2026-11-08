import copy
import json
import unittest
from unittest.mock import Mock
from summary_format import parse, render, generate_checked, balance_points
from summary_validation import validate_summary


class SummaryFormatTests(unittest.TestCase):
    def setUp(self):
        self.data = {'overview': '这是中文概览', 'points': [{'text': '需要核对实现结果', 'time': '00:00:10'}],
                     'quotes': [{'text': 'We should verify the entire project carefully.', 'time': '00:00:10', 'speaker': 'Host'}], 'actions': []}

    def test_chinese_paraphrases_and_literal_quotes_are_rendered_separately(self):
        text = render(json.dumps(self.data))
        checked = validate_summary(text, {'speakers': {'0': 'Host'}, 'segments': [
            {'start_ms': 10000, 'end_ms': 20000, 'speaker': '0', 'text': 'We should verify the entire project carefully.'}]})
        self.assertEqual(checked['unverified_quotes'], 0)
        self.assertEqual(checked['points_with_sources'], 1)
        self.assertIn('## 原文回看', text)

    def test_english_core_points_and_invalid_shapes_are_rejected(self):
        for change in ('english', 'too_many', 'invalid_time', 'missing'):
            with self.subTest(change=change):
                data = copy.deepcopy(self.data)
                if change == 'english': data['points'][0]['text'] = 'English quote instead of a Chinese point'
                elif change == 'too_many': data['points'] *= 8
                elif change == 'invalid_time': data['points'][0]['time'] = '00:99:00'
                else: data.pop('overview')
                with self.assertRaises(ValueError): parse(json.dumps(data))

    def test_meeting_actions_preserve_explicit_missing_values(self):
        self.data['actions'] = [{'text': '补充测试', 'time': '00:00:10', 'owner': '未提及', 'deadline': '未提及'}]
        text = render(json.dumps(self.data))
        self.assertIn('负责人：未提及；期限：未提及', text)

    def test_chinese_points_allow_english_names_and_quoted_terms(self):
        self.data['points'][0]['text'] = 'CI 检查用于约束 "模块边界"，避免导入错误。'
        self.assertIn('"模块边界"', render(json.dumps(self.data)))

    def generate(self, complete, grammar):
        return generate_checked(complete, grammar, style='通用摘要', merge=False,
                                times={'00:00:10'}, report=Mock())

    def response(self, data):
        return {'choices': [{'text': json.dumps(data), 'finish_reason': 'stop'}]}

    def test_valid_output_skips_expensive_grammar(self):
        complete, grammar = Mock(return_value=self.response(self.data)), Mock()
        self.assertEqual(parse(self.generate(complete, grammar)), self.data)
        grammar.assert_not_called()
        complete.assert_called_once_with(None)

    def test_invalid_output_gets_one_constrained_repair(self):
        invalid = copy.deepcopy(self.data)
        invalid['points'][0]['time'] = '00:09:00'
        complete, grammar = Mock(side_effect=[self.response(invalid), self.response(self.data)]), Mock(return_value='grammar')
        self.assertEqual(parse(self.generate(complete, grammar)), self.data)
        self.assertEqual(complete.call_args_list[1].args, ('grammar',))
        grammar.assert_called_once_with()

    def test_bad_repair_is_rejected_and_inference_errors_are_not_retried(self):
        complete, grammar = Mock(return_value=self.response({})), Mock()
        with self.assertRaises(ValueError): self.generate(complete, grammar)
        self.assertEqual(complete.call_count, 2)
        complete = Mock(side_effect=RuntimeError('vulkan device lost'))
        grammar.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'vulkan'): self.generate(complete, grammar)
        complete.assert_called_once_with(None)
        grammar.assert_not_called()

    def test_merge_cannot_drop_all_later_blocks(self):
        blocks = []
        for i in range(6):
            data = copy.deepcopy(self.data)
            data['points'] = [{'text': f'主题{i}需要核对实现结果', 'time': f'00:{i * 9:02d}:10'}]
            blocks.append(json.dumps(data))
        result = parse(balance_points(blocks[0], blocks))
        self.assertEqual(len(result['points']), 6)
        self.assertEqual(result['points'][-1]['time'], '00:45:10')
        self.assertEqual(result['points'], [parse(block)['points'][0] for block in blocks])

    def test_long_documents_keep_both_ends_with_bounded_existing_points(self):
        blocks = []
        for i in range(12):
            data = copy.deepcopy(self.data)
            data['points'] = [{'text': f'主题{i}需要核对实现结果', 'time': f'00:{i * 4:02d}:10'}]
            blocks.append(json.dumps(data))
        points = parse(balance_points(blocks[0], blocks))['points']
        self.assertEqual(len(points), 7)
        self.assertEqual(points[0], parse(blocks[0])['points'][0])
        self.assertEqual(points[-1], parse(blocks[-1])['points'][0])
        self.assertTrue(all(row in [parse(block)['points'][0] for block in blocks] for row in points))
