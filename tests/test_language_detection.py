import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from language_detector import detect_language, language_model_ready, sample_offsets


class LanguageDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "sample.wav"
        self.model = Mock()
        self.model.supported_languages = ["zh", "en", "fr"]
        self.model.detect_language.return_value = ("en", .99, [("en", .99), ("zh", .005)])
        self.factory = Mock(return_value=self.model)
        self.vad = Mock(side_effect=lambda audio, **_: [{"start": 0, "end": len(audio)}])

    def tearDown(self):
        self.temp.cleanup()

    def write_audio(self, seconds=6, sample_rate=16000, channels=1):
        with wave.open(str(self.source), "wb") as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            audio.writeframes(b"\x00\x00" * (int(seconds * sample_rate) * channels))

    def detect(self):
        return detect_language({"audio": str(self.source)}, lambda _: None,
                               model_factory=self.factory, vad=self.vad)

    def test_strong_english_selects_multilingual_local_cpu_model(self):
        self.write_audio()
        result = self.detect()
        self.assertEqual(result["language"], "en")
        self.assertFalse(result["needs_confirmation"])
        self.assertTrue(self.factory.call_args.kwargs["local_files_only"])
        self.assertEqual(self.factory.call_args.kwargs["device"], "cpu")
        self.assertEqual(self.factory.call_args.kwargs["compute_type"], "int8")
        self.assertTrue(self.factory.call_args.args[0].endswith("whisper-tiny"))
        self.assertEqual(self.model.detect_language.call_args.kwargs["audio"].dtype.name, "float32")
        self.assertEqual(result["samples"][0]["speech_ms"], 6000)

    def test_strong_chinese_routes_to_chinese(self):
        self.write_audio()
        self.model.detect_language.return_value = ("zh", .97, [("zh", .97), ("en", .02)])
        self.assertEqual(self.detect()["language"], "zh")

    def test_low_confidence_requires_confirmation_without_defaulting_chinese(self):
        self.write_audio()
        self.model.detect_language.return_value = ("en", .65, [("en", .65), ("zh", .3)])
        result = self.detect()
        self.assertIsNone(result["language"])
        self.assertTrue(result["needs_confirmation"])
        self.assertIn("手动选择", result["reason"])

    def test_disagreeing_distributed_samples_require_confirmation(self):
        self.write_audio(seconds=100)
        self.model.detect_language.side_effect = [
            ("en", .98, [("en", .98), ("zh", .01)]),
            ("zh", .95, [("zh", .95), ("en", .03)]),
            ("en", .99, [("en", .99), ("zh", .005)]),
        ]
        result = self.detect()
        self.assertIsNone(result["language"])
        self.assertIn("语言不一致", result["reason"])
        self.assertEqual([item["offset_ms"] for item in result["samples"]], [0, 35000, 70000])

    def test_unsupported_language_is_not_forced_into_english_or_chinese(self):
        self.write_audio()
        self.model.detect_language.return_value = ("fr", .98, [("fr", .98), ("en", .01)])
        result = self.detect()
        self.assertIsNone(result["language"])
        self.assertTrue(result["needs_confirmation"])
        self.assertIn("之外", result["reason"])

    def test_no_speech_or_too_little_speech_does_not_load_whisper(self):
        self.write_audio()
        self.vad.side_effect = None
        self.vad.return_value = []
        self.assertTrue(self.detect()["needs_confirmation"])
        self.factory.assert_not_called()
        self.vad.return_value = [{"start": 0, "end": 16000 * 2}]
        self.assertTrue(self.detect()["needs_confirmation"])
        self.factory.assert_not_called()

    def test_vad_filters_silence_before_detection_without_padding_speech(self):
        self.write_audio(seconds=20)
        self.vad.side_effect = None
        self.vad.return_value = [{"start": 80000, "end": 160000}]
        result = self.detect()
        self.assertEqual(result["samples"][0]["speech_ms"], 5000)
        self.assertEqual(len(self.model.detect_language.call_args.kwargs["audio"]), 80000)

    def test_long_audio_reads_only_three_30_second_windows(self):
        source = Mock()
        source.getframerate.return_value = 16000
        source.getnchannels.return_value = 1
        source.getsampwidth.return_value = 2
        source.getcomptype.return_value = "NONE"
        source.getnframes.return_value = 4 * 3600 * 16000
        source.readframes.return_value = b"\x00\x00" * (30 * 16000)
        with patch("language_detector.wave.open") as opened:
            opened.return_value.__enter__.return_value = source
            result = self.detect()
        self.assertEqual(source.readframes.call_count, 3)
        self.assertTrue(all(call.args == (480000,) for call in source.readframes.call_args_list))
        self.assertEqual(len(result["samples"]), 3)
        self.assertEqual(sample_offsets(4 * 3600 * 16000)[-1], (4 * 3600 - 30) * 16000)

    def test_invalid_audio_format_is_rejected_before_model_load(self):
        self.write_audio(sample_rate=8000)
        with self.assertRaisesRegex(ValueError, "16kHz"):
            self.detect()
        self.factory.assert_not_called()

    def test_english_only_model_is_rejected(self):
        self.write_audio()
        self.model.supported_languages = ["en"]
        with self.assertRaisesRegex(ValueError, "多语版本"):
            self.detect()
        self.model.detect_language.assert_not_called()

    def test_nonfinite_model_probability_requires_confirmation(self):
        self.write_audio()
        self.model.detect_language.return_value = ("en", float("nan"), [("en", float("nan"))])
        result = self.detect()
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["confidence"], 0)
        self.assertEqual(result["samples"][0]["confidence"], 0)

    def test_readiness_requires_complete_multilingual_model_files(self):
        base = Path(self.temp.name)
        folder = base / "whisper-tiny"
        folder.mkdir()
        for name in ("config.json", "tokenizer.json", "vocabulary.txt"):
            (folder / name).write_text("placeholder", encoding="utf-8")
        self.assertFalse(language_model_ready(base))
        with (folder / "model.bin").open("wb") as stream:
            stream.truncate(50_000_000)
        self.assertTrue(language_model_ready(base))
        (folder / "tokenizer.json").write_text("", encoding="utf-8")
        self.assertFalse(language_model_ready(base))


if __name__ == "__main__":
    unittest.main()
