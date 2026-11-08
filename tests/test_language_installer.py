import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import install_language


class LanguageInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.models = Path(self.temp.name) / "models"
        self.info = {"sha": "abcdef0123456789", "siblings": [
            {"rfilename": "model.bin", "size": 75_538_878, "lfs": {"sha256": "a" * 64}},
            {"rfilename": "config.json", "size": 2251},
            {"rfilename": "tokenizer.json", "size": 2_202_939},
            {"rfilename": "vocabulary.txt", "size": 459861},
            {"rfilename": "README.md", "size": 1990},
            {"rfilename": ".gitattributes", "size": 1480},
        ]}
        self.client = Mock()

    def tearDown(self):
        self.temp.cleanup()

    def install(self, models_only=True):
        with patch.object(install_language, "ROOT", Path(self.temp.name)), \
                patch.object(install_language, "MODELS", self.models), \
                patch.object(install_language.requests, "Session") as session, \
                patch.object(install_language, "model_info", return_value=self.info), \
                patch.object(install_language, "download") as download, \
                patch.object(install_language.subprocess, "run") as run:
            session.return_value.__enter__.return_value = self.client
            self.download = download
            install_language.install(models_only=models_only)
            return download, run

    def test_model_downloads_use_revision_and_weight_hash_without_credentials(self):
        download, run = self.install()
        run.assert_not_called()
        self.assertFalse(self.client.trust_env)
        self.assertEqual(download.call_count, 5)
        weight = next(call for call in download.call_args_list if call.args[2].name == "model.bin")
        self.assertIn("/resolve/abcdef0123456789/model.bin", weight.args[1])
        self.assertEqual(weight.args[2].parent, self.models / "whisper-tiny")
        self.assertEqual(weight.kwargs["expected_hash"], "a" * 64)
        self.assertEqual(weight.kwargs["expected_size"], 75_538_878)
        manifest = json.loads((self.models / "language-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["repository"], "Systran/faster-whisper-tiny")
        self.assertEqual(manifest["revision"], self.info["sha"])
        self.assertFalse((self.models / "language-manifest.tmp").exists())

    def test_standalone_installer_reuses_pinned_english_dependencies(self):
        _, run = self.install(models_only=False)
        command = run.call_args.args[0]
        self.assertIn("--isolated", command)
        self.assertIn("--no-input", command)
        self.assertIn(str(Path(self.temp.name) / "requirements-english.txt"), command)
        self.assertTrue(run.call_args.kwargs["check"])

    def test_missing_required_files_stop_before_download(self):
        self.info["siblings"] = self.info["siblings"][:2]
        with self.assertRaisesRegex(ValueError, "清单不完整"):
            self.install()
        self.download.assert_not_called()
        self.assertFalse((self.models / "language-manifest.json").exists())

    def test_missing_weight_hash_is_rejected(self):
        self.info["siblings"][0]["lfs"] = {}
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.install()
        self.assertFalse((self.models / "language-manifest.json").exists())

    def test_path_traversal_in_metadata_is_rejected(self):
        self.info["siblings"].append({"rfilename": "../outside", "size": 1})
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.models / "language-manifest.json").exists())

    def test_incomplete_download_keeps_previous_manifest(self):
        self.models.mkdir()
        target = self.models / "language-manifest.json"
        target.write_text('{"revision":"previous"}', encoding="utf-8")
        with patch.object(install_language, "MODELS", self.models), \
                patch.object(install_language.requests, "Session") as session, \
                patch.object(install_language, "model_info", return_value=self.info), \
                patch.object(install_language, "download", side_effect=RuntimeError("下载失败")):
            session.return_value.__enter__.return_value = self.client
            with self.assertRaises(RuntimeError):
                install_language.install(models_only=True)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["revision"], "previous")


if __name__ == "__main__":
    unittest.main()
