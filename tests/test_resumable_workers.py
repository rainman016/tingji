from contextlib import contextmanager
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import resumable_transcription as resumable
from english_worker import identify_speakers, transcribe_english
from local_worker import main, normalize_funasr, transcribe


def write_audio(path, seconds=5, quiet=None):
    import numpy as np
    samples = np.full(round(seconds * 16000), 2000, dtype="int16")
    if quiet:
        samples[round(quiet[0] * 16000):round(quiet[1] * 16000)] = 0
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(samples.tobytes())


class ResumableWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio.wav"
        write_audio(self.audio)
        self.store = resumable.MemoryStore()
        self.progress = []
        self.chunk_size = patch.object(resumable, "CHUNK_SECONDS", 2)
        self.chunk_size.start()

    def tearDown(self):
        self.chunk_size.stop()
        self.temp.cleanup()

    @staticmethod
    def factory(function, counters=None):
        @contextmanager
        def create():
            if counters is not None:
                counters.append("loaded")
            try:
                yield function
            finally:
                if counters is not None:
                    counters.append("released")
        return create

    def recognize(self, function, store=None, counters=None):
        return resumable.transcribe_chunks(self.audio, store or self.store, self.progress.append,
                                            self.factory(function, counters), "测试")

    @staticmethod
    def paragraph(clip, duration):
        return [{"start_ms": 0, "end_ms": duration, "speaker": "chunk-local-0", "text": "真实重复的句子"}]

    def test_resume_reuses_only_complete_blocks_and_keeps_repeated_text(self):
        calls, loads = [], []
        def interrupted(clip, duration):
            calls.append(duration)
            if len(calls) == 3:
                raise RuntimeError("模拟进程中断")
            return self.paragraph(clip, duration)
        with self.assertRaisesRegex(RuntimeError, "模拟进程中断"):
            self.recognize(interrupted, counters=loads)
        self.assertEqual(loads, ["loaded", "released"])
        self.assertIsNotNone(self.store.load_unit("asr-00001"))
        self.assertIsNone(self.store.load_unit("asr-00002"))
        resumed = Mock(side_effect=self.paragraph)
        result = self.recognize(resumed)
        self.assertEqual(resumed.call_count, 1)
        self.assertEqual([(row["start_ms"], row["end_ms"]) for row in result],
                         [(0, 2000), (2000, 4000), (4000, 5000)])
        self.assertEqual([row["text"] for row in result], ["真实重复的句子"] * 3)
        self.assertTrue(any("已保存 2/3" in message for message in self.progress))

    def test_fully_cached_asr_never_loads_model(self):
        expected = self.recognize(self.paragraph)
        forbidden = Mock(side_effect=AssertionError("模型不应再次加载"))
        actual = resumable.transcribe_chunks(self.audio, self.store, self.progress.append, forbidden, "测试")
        self.assertEqual(actual, expected)
        forbidden.assert_not_called()

    def test_fixed_plan_prefers_quiet_region_and_covers_every_frame_once(self):
        import soundfile as sf
        write_audio(self.audio, 5, quiet=(1.4, 1.8))
        with sf.SoundFile(self.audio) as audio:
            plan = resumable.load_plan(audio, self.store)
            self.assertGreaterEqual(plan["boundaries"][1], round(1.4 * 16000))
            self.assertLessEqual(plan["boundaries"][1], round(1.8 * 16000))
            self.assertEqual(plan["boundaries"][0], 0)
            self.assertEqual(plan["boundaries"][-1], 80000)
            self.assertEqual(sum(end - start for start, end in zip(plan["boundaries"], plan["boundaries"][1:])), 80000)
            with patch.object(resumable, "_quiet_boundary", side_effect=AssertionError("恢复时不得重切")):
                self.assertEqual(resumable.load_plan(audio, self.store), plan)

    def test_changed_audio_or_invalid_saved_plan_is_rejected(self):
        import soundfile as sf
        self.recognize(self.paragraph)
        write_audio(self.audio, 6)
        with sf.SoundFile(self.audio) as audio, self.assertRaisesRegex(ValueError, "分段计划不匹配"):
            resumable.load_plan(audio, self.store)
        write_audio(self.audio, 5)
        plan = self.store.load_unit("plan")
        plan["boundaries"] = [0, 32000, 32000, 80000]
        self.store.save_unit("plan", plan)
        with sf.SoundFile(self.audio) as audio, self.assertRaisesRegex(ValueError, "分段计划不匹配"):
            resumable.load_plan(audio, self.store)

    def test_corrupt_saved_block_is_not_silently_stitched(self):
        self.recognize(self.paragraph)
        saved = self.store.load_unit("asr-00001")
        saved["segments"][0]["start_ms"] = 500
        self.store.save_unit("asr-00001", saved)
        with self.assertRaisesRegex(ValueError, "无效内容或时间戳"):
            self.recognize(Mock(side_effect=AssertionError("不得覆盖损坏检查点")))

    def test_checkpoint_write_failure_does_not_advance_to_next_block(self):
        callback = Mock(side_effect=self.paragraph)
        save = self.store.save_unit
        def disk_full(key, data):
            if key.startswith("asr-"):
                raise OSError("disk full")
            return save(key, data)
        with patch.object(self.store, "save_unit", side_effect=disk_full), self.assertRaises(OSError):
            self.recognize(callback)
        self.assertEqual(callback.call_count, 1)
        self.assertIsNone(self.store.load_unit("asr-00000"))

    def test_silent_blocks_are_cached_without_discarding_later_speech(self):
        count = 0
        def silence_then_speech(clip, duration):
            nonlocal count
            count += 1
            return [] if count < 3 else self.paragraph(clip, duration)
        rows = self.recognize(silence_then_speech)
        self.assertEqual(rows[0]["start_ms"], 4000)
        self.assertEqual(self.store.load_unit("asr-00000")["segments"], [])
        with self.assertRaisesRegex(ValueError, "没有识别到语音"):
            self.recognize(lambda *_: [], store=resumable.MemoryStore())

    def test_chinese_empty_block_is_valid_but_missing_timestamps_are_not(self):
        self.assertEqual(normalize_funasr([{"text": "", "sentence_info": []}], allow_empty=True), ([], {}))
        with self.assertRaises(ValueError):
            normalize_funasr([{"text": "非空但缺少时间戳"}], allow_empty=True)

    def test_speaker_resume_reuses_features_then_clusters_the_whole_recording(self):
        rows = self.recognize(self.paragraph)
        calls, loads = [], []
        def interrupted(clip):
            calls.append(len(clip))
            if len(calls) == 2:
                raise RuntimeError("声纹中断")
            return [1., 0.]
        with self.assertRaisesRegex(RuntimeError, "声纹中断"):
            resumable.speaker_embeddings(self.audio, rows, self.store, self.progress.append,
                                          self.factory(interrupted, loads))
        self.assertEqual(loads, ["loaded", "released"])
        resumed = Mock(side_effect=[[0., 1.], [1., 0.]])
        vectors = resumable.speaker_embeddings(self.audio, rows, self.store, self.progress.append,
                                                self.factory(resumed))
        self.assertEqual(resumed.call_count, 2)
        self.assertEqual(vectors, [[1., 0.], [0., 1.], [1., 0.]])
        with patch("english_worker.cluster_labels", return_value=[7, 3, 7]) as cluster:
            speakers = identify_speakers(self.audio, rows, 0, "cpu", self.progress.append, store=self.store)
        self.assertEqual(cluster.call_args.args[0], vectors)
        self.assertEqual([row["speaker"] for row in rows], ["0", "1", "0"])
        self.assertEqual(speakers, {"0": "说话人 1", "1": "说话人 2"})

    def test_changed_segment_signature_or_invalid_embedding_blocks_resume(self):
        rows = self.recognize(self.paragraph)
        resumable.speaker_embeddings(self.audio, rows, self.store, self.progress.append,
                                      self.factory(lambda _: [1., 0.]))
        changed = copy.deepcopy(rows)
        changed[0]["text"] = "不同的段落"
        with self.assertRaisesRegex(ValueError, "声纹与当前文字段落不一致"):
            resumable.speaker_embeddings(self.audio, changed, self.store, self.progress.append, Mock())
        saved = self.store.load_unit("speaker-00000")
        saved["embedding"] = [float("nan"), 0.]
        self.store.save_unit("speaker-00000", saved)
        with self.assertRaisesRegex(ValueError, "说话人特征无效"):
            resumable.speaker_embeddings(self.audio, rows, self.store, self.progress.append, Mock())

    def test_chinese_worker_uses_timestamp_only_asr_and_one_model_per_run(self):
        model = Mock()
        model.generate.return_value = [{"text": "测试", "sentence_info": [
            {"text": "测试", "start": 0, "end": 500}]}]
        create = Mock(return_value=model)
        torch = SimpleNamespace(set_num_threads=Mock(), cuda=SimpleNamespace(is_available=lambda: False))
        with patch.dict("sys.modules", {"torch": torch, "funasr": SimpleNamespace(AutoModel=create)}), \
                patch.object(resumable, "worker_store", return_value=self.store):
            result = transcribe({"audio": str(self.audio), "speaker_count": 1}, self.progress.append)
        self.assertEqual(create.call_count, 1)
        self.assertNotIn("spk_model", create.call_args.kwargs)
        self.assertEqual(model.generate.call_count, 3)
        self.assertTrue(model.generate.call_args.kwargs["output_timestamp"])
        self.assertEqual([row["start_ms"] for row in result["segments"]], [0, 2000, 4000])
        self.assertEqual(result["speakers"], {"0": "说话人 1"})

    def test_english_worker_keeps_word_timing_and_loads_only_once(self):
        model = Mock()
        def decode(*args, **kwargs):
            self.assertEqual(kwargs["language"], "en")
            self.assertTrue(kwargs["word_timestamps"])
            return iter([SimpleNamespace(words=[SimpleNamespace(start=.1, end=.5, word=" Hello.")])]), None
        model.transcribe.side_effect = decode
        create = Mock(return_value=model)
        torch = SimpleNamespace(__file__=str(self.root / "fake-torch.py"), set_num_threads=Mock(),
                                cuda=SimpleNamespace(is_available=lambda: False))
        with patch.dict("sys.modules", {"torch": torch, "faster_whisper": SimpleNamespace(WhisperModel=create)}), \
                patch.object(resumable, "worker_store", return_value=self.store):
            result = transcribe_english({"audio": str(self.audio), "speaker_count": 1}, self.progress.append)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(model.transcribe.call_count, 3)
        self.assertEqual([row["start_ms"] for row in result["segments"]], [100, 2100, 4100])

    def test_worker_explicitly_dispatches_detection_and_rejects_unknown_mode(self):
        request, result, progress = (self.root / name for name in ("request.json", "result.json", "progress.json"))
        detector = Mock(return_value={"language": "en", "confidence": .98})
        with patch("local_worker.sys.argv", ["worker", str(request), str(result), str(progress)]), \
                patch("local_worker.block_network"), \
                patch.dict("sys.modules", {"language_detector": SimpleNamespace(detect_language=detector)}):
            request.write_text('{"mode":"detect_language"}', encoding="utf-8")
            self.assertEqual(main(), 0)
            self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["data"]["language"], "en")
            request.write_text('{"mode":"unknown"}', encoding="utf-8")
            with patch("local_worker.summarize") as summary, patch("traceback.print_exc"):
                self.assertEqual(main(), 1)
                summary.assert_not_called()
            self.assertIn("未知", json.loads(result.read_text(encoding="utf-8"))["error"])


if __name__ == "__main__":
    unittest.main()
