import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app
from core import export_text, new_document, save_document
from process_control import process_owner


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(app, "RESULTS", Path(self.temp.name))
        self.patcher.start()
        self.window = app.App()
        self.window.attributes("-alpha", 0)
        self.window.update()

    def tearDown(self):
        self.window.destroy()
        self.patcher.stop()
        self.temp.cleanup()

    def test_window_demo_edit_save_reload_and_export(self):
        self.window.open_example()
        self.window.update()
        self.assertEqual(self.window.reader.segment_count, 4)
        self.assertGreater(self.window.tabs.winfo_height(), 160)
        self.window.reader.select(1)
        self.window.update()
        self.window.segment_editor.delete("1.0", "end")
        self.window.segment_editor.insert("1.0", "校对后的实际内容。")
        self.window.update()
        self.window.flush_edits()
        self.assertEqual(self.window.doc["segments"][1]["text"], "校对后的实际内容。")
        self.assertFalse(self.window.doc["summary_stale"])
        self.window.open_segment_editor(1)
        self.window.rename_speaker()
        self.window.speaker_name_value.set("测试姓名")
        self.window.save_speaker_name()
        self.window.finish_segment_edit()
        self.assertIn("测试姓名", export_text(self.window.doc, "srt"))
        self.window.refresh_history()
        self.assertEqual(len(self.window.history_docs), 1)
        self.window.history.selection_set(0)
        self.window.open_history()
        self.window.update()
        self.assertIn("校对后的实际内容。", export_text(self.window.doc, "txt"))
        target = Path(self.temp.name) / "输出.srt"
        with patch.object(app.filedialog, "asksaveasfilename", return_value=str(target)):
            self.window.export("srt")
        self.assertIn("校对后的实际内容。", target.read_text(encoding="utf-8-sig"))

    def test_minimum_window_keeps_export_actions_inside(self):
        self.window.geometry("960x680")
        self.assertTrue(self.window.tabs.winfo_ismapped())
        self.window.load_document(new_document('sample.mp4'))
        self.window.import_flow.toggle_advanced()
        self.window.update()
        for button in [self.window.start_button, self.window.language_box,
                       self.window.count_box]:
            right = button.winfo_rootx() + button.winfo_width()
            bottom = button.winfo_rooty() + button.winfo_height()
            self.assertLessEqual(right, self.window.winfo_rootx() + self.window.winfo_width())
            self.assertLessEqual(bottom, self.window.winfo_rooty() + self.window.winfo_height())
        self.assertTrue(self.window.source_panel.winfo_ismapped())
        self.window.open_example()
        self.window.update()
        self.assertGreater(self.window.tabs.winfo_height(), 120)
        self.assertTrue(self.window.export_button.winfo_ismapped())

    def test_missing_local_models_stops_before_inference(self):
        self.window.language.set("中文")
        with patch.object(app, "local_status", return_value={"asr": False, "summary": False}), patch.object(app.messagebox, "showinfo") as message:
            self.assertFalse(self.window.check_ready())
            self.assertTrue(self.window.notice_text.get())
            message.assert_not_called()

    def test_ready_models_do_not_require_key_or_upload_consent(self):
        self.window.language.set("中文")
        with patch.object(app, "local_status", return_value={"asr": True, "summary": True}):
            self.assertTrue(self.window.check_ready())
        self.assertFalse(hasattr(self.window, "api_key"))
        self.assertFalse(hasattr(self.window, "consent"))

    def test_failure_is_visible_above_results_and_retry_clears_it(self):
        from core import new_document
        doc = new_document("example.mp4")
        doc.update(status="待重试", error="视频时长超过 4 小时，请分段。")
        self.window.load_document(doc)
        self.window.update()
        self.assertTrue(self.window.error_panel.winfo_ismapped())
        self.assertIn("4 小时", self.window.notice_text.get())
        self.assertEqual(self.window.empty_title.cget("text"), "处理未完成")
        self.window.set_busy(True)
        self.window.update()
        self.assertFalse(self.window.error_panel.winfo_ismapped())
        self.window.set_busy(False)

    def test_english_selection_requires_english_model(self):
        self.window.language.set("英文")
        with patch.object(app, "local_status", return_value={"asr": True, "summary": True, "english": False}), \
             patch.object(app.messagebox, "showinfo") as message:
            self.assertFalse(self.window.check_ready())
            self.assertIn("英文模型", self.window.notice_text.get())
            self.assertTrue(self.window.notice_text.get())
            message.assert_not_called()

    def test_new_file_defaults_auto_with_explicit_missing_detector_and_manual_fallback(self):
        self.assertEqual(self.window.language.get(), "自动检测")
        self.window.language.set("英文")
        self.window.load_document(new_document("another.mp4"))
        self.assertEqual(self.window.language.get(), "自动检测")
        state = {"asr": True, "english": True, "summary": True, "language_detection": False}
        with patch.object(app, "local_status", return_value=state), patch.object(app.messagebox, "showinfo") as message:
            self.assertFalse(self.window.check_ready())
            self.assertIn("安装语言检测模型", self.window.notice_text.get())
            self.assertIn("手动选择", self.window.notice_text.get())
            self.assertTrue(self.window.notice_text.get())
            message.assert_not_called()
            self.window.language.set("英文")
            self.assertTrue(self.window.check_ready())
            self.window.language.set("中文")
            self.assertTrue(self.window.check_ready())
        self.window.language.set("自动检测")
        with patch.object(app, "local_status", return_value={"asr": False, "english": False, "summary": True, "language_detection": False}):
            self.assertFalse(self.window.check_ready())

    def test_history_round_trip_keeps_saved_corrections(self):
        self.window.open_example()
        self.window.flush_edits()
        first_id = self.window.doc["id"]
        second = copy.deepcopy(self.window.doc)
        second["id"] = new_document("second.mp4")["id"]
        save_document(second, self.temp.name)
        self.window.refresh_history()
        self.window.doc["segments"][0]["text"] = "这段校对必须保留。"
        self.assertTrue(self.window.flush_edits())
        for document_id in (second["id"], first_id):
            index = next(i for i, row in enumerate(self.window.history_docs) if row["id"] == document_id)
            self.window.history.selection_clear(0, "end")
            self.window.history.selection_set(index)
            self.window.open_history()
        self.assertEqual(self.window.doc["segments"][0]["text"], "这段校对必须保留。")
        self.window.flush_edits()
        persisted = json.loads((Path(self.temp.name) / (first_id + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(persisted["segments"][0]["text"], "这段校对必须保留。")

    def test_save_failure_blocks_close_file_change_and_example(self):
        self.window.open_example()
        document_id = self.window.doc["id"]
        self.window.doc["segments"][0]["text"] = "尚未保存的校对"
        with patch.object(app, "save_document", side_effect=OSError("disk full")), \
             patch.object(app.messagebox, "showerror"), \
             patch.object(self.window, "destroy") as destroy, \
             patch.object(app.filedialog, "askopenfilename") as choose:
            self.window.close()
            destroy.assert_not_called()
            self.window.choose_file()
            choose.assert_not_called()
            self.window.open_example()
        self.assertEqual(self.window.doc["id"], document_id)
        self.assertEqual(self.window.doc["segments"][0]["text"], "尚未保存的校对")
        self.assertIn("保存失败", self.window.save_status.get())

    def test_save_failure_keeps_history_and_prevents_start(self):
        self.window.open_example()
        self.window.flush_edits()
        self.window.refresh_history()
        self.window.history.selection_set(0)
        self.window.doc["segments"][0]["text"] = "尚未落盘"
        source = Path(self.temp.name) / "source.mp4"
        source.touch()
        self.window.file_path.set(str(source))
        with patch.object(app, "save_document", side_effect=OSError("disk full")), \
             patch.object(app.messagebox, "showerror"), \
             patch.object(self.window, "check_ready", return_value=True), \
             patch.object(self.window, "launch_job") as launch:
            self.window.open_history()
            self.assertEqual(self.window.doc["segments"][0]["text"], "尚未落盘")
            self.window.start()
            launch.assert_not_called()

    def test_export_remains_available_when_local_save_fails(self):
        self.window.open_example()
        self.window.doc["segments"][0]["text"] = "需要导出的内存修改"
        target = Path(self.temp.name) / "backup.txt"
        with patch.object(app, "save_document", side_effect=OSError("disk full")), \
             patch.object(app.messagebox, "showerror"), \
             patch.object(app.filedialog, "asksaveasfilename", return_value=str(target)):
            self.window.export("txt")
        self.assertIn("需要导出的内存修改", target.read_text(encoding="utf-8-sig"))
        self.assertIn("保存失败", self.window.save_status.get())

    def test_external_change_is_not_overwritten_by_unsaved_edits(self):
        self.window.open_example()
        self.window.flush_edits()
        other = copy.deepcopy(self.window.doc)
        other["segments"][0]["text"] = "另一个窗口的修改"
        save_document(other, self.temp.name)
        self.window.doc["segments"][1]["text"] = "本窗口的修改"
        with patch.object(app.messagebox, "showerror"):
            self.assertFalse(self.window.flush_edits())
        persisted = json.loads((Path(self.temp.name) / (other["id"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(persisted["segments"][0]["text"], "另一个窗口的修改")
        self.assertEqual(self.window.doc["segments"][1]["text"], "本窗口的修改")

    def test_transcript_can_be_read_and_exported_before_summary_finishes(self):
        self.window.open_example()
        self.window.flush_edits()
        snapshot = copy.deepcopy(self.window.doc)
        snapshot.update(status="生成摘要", summary="")
        save_document(snapshot, self.temp.name)
        self.window.worker = Mock()
        self.window.events.put(("document", snapshot))
        self.window.drain_events()
        self.assertTrue(self.window.busy)
        self.assertEqual(self.window.segment_editor.cget("state"), "disabled")
        self.assertTrue(all(button.cget("state") == "normal" for button in self.window.export_buttons))
        self.window.reader.select(1)
        self.window.select_segment()
        self.assertEqual(self.window.segment_editor.get("1.0", "end-1c"), snapshot["segments"][1]["text"])
        latest = copy.deepcopy(snapshot)
        latest.update(status="已完成", summary="后台刚完成的新摘要")
        save_document(latest, self.temp.name)
        target = Path(self.temp.name) / "during-summary.txt"
        with patch.object(app, "save_document") as persist, \
             patch.object(app.filedialog, "asksaveasfilename", return_value=str(target)):
            self.window.export("txt")
            persist.assert_not_called()
        self.assertTrue(target.exists())
        persisted = json.loads((Path(self.temp.name) / (latest["id"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(persisted["summary"], "后台刚完成的新摘要")
        self.window.events.put(("done", (latest, "")))
        self.window.drain_events()
        self.assertFalse(self.window.busy)
        self.assertEqual(self.window.segment_editor.cget("state"), "normal")

    def test_worker_save_failure_keeps_unsaved_result_and_blocks_exit(self):
        self.window.open_example()
        self.window.flush_edits()
        result = copy.deepcopy(self.window.doc)
        result["summary"] = "尚未写入磁盘的摘要结果"
        self.window.worker = Mock()
        self.window.events.put(("done", (result, "保存失败")))
        with patch.object(app.messagebox, "showerror"):
            self.window.drain_events()
        self.assertIn("保存失败", self.window.save_status.get())
        with patch.object(app, "save_document", side_effect=OSError("disk full")), \
             patch.object(app.messagebox, "showerror"), patch.object(self.window, "destroy") as destroy:
            self.window.close()
            destroy.assert_not_called()
        self.assertEqual(self.window.doc["summary"], "尚未写入磁盘的摘要结果")

    def test_clean_stale_document_cannot_start_summary_over_newer_file(self):
        self.window.open_example()
        self.window.flush_edits()
        latest = copy.deepcopy(self.window.doc)
        latest["segments"][0]["text"] = "另一窗口保存的新原文"
        save_document(latest, self.temp.name)
        with patch.object(app.messagebox, "showerror"), patch.object(app.threading, "Thread") as thread:
            self.window.launch_job()
            thread.assert_not_called()
        disk = json.loads((Path(self.temp.name) / (latest["id"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(disk, latest)

    def test_external_active_document_is_read_only_but_exportable(self):
        self.window.open_example()
        active = copy.deepcopy(self.window.doc)
        active.update(status="生成摘要", task_owner=process_owner())
        save_document(active, self.temp.name)
        self.window.load_document(active)
        self.assertFalse(self.window.busy)
        self.assertTrue(self.window.document_locked)
        self.assertEqual(self.window.segment_editor.cget("state"), "disabled")
        self.assertEqual(self.window.start_button.cget("state"), "disabled")
        self.assertTrue(all(button.cget("state") == "normal" for button in self.window.export_buttons))
        with patch.object(app, "save_document") as persist, patch.object(app.threading, "Thread") as thread:
            self.window.flush_edits()
            self.window.launch_job()
            persist.assert_not_called()
            thread.assert_not_called()

    def test_conflict_reload_requires_explicit_discard_and_loads_latest(self):
        self.window.open_example()
        self.window.flush_edits()
        latest = copy.deepcopy(self.window.doc)
        latest["segments"][0]["text"] = "磁盘上的最新原文"
        save_document(latest, self.temp.name)
        self.window.doc["segments"][0]["text"] = "本地未保存修改"
        with patch.object(app.messagebox, "askyesno", return_value=False):
            self.window.reload_current_document()
        self.assertEqual(self.window.doc["segments"][0]["text"], "本地未保存修改")
        with patch.object(app.messagebox, "askyesno", return_value=True):
            self.window.reload_current_document()
        self.assertEqual(self.window.doc["segments"][0]["text"], "磁盘上的最新原文")
        self.assertTrue(self.window.flush_edits())


if __name__ == "__main__":
    unittest.main()
