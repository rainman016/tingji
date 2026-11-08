import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg

from core import AppError, Cancelled, extract_audio, probe_media_duration


class MediaTests(unittest.TestCase):
    def test_real_video_audio_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "中文测试视频.mp4"
            target = Path(folder) / "audio.mp3"
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error",
                            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
                            "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(video)],
                           check=True, timeout=20, creationflags=flags, capture_output=True)
            duration = extract_audio(video, target, threading.Event())
            self.assertTrue(target.is_file())
            self.assertGreater(duration, 900)
            self.assertLess(duration, 1500)

    def test_missing_file_has_actionable_error(self):
        with self.assertRaisesRegex(AppError, "找不到"):
            extract_audio("missing-file.mp4", "output.mp3", threading.Event())

    def test_two_hour_english_video_is_within_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "long.mp4"
            video.touch()
            result = subprocess.CompletedProcess([], 1, stderr=b"Duration: 02:03:47.18\nStream #0:1: Audio: aac")
            with patch("core.subprocess.run", return_value=result):
                self.assertEqual(probe_media_duration(video), 7427180)

    def test_over_limit_is_rejected_before_decoding(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "too-long.mp4"
            video.touch()
            result = subprocess.CompletedProcess([], 1, stderr=b"Duration: 04:00:30.00\nStream #0:1: Audio: aac")
            with patch("core.subprocess.run", return_value=result), patch("core.subprocess.Popen") as decode:
                with self.assertRaisesRegex(AppError, "4 小时"):
                    extract_audio(video, Path(folder) / "audio.wav", threading.Event())
                decode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
