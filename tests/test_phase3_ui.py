import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

import app
from checkpoint_store import fingerprint
from core import new_document, save_document
from transcription_recovery import checkpoint_for


class PhaseThreeUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.results = patch.object(app, "RESULTS", self.directory)
        self.results.start()
        self.models = patch.object(app, "local_status", return_value={
            "asr": True, "english": True, "summary": True, "language_detection": True})
        self.models.start()
        self.window = app.App()
        self.window.attributes("-alpha", 0)
        self.window.update()

    def tearDown(self):
        self.window.destroy()
        self.models.stop()
        self.results.stop()
        self.temp.cleanup()

    def make_recoverable(self, *, language="en", completed=1, speaker_count=3,
                         status="已停止", save_settings=True):
        source = self.directory / "source.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 160000)
        document = new_document(source)
        document.update(status=status, duration_ms=10000, language_mode="auto",
                        transcription_complete=False)
        if language:
            document["language"] = language
        if save_settings:
            document["transcription_settings"] = {"speaker_count": speaker_count}
        store = checkpoint_for(self.directory, document["id"])
        store.directory.mkdir(parents=True)
        shutil.copyfile(source, store.audio_path)
        store.save_manifest({"version": 1, "source": fingerprint(source),
                             "audio": fingerprint(store.audio_path), "duration_ms": 10000,
                             "language": language, "settings": {"speaker_count": speaker_count}})
        if completed:
            store.save_unit("plan", {"sample_rate": 16000, "boundaries": [0, 80000, 160000]})
            for index in range(completed):
                store.save_unit(f"asr-{index:05d}", {
                    "start_frame": index * 80000, "end_frame": (index + 1) * 80000,
                    "segments": [{"start_ms": index * 5000, "end_ms": (index + 1) * 5000,
                                  "text": "Preserved words.", "speaker": "unknown"}]})
        save_document(document, self.directory)
        return document, store

    def test_detected_english_stage_keeps_auto_selection_and_shows_resolved_language(self):
        document, _ = self.make_recoverable(language=None, completed=0)
        self.window.load_document(document)
        self.window._job_document_id = document["id"]
        self.window.worker = Mock()
        snapshot = copy.deepcopy(document)
        snapshot.update(status="本地识别中", language="en", language_detection={
            "language": "en", "confidence": .97, "needs_confirmation": False, "samples": []})
        save_document(snapshot, self.directory)
        self.window.events.put(("document", snapshot))
        self.window.drain_events()
        self.assertEqual(self.window.language.get(), "自动检测")
        self.assertEqual(self.window.doc["language"], "en")
        self.assertIn("自动检测结果：英文", self.window.language_status.get())
        self.assertIn("英文", self.window.meta.get())

    def test_waiting_for_language_then_manual_english_resumes_same_document(self):
        document, _ = self.make_recoverable(language=None, completed=0, status="等待确认语言")
        document["language_detection"] = {"language": None, "needs_confirmation": True,
                                          "reason": "取样不确定", "confidence": .5, "samples": []}
        save_document(document, self.directory)
        self.window.load_document(document)
        self.assertIn("手动选择", self.window.language_status.get())
        self.window.language.set("英文")
        called = {}

        def pipeline(current, directory, *args, **kwargs):
            called.update(kwargs)
            self.assertEqual(current["id"], document["id"])
            current.update(status="已完成", language="en", language_mode="en",
                           transcription_complete=True,
                           segments=[{"start_ms": 0, "end_ms": 1000, "speaker": "0", "text": "English result."}],
                           speakers={"0": "说话人 1"})
            save_document(current, directory)

        with patch.object(app, "local_status", return_value={
                "asr": False, "english": True, "summary": True, "language_detection": False}), \
                patch.object(app, "run_local_pipeline", side_effect=pipeline), \
                patch.object(app.threading, "Thread") as thread, \
                patch.object(app.messagebox, "showinfo") as message:
            self.window.resume_transcription()
            thread.assert_called_once()
            thread.call_args.kwargs["target"]()
            self.window.drain_events()
            message.assert_not_called()
        self.assertTrue(called["resume"])
        self.assertEqual(called["language"], "en")
        self.assertEqual(called["speaker_count"], 3)
        self.assertEqual(self.window.doc["id"], document["id"])
        self.assertEqual(self.window.doc["segments"][0]["text"], "English result.")

    def test_saved_chunk_progress_enables_resume_and_preserves_existing_work(self):
        document, store = self.make_recoverable()
        unit_before = (store.directory / "units" / "asr-00000.json").read_bytes()
        self.window.load_document(document)
        self.window.update()
        self.assertTrue(self.window.resume_button.winfo_ismapped())
        self.assertEqual(self.window.resume_button.cget("state"), "normal")
        self.assertEqual(self.window._recovery["completed_chunks"], 1)
        self.assertIn("00:00:05 / 00:00:10", self.window.recovery_status.get())
        with patch.object(self.window, "launch_job") as launch:
            self.window.resume_button.invoke()
            launch.assert_called_once_with(resume=True)
        self.assertEqual((store.directory / "units" / "asr-00000.json").read_bytes(), unit_before)

    def test_unconfirmed_language_blocks_resume_until_zh_or_en_is_chosen(self):
        document, _ = self.make_recoverable(language=None, completed=0, status="等待确认语言")
        document["language_detection"] = {"language": None, "needs_confirmation": True,
                                          "reason": "取样不确定", "confidence": .5, "samples": []}
        save_document(document, self.directory)
        self.window.load_document(document)
        self.window.update()
        self.assertEqual(self.window.language.get(), "自动检测")
        self.assertEqual(self.window.resume_button.cget("state"), "disabled")
        with patch.object(self.window, "launch_job") as launch:
            self.window.resume_transcription()
            launch.assert_not_called()
            self.assertIn("选择中文或英文", self.window.status.get())
            self.window.language.set("中文")
            self.assertEqual(self.window.resume_button.cget("state"), "normal")
            self.window.resume_transcription()
            launch.assert_called_once_with(resume=True)

    def test_stopped_task_panel_keeps_step_and_makes_resume_primary(self):
        document, _ = self.make_recoverable()
        self.window.load_document(document)
        self.window.update()
        flow = self.window.import_flow
        self.assertEqual(self.window.import_drawer.title.cget("text"), "已停止 · 可继续")
        self.assertTrue(flow.paused)
        resume, restart, back = self.window.resume_button, self.window.start_button, flow.back_button
        self.assertEqual(restart.cget("text"), "重新转写")
        self.assertGreater(resume.winfo_rootx(), back.winfo_rootx())
        self.assertGreater(back.winfo_rootx(), restart.winfo_rootx())
        self.assertEqual(resume.cget("state"), "normal")

    def test_loading_history_restores_saved_speaker_count(self):
        document, _ = self.make_recoverable(speaker_count=4)
        self.window.speaker_count.set("7")
        self.window.load_document(document)
        self.assertEqual(self.window.speaker_count.get(), "4")

    def test_manifest_restores_speaker_count_when_interrupted_before_document_settings_saved(self):
        document, _ = self.make_recoverable(speaker_count=4, save_settings=False)
        self.window.load_document(document)
        self.assertEqual(self.window.speaker_count.get(), "4")

    def test_confirmed_cached_language_can_resume_without_detector(self):
        document, _ = self.make_recoverable(language="en")
        self.window.load_document(document)
        self.assertEqual(self.window.language.get(), "自动检测")
        with patch.object(app, "local_status", return_value={
                "asr": False, "english": True, "summary": True, "language_detection": False}), \
                patch.object(self.window, "launch_job") as launch:
            self.window.resume_transcription()
        launch.assert_called_once_with(resume=True)

    def test_missing_audio_blocks_resume_and_new_transcription_preserves_old_files(self):
        document, store = self.make_recoverable()
        old_path = self.directory / (document["id"] + ".json")
        saved_before = old_path.read_bytes()
        manifest_before = (store.directory / "manifest.json").read_bytes()
        store.audio_path.unlink()
        self.window.load_document(document)
        self.window.update()
        self.assertFalse(self.window._recovery["available"])
        self.assertFalse(self.window.resume_button.winfo_ismapped())
        self.assertIn("缓存缺失", self.window.recovery_status.get())
        with patch.object(self.window, "launch_job") as launch:
            self.window.resume_transcription()
            launch.assert_not_called()
            with patch.object(app.messagebox, "askyesno", return_value=True):
                self.window.start()
            launch.assert_called_once_with()
        self.assertNotEqual(self.window.doc["id"], document["id"])
        self.assertEqual(old_path.read_bytes(), saved_before)
        self.assertEqual((store.directory / "manifest.json").read_bytes(), manifest_before)

    def test_corrupt_checkpoint_disables_resume_without_discarding_saved_data(self):
        document, store = self.make_recoverable()
        unit_path = store.directory / "units" / "asr-00000.json"
        encoded = json.loads(unit_path.read_text(encoding="utf-8"))
        encoded["data"]["segments"][0]["text"] = "Changed without its checksum."
        unit_path.write_text(json.dumps(encoded), encoding="utf-8")
        corrupt_before = unit_path.read_bytes()
        self.window.load_document(document)
        self.window.update()
        self.assertFalse(self.window._recovery["available"])
        self.assertFalse(self.window.resume_button.winfo_ismapped())
        self.assertIn("校验失败", self.window.recovery_status.get())
        with patch.object(self.window, "launch_job") as launch:
            self.window.resume_transcription()
        launch.assert_not_called()
        self.assertEqual(unit_path.read_bytes(), corrupt_before)

    def test_relink_moved_source_saves_path_without_changing_document_or_checkpoint_identity(self):
        document, store = self.make_recoverable(language=None, completed=0, status="等待确认语言")
        self.window.load_document(document)
        manifest_before = store.load_manifest()
        moved = self.directory / "moved.wav"
        Path(document["source"]).rename(moved)
        with patch.object(app.filedialog, "askopenfilename", return_value=str(moved)):
            self.window.source_relink_button.invoke()
        persisted = json.loads((self.directory / (document["id"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(persisted["id"], document["id"])
        self.assertEqual(persisted["source"], str(moved))
        self.assertEqual(self.window.file_path.get(), str(moved))
        self.assertEqual(store.load_manifest(), manifest_before)
        self.assertEqual(fingerprint(moved), manifest_before["source"])
        self.window.load_document(persisted)
        self.assertTrue(self.window._recovery["available"])


if __name__ == "__main__":
    unittest.main()
