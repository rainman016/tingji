import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_worker import atomic_json, main, write_progress


class WorkerIOTests(unittest.TestCase):
    def test_transient_access_denied_retries_and_publishes_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            atomic_json(path, {"old": True})
            replace = os.replace
            attempts = []

            def briefly_locked(source, target):
                attempts.append(target)
                if len(attempts) < 3:
                    raise PermissionError("file in use")
                return replace(source, target)

            with patch("local_worker.os.replace", side_effect=briefly_locked), \
                 patch("local_worker.time.sleep"):
                atomic_json(path, {"text": "完整结果"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"text": "完整结果"})
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_persistent_result_failure_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("local_worker.os.replace", side_effect=PermissionError("locked")), \
                 patch("local_worker.time.sleep"):
                with self.assertRaises(PermissionError):
                    atomic_json(Path(folder) / "result.json", {"ok": True})

    def test_worker_finishes_even_when_all_progress_replacements_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            request, result, progress = (root / name for name in
                                         ("request.json", "result.json", "progress.json"))
            request.write_text('{"mode":"asr"}', encoding="utf-8")
            replace = os.replace

            def progress_locked(source, target):
                if target == progress:
                    raise PermissionError("progress locked by reader")
                return replace(source, target)

            def infer(request, report):
                report("正在识别")
                report("识别完成")
                return {"segments": [{"text": "Final words."}]}

            with patch("local_worker.sys.argv", ["worker", str(request), str(result), str(progress)]), \
                 patch("local_worker.block_network"), \
                 patch("local_worker.transcribe", side_effect=infer), \
                 patch("local_worker.os.replace", side_effect=progress_locked), \
                 patch("local_worker.time.sleep"):
                self.assertEqual(main(), 0)
            saved = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(saved["ok"])
            self.assertEqual(saved["data"]["segments"][0]["text"], "Final words.")
            write_progress(progress, "已恢复")
            self.assertEqual(json.loads(progress.read_text(encoding="utf-8"))["message"], "已恢复")

    @unittest.skipUnless(os.name == "nt", "Windows file-sharing semantics")
    def test_real_windows_reader_lock_does_not_abort_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.json"
            write_progress(path, "上一条进度")
            with path.open("rb"):
                with self.assertRaises(PermissionError):
                    atomic_json(path, {"message": "文件被占用"})
                write_progress(path, "继续识别")
            write_progress(path, "新的进度")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["message"], "新的进度")


if __name__ == "__main__":
    unittest.main()
