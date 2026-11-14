import copy
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
from core import new_document, save_document


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(app, "RESULTS", Path(self.temp.name))
        self.storage.start()
        self.window = app.App()
        self.window.attributes("-alpha", 0)
        self.window.open_example()
        self.window.update()

    def tearDown(self):
        self.window.destroy()
        self.storage.stop()
        self.temp.cleanup()






    def test_timestamp_navigation_and_export_menu_keep_all_formats(self):
        self.window.jump_to_time(19000)
        self.assertEqual(self.window.selected, 2)
        with patch.object(self.window, 'export') as export:
            for index in range(4): self.window.export_menu.invoke(index)
        self.assertEqual([call.args[0] for call in export.call_args_list], ['txt','srt','md','json'])


    def test_search_selects_segment_and_correction_keeps_search_current(self):
        self.window.search_query.set("视频")
        current, total = self.window.apply_search()
        self.assertGreater(total, 1)
        first = self.window.selected
        self.window.search_next()
        self.assertNotEqual(self.window.selected, first)
        index = self.window.selected
        self.window.segment_editor.delete("1.0", "end")
        self.window.segment_editor.insert("1.0", "修订后不再包含关键词。")
        self.window.update()
        self.assertEqual(self.window.doc["segments"][index]["text"], "修订后不再包含关键词。")
        _, remaining = self.window.reader.search("视频")
        self.assertLess(remaining, total)
        self.assertFalse(self.window.doc["summary_stale"])

    def test_segment_assignment_and_merge_are_saved_as_real_speaker_changes(self):
        self.window.reader.select(0)
        choices = self.window.speaker_box.cget("values")
        self.window.assign_speaker(choices[1])
        self.assertEqual(self.window.doc["segments"][0]["speaker"], "1")
        self.assertEqual(self.window.doc["segments"][2]["speaker"], "0")
        with patch.object(app.messagebox, "askyesno", return_value=False):
            self.window.merge_speaker("0")
        self.assertEqual(len(self.window.doc["speakers"]), 2)
        with patch.object(app.messagebox, "askyesno", return_value=True):
            self.window.merge_speaker("0")
        self.assertEqual(len(self.window.doc["speakers"]), 1)
        self.assertTrue(all(row["speaker"] == "0" for row in self.window.doc["segments"]))
        path = Path(self.temp.name) / (self.window.doc["id"] + ".json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["speakers"], self.window.doc["speakers"])

    def test_history_search_and_rename_keep_document_identity(self):
        identity = self.window.doc["id"]
        self.window.doc["language"] = "en"
        self.window.rename_document()
        self.window.rename_value.set("English lecture notes")
        self.window.save_document_name()
        self.window.history_query.set("ENGLISH")
        self.assertEqual([row["id"] for row in self.window.history_docs], [identity])
        self.window.history_query.set("英文")
        self.assertEqual(len(self.window.history_docs), 1)
        self.window.history_query.set("不存在的文稿")
        self.assertEqual(len(self.window.history_docs), 0)
        self.assertEqual(self.window.doc["id"], identity)
        self.window.history_query.set("")
        self.window.history.selection_set(0)
        self.window.open_history()
        self.assertEqual(self.window.doc["title"], "English lecture notes")

    def test_audio_range_and_document_switch_stop_previous_preview(self):
        source = Path(self.temp.name) / "source.wav"
        source.touch()
        self.window.doc["source"] = str(source)
        with patch.object(self.window, "audio") as audio:
            audio.poll.return_value = {"state": "idle", "position_ms": 0, "error": ""}
            self.window.play_segment(1)
            audio.play.assert_called_once_with(str(source), 6000, 21000)
            audio.poll.return_value["state"] = "playing"
            self.window.toggle_audio()
            audio.pause.assert_called_once()
            audio.poll.return_value["state"] = "paused"
            self.window.toggle_audio()
            audio.resume.assert_called_once()
            self.window.load_document(new_document("another.wav"))
            audio.stop.assert_called_once()

    def test_missing_source_can_be_relinked_without_changing_transcript(self):
        original = copy.deepcopy(self.window.doc["segments"])
        self.window.play_current_segment()
        self.assertIn("重新关联", self.window.audio_hint.get())
        source = Path(self.temp.name) / "moved.wav"
        source.touch()
        with patch.object(app.filedialog, "askopenfilename", return_value=str(source)):
            self.window.relink_source()
        self.assertEqual(self.window.doc["source"], str(source))
        self.assertEqual(self.window.doc["segments"], original)

    def test_audio_context_never_runs_before_start_or_after_file_end(self):
        source = Path(self.temp.name) / "source.wav"
        source.touch()
        self.window.doc["source"] = str(source)
        with patch.object(self.window, "audio") as audio:
            self.window.play_segment(0)
            self.assertEqual(audio.play.call_args.args, (str(source), 0, 11500))
            self.window.play_segment(3)
            self.assertEqual(audio.play.call_args.args, (str(source), 24000, 32000))

    def test_background_updates_do_not_replace_history_being_read(self):
        self.window.flush_edits()
        first = copy.deepcopy(self.window.doc)
        second = copy.deepcopy(first)
        second["id"] = new_document("second.mp4")["id"]
        second["title"] = "另一份文稿"
        save_document(second, self.temp.name)
        self.window._job_document_id = first["id"]
        self.window._job_snapshot = first
        self.window.worker = Mock()
        self.window.refresh_history()
        index = next(i for i, row in enumerate(self.window.history_docs) if row["id"] == second["id"])
        self.window.history.selection_clear(0, "end")
        self.window.history.selection_set(index)
        self.window.open_history()
        self.assertEqual(self.window.doc["id"], second["id"])
        first["status"] = "生成摘要"
        save_document(first, self.temp.name)
        self.window.events.put(("document", first))
        self.window.drain_events()
        self.assertEqual(self.window.doc["id"], second["id"])
        first.update(status="已完成", summary="新的摘要")
        save_document(first, self.temp.name)
        self.window.events.put(("done", (first, "")))
        self.window.drain_events()
        self.assertEqual(self.window.doc["id"], second["id"])
        self.window.return_to_task()
        self.assertEqual(self.window.doc["id"], first["id"])
        self.assertEqual(self.window.doc["summary"], "新的摘要")


    def test_switching_segments_cannot_undo_into_previous_segment(self):
        self.window.reader.select(0)
        self.window.segment_editor.insert("end", "这是上一段的编辑")
        self.window.update()
        expected = self.window.doc["segments"][1]["text"]
        self.window.reader.select(1)
        with self.assertRaises(tk.TclError):
            self.window.segment_editor.edit_undo()
        self.assertEqual(self.window.segment_editor.get("1.0", "end-1c"), expected)


    def test_retry_save_then_return_to_task_reads_latest_document(self):
        self.window.flush_edits()
        saved = copy.deepcopy(self.window.doc)
        self.window._job_document_id = saved["id"]
        self.window._job_saved_snapshot = saved
        self.window.worker = Mock()
        result = copy.deepcopy(saved)
        result.update(status="已完成", summary="任务未落盘的摘要")
        self.window.events.put(("done", (result, "")))
        self.window.drain_events()
        self.assertFalse(self.window._job_persisted)
        self.assertTrue(self.window.flush_edits())
        self.assertTrue(self.window._job_persisted)
        self.window.doc["summary"] = "重试保存后的最新校对"
        self.assertTrue(self.window.flush_edits())
        other = copy.deepcopy(saved)
        other["id"] = new_document("other.wav")["id"]
        save_document(other, self.temp.name)
        self.window.load_document(other)
        self.window.return_to_task()
        self.assertEqual(self.window.doc["summary"], "重试保存后的最新校对")
        self.assertEqual(self.window._disk_snapshot["id"], saved["id"])
        self.window.doc["title"] = "继续校对"
        self.assertTrue(self.window.flush_edits())

    def test_other_interrupted_history_is_reconciled_while_task_runs(self):
        self.window._job_document_id = self.window.doc["id"]
        other = copy.deepcopy(self.window.doc)
        other.update(id=new_document("interrupted.wav")["id"], status="生成摘要")
        self.window.worker = Mock()
        with patch("local_service.owner_is_alive", return_value=False):
            self.window.load_document(other)
        self.assertEqual(self.window.doc["status"], "已中断")
        self.window.worker = None
        self.window.set_busy(False)
        self.assertFalse(self.window.document_locked)
        self.assertEqual(self.window.segment_editor.cget("state"), "normal")

    def test_stop_failure_is_visible_after_switch_and_can_retry(self):
        self.window.stop_audio()
        with patch.object(self.window.audio, "poll", return_value={
                "state": "error", "position_ms": 0, "error": "回听资源尚未释放"}):
            self.window.poll_audio()
        self.assertEqual(self.window.audio_hint.get(), "回听资源尚未释放")
        self.assertEqual(self.window.audio_stop_button.cget("state"), "normal")
        with patch.object(self.window.audio, "stop") as stop:
            self.window.audio_stop_button.invoke()
            stop.assert_called_once()

    def test_failed_audio_close_keeps_task_event_polling(self):
        self.window.flush_edits()
        self.window.worker = Mock()
        self.window.closing = True
        self.window.after_cancel(self.window.poll_timer)
        self.window.poll_timer = None
        self.window.events.put(("done", (copy.deepcopy(self.window.doc), "")))
        with patch.object(self.window.audio, "close", side_effect=app.PreviewError("释放失败")), \
                patch.object(app.messagebox, "showerror"):
            self.window.drain_events()
        self.assertTrue(self.window.winfo_exists())
        self.assertIsNotNone(self.window.poll_timer)
        self.assertIn(self.window.poll_timer, self.window.tk.call("after", "info"))

    def test_responsive_single_transcript_keeps_actions_visible(self):
        for size in ('1240x820','960x680'):
            self.window.geometry(size);self.window.update()
            self.assertFalse(self.window.source_panel.winfo_ismapped())
            self.assertGreater(self.window.reader.winfo_height(),200)
            for widget in (self.window.play_button,self.window.search_entry,*self.window.export_buttons):
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.window.winfo_rootx()+self.window.winfo_width())


if __name__ == "__main__":
    unittest.main()
