import customtkinter as ctk
import unittest
from unittest.mock import Mock

from summary_reader import SummaryReader, blocks


class SummaryReaderTests(unittest.TestCase):
    def setUp(self):
        self.window = ctk.CTk()
        self.window.withdraw()
        self.follow = Mock()
        self.reader = SummaryReader(self.window, self.follow)
        self.reader.pack()

    def tearDown(self):
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()

    def test_read_only_presentation_removes_markup_but_keeps_content(self):
        source = '## 概览\n保留 **重要信息**，不能删减。\n\n- 第一条内容。\n> 原文引用。'
        self.reader.render(source, set(), 'one')
        text = self.reader.text.get('1.0', 'end-1c')
        self.assertEqual(text, '概览\n保留 重要信息，不能删减。\n•  第一条内容。\n原文引用。\n')
        self.assertTrue(self.reader.text.tag_ranges('strong'))
        self.reader.text.insert('1.0', 'unexpected edit')
        self.assertEqual(self.reader.text.get('1.0', 'end-1c'), text)

    def test_only_verified_times_are_clickable_and_keyboard_accessible(self):
        self.reader.render('- 可回看 [00:00:09]\n- 待核对 [09:00:00]', {'00:00:09'}, 'one')
        valid = self.reader.text.search('[00:00:09]', '1.0')
        invalid = self.reader.text.search('[09:00:00]', '1.0')
        self.reader._follow_at(invalid)
        self.follow.assert_not_called()
        self.reader.text.mark_set('insert', '1.0')
        self.reader._move_citation(1)
        self.reader._follow_at(self.reader.text.index('insert'))
        self.follow.assert_called_once_with(9000)
        self.assertEqual(self.reader.text.index('insert'), valid)

    def test_plain_text_and_numbered_lists_remain_readable(self):
        self.assertEqual(list(blocks('#hashtag is text\n1. 第一条\n2、 第二条')),
                         [('paragraph', '#hashtag is text'), ('point', '•  第一条'), ('point', '•  第二条')])
        self.reader.render('English words stay together.\n中文段落按文字换行。', set())
        self.assertNotIn('cjk', self.reader.text.tag_names('1.0'))
        self.assertIn('cjk', self.reader.text.tag_names('2.0'))


if __name__ == '__main__':
    unittest.main()
