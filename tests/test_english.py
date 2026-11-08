import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import new_document
from english_worker import phrase_segments
from local_service import LocalService, run_local_pipeline
from media_fixture import copy_audio, make_source


class EnglishTests(unittest.TestCase):
    def test_sentence_split_preserves_last_words_and_timestamps(self):
        words = [SimpleNamespace(start=0, end=.4, word="Hello"),
                 SimpleNamespace(start=.4, end=1.2, word=" world."),
                 SimpleNamespace(start=1.4, end=2, word=" Final"),
                 SimpleNamespace(start=2, end=3, word=" words")]
        parts = phrase_segments(SimpleNamespace(words=words), 4000)
        self.assertEqual([x["text"] for x in parts], ["Hello world.", "Final words"])
        self.assertEqual(parts[-1]["end_ms"], 3000)

    def test_no_word_timestamps_keeps_the_transcript(self):
        part = SimpleNamespace(words=None, start=4, end=6, text="Fallback sentence.")
        self.assertEqual(phrase_segments(part, 10000)[0]["start_ms"], 4000)

    def test_english_routes_to_local_worker_without_chinese_model(self):
        service = LocalService(threading.Event(), lambda _: None)
        with patch("local_service.local_status", return_value={"asr": False, "english": True}), \
             patch.object(service, "run_worker", return_value={"segments": []}) as run:
            service.transcribe("test.wav", 2, language="en")
            self.assertEqual(run.call_args.args[0]["mode"], "english")
            self.assertEqual(run.call_args.args[0]["speaker_count"], 2)

    def test_pipeline_persists_english_engine_and_language(self):
        class EnglishService:
            def __init__(self, *args): pass
            def transcribe(self, audio, count, language, checkpoint_dir=None):
                if language != "en": raise AssertionError("English was not selected")
                return {"segments": [{"start_ms": 0, "end_ms": 1000, "speaker": "0", "text": "English text."}],
                        "speakers": {"0": "说话人 1"}, "device": "cpu", "seconds": 1}
        with tempfile.TemporaryDirectory() as folder:
            doc = new_document(make_source(folder, duration_ms=3000, name="English.wav"))
            run_local_pipeline(doc, folder, threading.Event(), lambda _: None, language="en", auto_summary=False,
                               service_factory=EnglishService, extractor=copy_audio)
            self.assertEqual(doc["language"], "en")
            self.assertEqual(doc["engine"], "local-whisper-small-en")
            self.assertEqual(doc["duration_ms"], 3000)
