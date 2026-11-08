import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

import prepare_runtime

from bootstrap_local import allowed_path, download_ranges


class RangeResponse:
    status_code = 206
    def __init__(self, data, start, end):
        self.content = data[start:end+1]
        self.headers = {"Content-Range": f"bytes {start}-{end}/{len(data)}"}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def raise_for_status(self):
        pass


class InstallerTests(unittest.TestCase):
    def test_cu126_pair_is_reused_without_runtime_download(self):
        with patch("prepare_runtime.version", return_value="2.8.0+cu126"), \
             patch("prepare_runtime.requests.Session") as session, \
             patch("prepare_runtime.subprocess.run") as run:
            prepare_runtime.main(asr_only=True)
        session.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertIn(str(prepare_runtime.ROOT / "requirements-local.txt"), run.call_args.args[0])

    def test_old_runtime_is_replaced_with_consistent_cu126_pair(self):
        client = MagicMock()
        client.get.return_value.text = '<a href="https://download.pytorch.org/whl/cu126/torch-2.8.0%2Bcu126-cp312-cp312-win_amd64.whl#sha256=abc">torch</a>'
        client.get.return_value.headers = {"Content-Range": "bytes 0-0/100"}
        with patch("prepare_runtime.version", return_value="2.8.0+cu129"), \
             patch("prepare_runtime.requests.Session") as session, \
             patch("prepare_runtime.download") as download, \
             patch("prepare_runtime.subprocess.run") as run:
            session.return_value.__enter__.return_value = client
            prepare_runtime.main(asr_only=True)
        self.assertEqual(client.get.call_args_list[0].args[0], "https://download.pytorch.org/whl/cu126/torch/")
        self.assertEqual(download.call_args.args[2].name, "torch-2.8.0+cu126-cp312-cp312-win_amd64.whl")
        command = run.call_args_list[0].args[0]
        self.assertIn("torchaudio==2.8.0+cu126", command)
        self.assertIn("https://download.pytorch.org/whl/cu126", command)
        self.assertNotIn("cu129", " ".join(command))

    def test_wrong_python_is_rejected_before_installing(self):
        with patch("prepare_runtime.sys.version_info", (3, 13)), \
             patch("prepare_runtime.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "Python 3.12"):
                prepare_runtime.main()
        run.assert_not_called()

    def test_resume_ranges_preserves_order_and_checks_hash(self):
        data = bytes(range(256)) * (1024 * 65)
        calls = []
        class Session:
            def get(self, url, headers, timeout):
                start, end = map(int, headers["Range"][6:].split("-"))
                calls.append((start, end))
                return RangeResponse(data, start, end)
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as folder, patch("bootstrap_local.requests.Session", Session):
            path = Path(folder) / "weights.bin"
            path.with_suffix(".bin.part").write_bytes(data[:137])
            download_ranges(None, "https://example.test/model", path, len(data), hashlib.sha256(data).hexdigest())
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(min(start for start, _ in calls), 137)
            self.assertFalse(path.with_suffix(".bin.part").exists())

    def test_invalid_paths_are_rejected(self):
        for name in ("../outside", "/absolute", "a\\b"):
            with self.assertRaises(ValueError):
                allowed_path(name)

    def test_corrupt_completed_partial_is_removed_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "model.bin"
            partial = target.with_suffix(".bin.part")
            partial.write_bytes(b"bad")
            with self.assertRaises(ValueError):
                download_ranges(None, "unused", target, 3, hashlib.sha256(b"yes").hexdigest())
            self.assertFalse(target.exists())
            self.assertFalse(partial.exists())


if __name__ == "__main__":
    unittest.main()
