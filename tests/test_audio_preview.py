"""Silent preview tests: no real MCI device, FFmpeg, or user media is used."""
import tempfile
import threading
import time
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

from audio_preview import AudioPreview, PreviewError, _Cancelled, _decode_excerpt


def write_pcm(target, milliseconds):
    with wave.open(str(target), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * (milliseconds * 16))


class FakePlayer:
    def __init__(self, records):
        self.records = records
        self.mode = "stopped"
        self.position = 0
        self.path = None
        self.fail_close = False

    def record(self, operation):
        self.records.append((operation, threading.get_ident()))

    def open(self, path):
        self.record("open")
        self.path = path

    def play(self):
        self.record("play")
        self.mode = "playing"

    def pause(self):
        self.record("pause")
        self.mode = "paused"

    def resume(self):
        self.record("resume")
        self.mode = "playing"

    def poll(self):
        self.record("poll")
        return {"mode": self.mode, "position_ms": self.position}

    def close(self):
        self.record("close")
        if self.fail_close:
            raise PreviewError("测试设备尚未关闭")
        self.mode = "stopped"


class AudioPreviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.source = Path(self.folder.name) / "source.mp4"
        self.source.touch()
        self.records, self.players, self.decoded_paths = [], [], []
        self.preview = None

    def tearDown(self):
        for player in self.players:
            player.fail_close = False
        if self.preview:
            self.preview.close()
        self.folder.cleanup()

    def decoder(self, source, start_ms, end_ms, target, cancel):
        self.decoded_paths.append(target)
        if cancel.is_set():
            raise _Cancelled()
        write_pcm(target, end_ms - start_ms)

    def factory(self):
        player = FakePlayer(self.records)
        self.players.append(player)
        return player

    def create(self, decoder=None):
        self.preview = AudioPreview(decoder=decoder or self.decoder, mci_factory=self.factory)
        return self.preview

    def await_state(self, state):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = self.preview.poll()
            if current["state"] == state:
                return current
            time.sleep(.01)
        self.fail(f"未到达 {state}：{self.preview.poll()}")

    def test_constructing_and_polling_does_not_prepare_or_play(self):
        preview = self.create()
        self.assertEqual(preview.poll(), {"state": "idle", "position_ms": 0, "error": ""})
        self.assertIsNone(preview._thread)
        self.assertFalse(self.players)
        self.assertFalse(self.decoded_paths)

    def test_pause_resume_and_all_device_calls_stay_on_one_worker_thread(self):
        preview = self.create()
        preview.play(self.source, 1000, 2000)
        self.await_state("playing")
        self.players[0].position = 200
        preview.pause()
        self.assertEqual(self.await_state("paused")["position_ms"], 1200)
        preview.resume()
        self.await_state("playing")
        preview.stop()
        self.await_state("idle")
        ids = {thread_id for _, thread_id in self.records}
        self.assertEqual(len(ids), 1)
        self.assertNotIn(threading.get_ident(), ids)
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_new_play_cancels_preparation_and_latest_request_wins_without_parallel_decode(self):
        entered = threading.Event()
        active, maximum = 0, 0
        def decode(source, start_ms, end_ms, target, cancel):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            self.decoded_paths.append(target)
            try:
                if start_ms == 0:
                    entered.set()
                    if not cancel.wait(2):
                        raise AssertionError("旧请求未收到取消")
                    raise _Cancelled()
                write_pcm(target, end_ms - start_ms)
            finally:
                active -= 1
        preview = self.create(decode)
        preview.play(self.source, 0, 1000)
        self.assertTrue(entered.wait(2))
        preview.play(self.source, 1000, 2000)
        preview.play(self.source, 2000, 3000)
        self.assertEqual(self.await_state("playing")["position_ms"], 2000)
        self.assertEqual(maximum, 1)
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_stop_during_preparation_cleans_temp_and_never_opens_audio_device(self):
        entered = threading.Event()
        def decode(source, start_ms, end_ms, target, cancel):
            self.decoded_paths.append(target)
            entered.set()
            if not cancel.wait(2):
                raise AssertionError("停止未送达解码器")
            raise _Cancelled()
        preview = self.create(decode)
        preview.play(self.source, 0, 1000)
        self.assertTrue(entered.wait(2))
        preview.stop()
        self.await_state("idle")
        self.assertFalse(self.players)
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_finish_releases_wave_and_reports_absolute_end(self):
        preview = self.create()
        preview.play(self.source, 5000, 6000)
        self.await_state("playing")
        self.players[0].mode = "stopped"
        self.assertEqual(self.await_state("finished")["position_ms"], 6000)
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_duration_display_rounding_plays_and_reports_actual_end(self):
        def decode(source, start_ms, end_ms, target, cancel):
            self.decoded_paths.append(target)
            write_pcm(target, end_ms - start_ms - 5)
        preview = self.create(decode)
        preview.play(self.source, 5000, 6000)
        self.await_state("playing")
        self.players[0].mode = "stopped"
        self.assertEqual(self.await_state("finished")["position_ms"], 5995)

    def test_missing_source_and_invalid_range_are_actionable_without_decoder(self):
        preview = self.create()
        preview.play(self.source, 200, 100)
        self.assertIn("范围无效", self.await_state("error")["error"])
        self.assertIsNone(preview._thread)
        preview.play(self.source.with_name("missing.mp4"), 0, 1000)
        self.assertIn("找不到原", self.await_state("error")["error"])
        self.assertFalse(self.decoded_paths)
        preview.stop()
        self.await_state("idle")

    def test_short_decoded_audio_is_rejected_instead_of_silently_truncating(self):
        def decode(source, start_ms, end_ms, target, cancel):
            self.decoded_paths.append(target)
            write_pcm(target, 500)
        preview = self.create(decode)
        preview.play(self.source, 0, 1000)
        self.assertIn("超出了", self.await_state("error")["error"])
        self.assertFalse(self.players)
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_decoder_error_keeps_message_and_releases_temp(self):
        def decode(source, start_ms, end_ms, target, cancel):
            self.decoded_paths.append(target)
            raise PreviewError("无法解码，请转换为 WAV 后重试")
        preview = self.create(decode)
        preview.play(self.source, 0, 1000)
        self.assertIn("转换为 WAV", self.await_state("error")["error"])
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_unavailable_output_device_reports_error_and_releases_prepared_audio(self):
        def unavailable():
            raise PreviewError("请在 Windows 声音设置中确认输出设备可用")
        self.preview = AudioPreview(decoder=self.decoder, mci_factory=unavailable)
        self.preview.play(self.source, 0, 1000)
        self.assertIn("输出设备", self.await_state("error")["error"])
        self.assertFalse(self.decoded_paths[0].parent.exists())

    def test_default_decoder_uses_owned_process_exact_range_and_no_shell(self):
        class CompletedProcess:
            returncode = 0
            def poll(self):
                return 0
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        target = Path(self.folder.name) / "excerpt.wav"
        with patch("audio_preview.start_owned_process", return_value=CompletedProcess()) as start, \
             patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg.exe"):
            _decode_excerpt(self.source, 1250, 2750, target, threading.Event())
        command = start.call_args.args[0]
        self.assertFalse(start.call_args.kwargs["shell"])
        self.assertEqual(command[command.index("-ss") + 1], "1.250")
        self.assertEqual(command[command.index("-t") + 1], "1.500")
        self.assertEqual(command[command.index("-i") + 1], str(self.source))

    def test_close_cancels_preparation_and_joins_worker(self):
        entered = threading.Event()
        def decode(source, start_ms, end_ms, target, cancel):
            self.decoded_paths.append(target)
            entered.set()
            if not cancel.wait(2):
                raise AssertionError("关闭未送达解码器")
            raise _Cancelled()
        preview = self.create(decode)
        preview.play(self.source, 0, 1000)
        self.assertTrue(entered.wait(2))
        preview.close()
        self.assertFalse(preview._thread.is_alive())
        self.assertFalse(self.decoded_paths[0].parent.exists())
        with self.assertRaises(PreviewError):
            preview.play(self.source, 0, 1000)

    def test_failed_close_is_reported_and_can_be_retried_without_losing_ownership(self):
        preview = self.create()
        preview.play(self.source, 0, 1000)
        self.await_state("playing")
        self.players[0].fail_close = True
        with self.assertRaises(PreviewError):
            preview.close()
        self.assertTrue(preview._thread.is_alive())
        self.assertTrue(self.decoded_paths[0].exists())
        self.players[0].fail_close = False
        preview.close()
        self.assertFalse(preview._thread.is_alive())
        self.assertFalse(self.decoded_paths[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
