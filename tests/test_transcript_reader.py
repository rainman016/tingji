import tkinter as tk
from types import SimpleNamespace
import unittest

from transcript_reader import TranscriptReader


def segment(text, start=0, speaker="0"):
    return {"start_ms": start, "end_ms": start + 1000, "speaker": speaker, "text": text}


class TranscriptReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0)
        self.root.geometry("720x480")
        self.selections = []
        self.plays = []
        self.search_updates = []
        self.reader = TranscriptReader(
            self.root, on_select=self.selections.append, on_play=self.plays.append,
            on_search_change=lambda current, total: self.search_updates.append((current, total)),
        )
        self.reader.pack(fill="both", expand=True)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def active_match(self):
        ranges = self.reader.text.tag_ranges("current_match")
        return self.reader.text.get(*ranges) if ranges else ""

    def test_document_is_read_only_and_selection_notifies_without_playing(self):
        items = [segment("第一段正文。"), segment("Second paragraph.", 65000, "1")]
        self.reader.set_document(items, {"0": "林晓", "1": "Alex"})
        content = self.reader.text.get("1.0", "end-1c")
        self.assertIn("[00:01:05]  Alex\nSecond paragraph.", content)
        self.assertEqual(self.reader.text.cget("state"), "disabled")
        self.assertEqual(self.reader.segment_count, 2)
        self.assertEqual(self.reader.selected_index, 0)
        self.assertEqual(self.selections, [])
        self.reader.select(1)
        self.assertEqual(self.selections, [1])
        self.assertEqual(self.plays, [])
        self.reader.text.insert("1.0", "不能写入")
        self.assertEqual(self.reader.text.get("1.0", "end-1c"), content)

    def test_literal_chinese_and_english_search_tracks_every_occurrence(self):
        self.reader.set_document(
            [segment("中文目标。TARGET target [x]"), segment("目标 target [x]", 1000)],
            {"0": "target 是说话人名字"},
        )
        self.assertEqual(self.reader.search("目标"), (1, 2))
        self.assertEqual(self.active_match(), "目标")
        self.assertEqual(self.reader.next_match(), (2, 2))
        self.assertEqual(self.reader.selected_index, 1)
        self.assertEqual(self.reader.search("target"), (1, 3))
        self.assertEqual(self.active_match(), "TARGET")
        first_range = self.reader.text.tag_ranges("current_match")
        self.assertEqual(self.reader.next_match(), (2, 3))
        self.assertEqual(self.reader.selected_index, 0)
        self.assertNotEqual(first_range, self.reader.text.tag_ranges("current_match"))
        self.assertEqual(self.active_match(), "target")
        self.assertEqual(self.reader.next_match(), (3, 3))
        self.assertEqual(self.reader.selected_index, 1)
        self.assertEqual(self.reader.next_match(), (1, 3))
        self.assertEqual(self.reader.next_match(-1), (3, 3))
        self.assertEqual(self.reader.search("[x]"), (1, 2))
        self.assertEqual(self.active_match(), "[x]")
        self.assertEqual(self.plays, [])
        self.assertEqual(self.search_updates[-1], (1, 2))

    def test_unicode_prefix_does_not_shift_match_positions(self):
        self.reader.set_document([segment("İstanbul Straße 🌍 中文 TARGET target")], {"0": "A"})
        self.assertEqual(self.reader.search("target"), (1, 2))
        self.assertEqual(self.active_match(), "TARGET")
        self.reader.next_match()
        self.assertEqual(self.active_match(), "target")

    def test_update_rebuilds_search_indices_and_preserves_selected_paragraph(self):
        items = [segment("target before"), segment("target after", 1000), segment("Keep selected", 2000)]
        self.reader.set_document(items, {"0": "A"})
        self.reader.search("target")
        self.reader.next_match()
        self.reader.select(2)
        notified_before = list(self.selections)
        self.reader.update_segment(0, segment("Added line\nMore context\nNo match here"), {"0": "Renamed"})
        self.assertEqual(self.reader.selected_index, 2)
        self.assertEqual(self.selections, notified_before)
        self.assertEqual(self.search_updates[-1], (1, 1))
        self.assertEqual(self.active_match(), "target")
        self.reader.next_match()
        self.assertEqual(self.reader.selected_index, 1)
        self.assertEqual(self.active_match(), "target")
        rendered = self.reader.text.get("1.0", "end-1c")
        self.assertNotIn("target before", rendered)
        self.assertEqual(rendered.count("Renamed"), 3)
        self.assertIn("Keep selected", rendered)
        self.assertEqual(self.reader.text.cget("state"), "disabled")
        self.assertNotIn("timestamp", self.reader.text.tag_names("2.1"))

    def test_clearing_search_removes_all_match_highlights(self):
        self.reader.set_document([segment("one one")], {"0": "A"})
        self.reader.search("one")
        self.assertEqual(self.reader.search(""), (0, 0))
        self.assertEqual(self.reader.text.tag_ranges("search_match"), ())
        self.assertEqual(self.reader.text.tag_ranges("current_match"), ())
        self.assertEqual(self.reader.next_match(), (0, 0))

    def test_body_click_only_selects_timestamp_click_plays(self):
        self.reader.set_document([segment("正文可以选中和复制。")], {"0": "林晓"})
        self.root.update()
        body = self.reader.text.bbox("2.1")
        self.assertIsNotNone(body)
        self.reader._on_click(SimpleNamespace(x=body[0] + 1, y=body[1] + 1))
        self.assertEqual(self.selections, [0])
        self.assertEqual(self.plays, [])
        stamp = self.reader.text.bbox("1.1")
        self.assertIsNotNone(stamp)
        self.reader._on_click(SimpleNamespace(x=1, y=stamp[1] + 1))
        self.assertEqual(self.plays, [])
        self.reader._on_click(SimpleNamespace(x=stamp[0] + 1, y=stamp[1] + 1))
        self.assertEqual(self.plays, [0])
        self.reader.search("正文")
        self.reader.next_match()
        self.assertEqual(self.plays, [0])

    def test_thousand_paragraphs_use_constant_widget_count_and_keep_view(self):
        items = [segment(f"Paragraph {index}: 中文内容 needle {index}", index * 1000) for index in range(1000)]
        self.reader.set_document(items, {"0": "Speaker"})
        self.root.update()
        self.assertEqual(self.reader.segment_count, 1000)
        self.assertEqual(len(self.reader.winfo_children()), 2)
        self.assertEqual(self.reader.search("needle"), (1, 1000))
        self.reader.select(650)
        self.root.update()
        top_before = self.reader.text.get(self.reader.text.index("@0,0"), self.reader.text.index("@0,0") + " lineend")
        self.reader.update_segment(0, segment("Replacement\nwith an extra line"), {"0": "Speaker"})
        self.root.update()
        top_after = self.reader.text.get(self.reader.text.index("@0,0"), self.reader.text.index("@0,0") + " lineend")
        self.assertEqual(self.reader.selected_index, 650)
        self.assertEqual(top_after, top_before)
        self.assertEqual(self.search_updates[-1][1], 999)
        self.assertEqual(len(self.reader.winfo_children()), 2)
        self.assertEqual(self.reader.next_match(-1), (999, 999))
        self.assertEqual(self.reader.selected_index, 999)
        self.assertEqual(self.active_match(), "needle")

    def test_empty_document_resets_selection_and_search(self):
        self.reader.set_document([segment("word")], {"0": "A"})
        self.reader.search("word")
        self.reader.set_document([], {})
        self.assertEqual(self.reader.segment_count, 0)
        self.assertIsNone(self.reader.selected_index)
        self.assertEqual(self.reader.next_match(), (0, 0))
        self.assertEqual(self.reader.text.get("1.0", "end-1c"), "")


if __name__ == "__main__":
    unittest.main()
