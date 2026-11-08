import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

from checkpoint_store import CheckpointInUse, CheckpointStore
from core import AppError, Cancelled, new_document, save_document
from local_service import run_local_pipeline
from media_fixture import copy_audio, make_source
from transcription_recovery import LanguageConfirmationRequired, checkpoint_for, recovery_info


class RecoveryPipelineTests(unittest.TestCase):
    def test_summary_citation_guard_is_saved_with_raw_audit_copy(self):
        self.service.summarize.return_value = '结论应核对原文 [00:59:59]'
        self.run_pipeline(auto_summary=True)
        self.assertIn('时间待核对', self.document['summary'])
        self.assertNotIn('[00:59:59]', self.document['summary'])
        self.assertEqual(self.document['summary_original'], '结论应核对原文 [00:59:59]')
        saved = json.loads(self.document_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['summary_validation']['issues'][0]['kind'], 'unverified_time')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.directory = self.folder / "results"
        self.source = make_source(self.folder)
        self.document = new_document(self.source)
        self.cancel = threading.Event()
        self.extract = Mock(side_effect=copy_audio)
        self.service = Mock()
        self.service.detect_language.return_value = {
            "language": "en", "confidence": .96, "needs_confirmation": False,
            "reason": "多个取样位置一致识别为英文", "samples": []}
        self.service.transcribe.side_effect = self.transcribe
        self.service.summarize.return_value = "摘要"
        self.fail_after_first_chunk = False
        self.processed_chunks = []
        self.events = []
        self.models = self.folder / "models"
        self.models_patch = patch("local_service.MODELS", self.models)
        self.models_patch.start()

    def tearDown(self):
        self.models_patch.stop()
        self.temp.cleanup()

    @property
    def store(self):
        return checkpoint_for(self.directory, self.document["id"])

    @property
    def document_path(self):
        return self.directory / (self.document["id"] + ".json")

    def run_pipeline(self, **options):
        settings = dict(auto_summary=False, service_factory=lambda *_: self.service,
                        extractor=self.extract, on_document=self.events.append)
        settings.update(options)
        return run_local_pipeline(self.document, self.directory, self.cancel, lambda _: None, **settings)

    def transcribe(self, audio, speaker_count, language="zh", checkpoint_dir=None):
        self.assertTrue(Path(audio).is_file())
        self.assertIsNotNone(checkpoint_dir)
        store = CheckpointStore(checkpoint_dir)
        boundaries = [0, 16000, 48000]
        if store.load_unit("plan") is None:
            store.save_unit("plan", {"boundaries": boundaries, "sample_rate": 16000})
        segments = []
        for index in range(2):
            key = f"asr-{index:05d}"
            unit = store.load_unit(key)
            if unit is None:
                self.processed_chunks.append(index)
                unit = {"start_frame": boundaries[index], "end_frame": boundaries[index + 1],
                        "segments": [{"start_ms": boundaries[index] // 16,
                                      "end_ms": boundaries[index + 1] // 16,
                                      "speaker": "0", "text": f"{language} chunk {index}"}]}
                store.save_unit(key, unit)
            segments.extend(unit["segments"])
            if self.fail_after_first_chunk and index == 0:
                self.cancel.set()
                raise Cancelled("模拟已保存首块后中断")
        return {"segments": segments, "speakers": {"0": "说话人 1"}, "device": "cpu", "seconds": 1}

    def interrupt_after_first_chunk(self, **options):
        self.fail_after_first_chunk = True
        with self.assertRaises(Cancelled):
            self.run_pipeline(**options)
        self.fail_after_first_chunk = False
        self.cancel.clear()
        self.service.transcribe.reset_mock()
        self.events.clear()

    def test_asr_interruption_retains_audio_and_resume_reuses_complete_chunks(self):
        self.interrupt_after_first_chunk(language="zh")
        self.assertEqual(self.document["status"], "已停止")
        self.assertTrue(self.store.audio_path.is_file())
        first = self.store.load_unit("asr-00000")
        info = recovery_info(self.document, self.directory)
        self.assertTrue(info["available"])
        self.assertEqual((info["completed_chunks"], info["total_chunks"]), (1, 2))
        self.assertEqual((info["completed_ms"], info["total_ms"]), (1000, 3000))
        self.run_pipeline(resume=True, language="zh")
        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(self.processed_chunks, [0, 1])
        self.assertEqual(self.store.load_unit("asr-00000"), first)
        self.assertNotIn("提取音频", [event["status"] for event in self.events])
        self.assertEqual([row["start_ms"] for row in self.document["segments"]], [0, 1000])
        self.assertEqual(self.document["segments"][-1]["end_ms"], 3000)
        self.assertTrue(self.document["transcription_complete"])
        self.assertFalse(recovery_info(self.document, self.directory)["available"])

    def test_auto_confident_english_routes_and_persists_detection(self):
        self.run_pipeline(language="auto")
        self.assertEqual(self.document["language"], "en")
        self.assertEqual(self.document["language_mode"], "auto")
        self.assertEqual(self.document["engine"], "local-whisper-small-en")
        self.assertEqual(self.document["language_detection"]["confidence"], .96)
        self.assertEqual(self.service.detect_language.call_count, 1)
        self.assertEqual(self.service.transcribe.call_args.kwargs["language"], "en")
        disk = json.loads(self.document_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["language_detection"], self.document["language_detection"])

    def test_uncertain_language_waits_and_manual_confirmation_reuses_audio(self):
        self.service.detect_language.return_value = {
            "language": None, "confidence": .51, "needs_confirmation": True,
            "reason": "语言不确定，请手动选择。", "samples": []}
        with self.assertRaises(LanguageConfirmationRequired):
            self.run_pipeline(language="auto")
        self.assertEqual(self.document["status"], "等待确认语言")
        self.assertNotIn("language", self.document)
        self.service.transcribe.assert_not_called()
        self.assertTrue(self.store.audio_path.is_file())
        self.assertTrue(recovery_info(self.document, self.directory)["available"])
        self.run_pipeline(resume=True, language="zh")
        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(self.service.detect_language.call_count, 1)
        self.assertEqual(self.service.transcribe.call_args.kwargs["language"], "zh")
        self.assertEqual(self.document["language"], "zh")

    def test_resuming_auto_detection_keeps_previously_confirmed_language(self):
        self.interrupt_after_first_chunk(language="auto")
        self.service.detect_language.return_value = {
            "language": "zh", "confidence": .99, "needs_confirmation": False}
        self.run_pipeline(resume=True, language="auto")
        self.assertEqual(self.service.detect_language.call_count, 1)
        self.assertEqual(self.service.transcribe.call_args.kwargs["language"], "en")
        self.assertEqual(self.document["language"], "en")
        self.assertTrue(all(row["text"].startswith("en") for row in self.document["segments"]))

    def test_replaced_source_rejects_cache_without_overwriting_saved_chunks(self):
        self.interrupt_after_first_chunk()
        saved_manifest = self.store.load_manifest()
        first = self.store.load_unit("asr-00000")
        make_source(self.folder, amplitude=123)
        with self.assertRaisesRegex(AppError, "原音视频内容已改变"):
            self.run_pipeline(resume=True)
        self.service.transcribe.assert_not_called()
        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(self.store.load_manifest(), saved_manifest)
        self.assertEqual(self.store.load_unit("asr-00000"), first)

    def test_moving_same_source_preserves_recoverability(self):
        self.interrupt_after_first_chunk()
        moved = self.folder / "renamed.wav"
        self.source.rename(moved)
        self.document["source"] = str(moved)
        self.run_pipeline(resume=True)
        self.assertEqual(self.extract.call_count, 1)
        self.assertEqual(self.processed_chunks, [0, 1])

    def test_changed_audio_cache_is_rejected_without_reextracting(self):
        self.interrupt_after_first_chunk()
        make_source(self.store.directory, name="audio.wav", amplitude=321)
        with self.assertRaisesRegex(AppError, "音频缓存.*损坏"):
            self.run_pipeline(resume=True)
        self.service.transcribe.assert_not_called()
        self.assertEqual(self.extract.call_count, 1)
        self.assertTrue(self.store.audio_path.is_file())

    def test_changed_speaker_setting_cannot_mix_old_and_new_chunks(self):
        self.interrupt_after_first_chunk(speaker_count=2)
        with self.assertRaisesRegex(AppError, "说话人数与已保存进度不同"):
            self.run_pipeline(resume=True, speaker_count=3)
        self.service.transcribe.assert_not_called()
        self.assertEqual(self.store.load_manifest()["settings"]["speaker_count"], 2)

    def test_changed_language_cannot_mix_old_and_new_chunks(self):
        self.interrupt_after_first_chunk(language="en")
        with self.assertRaisesRegex(AppError, "所选语言与已保存进度不同"):
            self.run_pipeline(resume=True, language="zh")
        self.service.transcribe.assert_not_called()
        self.assertEqual(self.store.load_manifest()["language"], "en")

    def test_changed_model_cannot_mix_old_and_new_chunks(self):
        self.interrupt_after_first_chunk(language="zh")
        folder = self.models / "paraformer-zh"
        folder.mkdir(parents=True)
        (folder / "model.pt").write_bytes(b"new model fixture")
        with self.assertRaisesRegex(AppError, "模型版本已变化"):
            self.run_pipeline(resume=True, language="zh")
        self.service.transcribe.assert_not_called()

    def test_audio_is_released_only_after_complete_transcript_is_durable(self):
        completed = []
        def snapshot(value):
            if value["status"] == "已转写":
                persisted = json.loads(self.document_path.read_text(encoding="utf-8"))
                self.assertTrue(persisted["transcription_complete"])
                self.assertEqual(persisted["segments"], value["segments"])
                if not completed:
                    self.assertTrue(self.store.audio_path.is_file())
                completed.append(value)
        self.run_pipeline(on_document=snapshot)
        self.assertTrue(completed)
        self.assertFalse(self.store.audio_path.exists())
        self.assertIsNotNone(self.store.load_unit("asr-00000"))

    def test_failure_to_save_final_transcript_retains_audio_and_chunks(self):
        def persist(document, directory):
            if document.get("transcription_complete"):
                raise OSError("disk full before completed transcript is saved")
            return save_document(document, directory)
        with patch("core.save_document", side_effect=persist):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.run_pipeline()
        self.assertTrue(self.store.audio_path.is_file())
        self.assertIsNotNone(self.store.load_unit("asr-00001"))
        persisted = json.loads(self.document_path.read_text(encoding="utf-8"))
        self.assertFalse(persisted["transcription_complete"])
        self.assertEqual(persisted["segments"], [])

    def test_lock_conflict_does_not_publish_or_overwrite_active_task(self):
        self.document.update(status="本地识别中", task_owner={"pid": 12345, "creation_time": "active"})
        save_document(self.document, self.directory)
        saved = self.document_path.read_bytes()
        with self.store.lock():
            with self.assertRaises(CheckpointInUse):
                self.run_pipeline(resume=True)
        self.assertEqual(self.document_path.read_bytes(), saved)
        self.assertEqual(self.events, [])
        self.service.transcribe.assert_not_called()
        self.extract.assert_not_called()

    def test_job_lock_remains_owned_while_summary_is_generated(self):
        checks = []
        def summarize(document, style):
            self.assertTrue(document["transcription_complete"])
            with self.assertRaises(CheckpointInUse):
                with CheckpointStore(self.store.directory).lock():
                    self.fail("摘要期间不应允许第二个任务取得文稿锁")
            checks.append("summary")
            return "锁保护下生成的摘要"
        self.service.summarize.side_effect = summarize
        self.run_pipeline(auto_summary=True)
        self.assertEqual(checks, ["summary"])
        self.assertEqual(self.document["summary"], "锁保护下生成的摘要")
        self.assertEqual(self.document["status"], "已完成")

    def test_job_lock_remains_owned_during_failure_persistence_callback(self):
        checks = []
        self.service.transcribe.side_effect = AppError("模拟识别失败")
        def snapshot(value):
            self.events.append(value)
            if value["status"] == "待重试":
                self.assertEqual(json.loads(self.document_path.read_text(encoding="utf-8")), value)
                with self.assertRaises(CheckpointInUse):
                    with CheckpointStore(self.store.directory).lock():
                        self.fail("失败结果落盘期间不应允许第二个任务取得文稿锁")
                checks.append("failure")
        with self.assertRaisesRegex(AppError, "模拟识别失败"):
            self.run_pipeline(on_document=snapshot)
        self.assertEqual(checks, ["failure"])
        self.assertEqual(self.events[-1]["status"], "待重试")

    def test_initial_cancellation_cannot_overwrite_another_lock_owners_document(self):
        self.document.update(status="本地识别中", task_owner={"pid": 12345, "creation_time": "active"})
        save_document(self.document, self.directory)
        saved = self.document_path.read_bytes()
        original = copy.deepcopy(self.document)
        self.cancel.set()
        with self.store.lock():
            with self.assertRaises(CheckpointInUse):
                self.run_pipeline(resume=True)
        self.assertEqual(self.document_path.read_bytes(), saved)
        self.assertEqual(self.document, original)
        self.assertEqual(self.events, [])
        self.service.transcribe.assert_not_called()
        self.extract.assert_not_called()

    def test_stale_resume_cannot_replace_an_already_completed_document(self):
        completed_markers = (
            {"transcription_complete": True},
            {"segments": [{"start_ms": 0, "end_ms": 1000, "speaker": "0", "text": "已完成的原文"}],
             "speakers": {"0": "说话人 1"}},
        )
        for marker in completed_markers:
            with self.subTest(marker=marker):
                latest = copy.deepcopy(self.document)
                latest.update(status="已完成", **marker)
                save_document(latest, self.directory)
                saved = self.document_path.read_bytes()
                with self.assertRaisesRegex(AppError, "已经完成转写"):
                    self.run_pipeline(resume=True)
                self.assertEqual(self.document_path.read_bytes(), saved)
                self.assertEqual(self.events, [])
                self.service.transcribe.assert_not_called()
                self.extract.assert_not_called()

    def test_expected_document_conflict_is_rejected_without_publishing(self):
        expected = copy.deepcopy(self.document)
        latest = copy.deepcopy(expected)
        latest["title"] = "另一个窗口刚修改的名称"
        save_document(latest, self.directory)
        saved = self.document_path.read_bytes()
        with self.assertRaisesRegex(AppError, "已被其他窗口更新"):
            self.run_pipeline(expected_document=expected)
        self.assertEqual(self.document_path.read_bytes(), saved)
        self.assertEqual(self.document, expected)
        self.assertEqual(self.events, [])
        self.service.transcribe.assert_not_called()
        self.extract.assert_not_called()

    def test_invalid_extracted_audio_is_not_committed_as_recoverable_audio(self):
        def invalid_audio(source, target, cancel):
            with wave.open(str(target), "wb") as media:
                media.setnchannels(2)
                media.setsampwidth(2)
                media.setframerate(16000)
                media.writeframes(b"\0" * 64000)
            return 1000
        self.extract.side_effect = invalid_audio
        with self.assertRaisesRegex(AppError, "恢复音频格式无效"):
            self.run_pipeline()
        self.service.transcribe.assert_not_called()
        self.assertIsNone(self.store.load_manifest()["audio"])
        self.assertFalse(self.store.audio_path.exists())
        self.assertFalse((self.store.directory / "audio.partial.wav").exists())


if __name__ == "__main__":
    unittest.main()
