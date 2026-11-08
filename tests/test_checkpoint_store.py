import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from checkpoint_store import CheckpointStore, fingerprint
from core import AppError, Cancelled


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "checkpoint"
        self.store = CheckpointStore(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_checkpoint_is_read_only_and_returns_none(self):
        self.assertIsNone(self.store.load_manifest())
        self.assertIsNone(self.store.load_unit("asr-0000"))
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.store.audio_path, self.directory / "audio.wav")

    def test_manifest_and_unit_round_trip_preserve_types_and_unicode(self):
        manifest = {"language": "zh", "source": {"size": 100, "sha256": "digest"}}
        unit = {"segments": [{"text": "你好 😀", "start_ms": 0}], "embedding": [0.5, -0.25]}
        with self.store.lock():
            self.store.save_manifest(manifest)
            self.store.save_unit("asr-0000", unit)
        self.assertEqual(self.store.load_manifest(), manifest)
        self.assertEqual(self.store.load_unit("asr-0000"), unit)

    def test_replace_failure_preserves_last_complete_checkpoint(self):
        original = {"segments": [{"text": "上一份完整内容"}]}
        self.store.save_unit("asr-0", original)
        with patch("checkpoint_store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(AppError, "上一份完整进度仍保留"):
                self.store.save_unit("asr-0", {"segments": [{"text": "不能写入"}]})
        self.assertEqual(self.store.load_unit("asr-0"), original)
        self.assertEqual(list((self.directory / "units").glob(".checkpoint-*.tmp")), [])

    def test_fsync_failure_preserves_manifest(self):
        self.store.save_manifest({"phase": "asr"})
        with patch("checkpoint_store.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(AppError):
                self.store.save_manifest({"phase": "finished"})
        self.assertEqual(self.store.load_manifest(), {"phase": "asr"})

    def test_transient_windows_replace_denial_retries(self):
        replace = os.replace
        with patch("checkpoint_store.os.replace", side_effect=[PermissionError(), PermissionError(), None]) as mocked, \
                patch("checkpoint_store.time.sleep"):
            def attempt(source, target):
                if mocked.call_count < 3:
                    raise PermissionError("reader lock")
                return replace(source, target)
            mocked.side_effect = attempt
            self.store.save_unit("asr-0", {"text": "完成"})
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(self.store.load_unit("asr-0"), {"text": "完成"})

    def test_corrupt_manifest_is_not_treated_as_missing(self):
        self.directory.mkdir()
        path = self.directory / "manifest.json"
        for content in ("{broken", "[]", '{"duration": NaN}'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(AppError):
                    self.store.load_manifest()
                self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_unit_tampering_and_wrong_schema_are_rejected(self):
        self.store.save_unit("asr-0", {"text": "original"})
        path = self.directory / "units" / "asr-0.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        variants = [dict(original, schema=2), dict(original, schema=True),
                    dict(original, data={"text": "changed"}), dict(original, checksum="not-a-hash")]
        for value in variants:
            with self.subTest(value=value):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(AppError):
                    self.store.load_unit("asr-0")
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), value)

    def test_invalid_keys_cannot_escape_checkpoint_directory(self):
        for key in ("", "../other", "a/b", "a\\b", "UPPER", "x" * 81, None, 1):
            with self.subTest(key=key):
                with self.assertRaises(AppError):
                    self.store.load_unit(key)
                with self.assertRaises(AppError):
                    self.store.save_unit(key, {})
        self.assertFalse(self.directory.exists())

    def test_saving_cannot_silently_overwrite_corrupt_existing_data(self):
        self.store.save_manifest({"phase": "asr"})
        self.store.save_unit("asr-0", {"text": "original"})
        manifest = self.directory / "manifest.json"
        unit = self.directory / "units" / "asr-0.json"
        manifest.write_text("{broken", encoding="utf-8")
        unit.write_text("{broken", encoding="utf-8")
        with self.assertRaises(AppError):
            self.store.save_manifest({"phase": "new"})
        with self.assertRaises(AppError):
            self.store.save_unit("asr-0", {"text": "new"})
        self.assertEqual(manifest.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(unit.read_text(encoding="utf-8"), "{broken")

    def test_non_finite_or_non_object_data_is_rejected_before_replacing(self):
        self.store.save_unit("asr-0", {"text": "保留"})
        for data in ([], {"embedding": [float("nan")]}, {"end_ms": float("inf")}):
            with self.subTest(data=data), self.assertRaises(AppError):
                self.store.save_unit("asr-0", data)
        self.assertEqual(self.store.load_unit("asr-0"), {"text": "保留"})

    def test_lock_prevents_concurrent_owner_and_can_be_reacquired(self):
        other = CheckpointStore(self.directory)
        with self.store.lock():
            with self.assertRaisesRegex(AppError, "其他窗口"):
                with other.lock():
                    self.fail("不应取得同一份任务的锁")
        self.assertTrue((self.directory / ".lock").is_file())
        with other.lock():
            other.save_manifest({"recovered": True})
        self.assertEqual(self.store.load_manifest(), {"recovered": True})

    def test_exception_inside_lock_releases_ownership(self):
        with self.assertRaisesRegex(ValueError, "work failed"):
            with self.store.lock():
                raise ValueError("work failed")
        with CheckpointStore(self.directory).lock():
            self.store.save_manifest({"recovered": True})

    def test_fingerprint_detects_content_and_is_independent_of_location(self):
        first, moved = Path(self.temp.name) / "original.wav", Path(self.temp.name) / "moved.wav"
        first.write_bytes(b"same audio bytes")
        expected = {"size": first.stat().st_size, "sha256": hashlib.sha256(first.read_bytes()).hexdigest()}
        self.assertEqual(fingerprint(first), expected)
        first.rename(moved)
        self.assertEqual(self.store.fingerprint(moved), expected)
        moved.write_bytes(b"different audio!")
        self.assertNotEqual(fingerprint(moved), expected)

    def test_fingerprint_honors_cancellation_before_reading(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            fingerprint(Path(self.temp.name) / "missing.wav", cancel)

    def test_fingerprint_rejects_a_file_changed_during_hashing(self):
        source = Path(self.temp.name) / "audio.wav"
        source.write_bytes(b"a" * 4096)
        before = source.stat()
        real_digest = hashlib.sha256()
        def change_file(chunk):
            real_digest.update(chunk)
            source.write_bytes(b"b" * 4096)
            os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        with patch("checkpoint_store.hashlib.sha256") as factory:
            factory.return_value.update.side_effect = change_file
            factory.return_value.hexdigest.side_effect = real_digest.hexdigest
            with self.assertRaisesRegex(AppError, "文件发生变化"):
                fingerprint(source)

    def test_fingerprint_honors_cancellation_during_reading(self):
        source = Path(self.temp.name) / "audio.wav"
        source.write_bytes(b"a" * 4096)
        cancel = threading.Event()
        with patch("checkpoint_store.hashlib.sha256") as factory:
            factory.return_value.update.side_effect = lambda _chunk: cancel.set()
            with self.assertRaises(Cancelled):
                fingerprint(source, cancel)


if __name__ == "__main__":
    unittest.main()
