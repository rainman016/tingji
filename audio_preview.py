"""Asynchronous, local paragraph preview with one owned decoder and MCI device."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid
import wave

from process_control import start_owned_process


class PreviewError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def _decode_excerpt(source, start_ms, end_ms, target, cancel):
    """Called only by the preview worker after an explicit play request."""
    if cancel.is_set():
        raise _Cancelled()
    import imageio_ffmpeg
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-protocol_whitelist", "file,pipe", "-ss", f"{start_ms / 1000:.3f}", "-i", str(source),
               "-t", f"{(end_ms - start_ms) / 1000:.3f}", "-map", "0:a:0", "-vn", "-ac", "1",
               "-ar", "16000", "-c:a", "pcm_s16le", str(target)]
    try:
        with (target.parent / "decode.log").open("wb") as errors:
            with start_owned_process(command, stdout=subprocess.DEVNULL, stderr=errors,
                                     creationflags=flags, shell=False) as proc:
                started = time.monotonic()
                while proc.poll() is None:
                    if cancel.wait(.05):
                        raise _Cancelled()
                    if time.monotonic() - started > 60:
                        raise PreviewError("准备回听超时。请确认原文件保存在本机，或选择较短片段后重试。")
                if cancel.is_set():
                    raise _Cancelled()
                if proc.returncode:
                    raise PreviewError("无法解码该片段。请确认原文件可以播放且包含音轨，必要时重新导出为 MP4 或 WAV。")
    except OSError as exc:
        raise PreviewError("无法启动本地回听。请检查原文件及临时目录权限，并确认 FFmpeg 运行文件完整。") from exc


def _validate_wave(path, expected_ms):
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getcomptype() != "NONE" or audio.getnframes() == 0 or audio.getframerate() <= 0:
                raise PreviewError("该片段没有可播放的音频。请选择包含声音的段落。")
            actual_ms = audio.getnframes() * 1000 / audio.getframerate()
    except (OSError, EOFError, wave.Error) as exc:
        raise PreviewError("未能生成完整的回听音频。请确认原文件音轨正常后重试。") from exc
    # Saved durations come from FFmpeg's centisecond display. Permit that
    # rounding error, but reject a materially shorter or mismatched source.
    if actual_ms + 10 < expected_ms:
        raise PreviewError("选定片段超出了可播放的音轨范围。请缩小回听范围，或重新确认文字稿对应的原文件。")
    return round(actual_ms)


class _MciPlayer:
    """All methods must run on the same thread; each instance owns one unique alias."""
    def __init__(self):
        if os.name != "nt":
            raise PreviewError("当前回听功能需要 Windows 音频设备。")
        from ctypes import wintypes
        self._winmm = ctypes.WinDLL("winmm")
        self._winmm.mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HWND]
        self._winmm.mciSendStringW.restype = wintypes.DWORD
        self.alias = "tjpreview_" + uuid.uuid4().hex
        self.opened = False
        self._pause_position = 0

    def _send(self, command):
        response = ctypes.create_unicode_buffer(256)
        code = self._winmm.mciSendStringW(command, response, len(response), None)
        if code:
            raise PreviewError("音频设备无法完成回听。请在 Windows 声音设置中确认输出设备可用，关闭独占音频设备的程序后重试。")
        return response.value.strip()

    def open(self, path):
        self._send(f'open "{path}" type waveaudio alias {self.alias}')
        self.opened = True
        self._send(f"set {self.alias} time format milliseconds")

    def play(self):
        self._send(f"play {self.alias} from 0")

    def pause(self):
        self._send(f"pause {self.alias}")
        self._pause_position = int(self._send(f"status {self.alias} position"))

    def resume(self):
        # waveaudio supports PLAY from its paused position on Windows versions
        # whose driver does not implement the generic MCI RESUME command.
        self._send(f"play {self.alias} from {self._pause_position}")

    def poll(self):
        return {"mode": self._send(f"status {self.alias} mode"),
                "position_ms": int(self._send(f"status {self.alias} position"))}

    def close(self):
        if self.opened:
            self._send(f"close {self.alias}")
            self.opened = False


class AudioPreview:
    """Tk-safe commands; poll positions are absolute milliseconds in the source.

    Constructing the object performs no I/O and starts no thread. The optional
    decoder and mci_factory support silent tests; production callers omit them.
    stop() requests cancellation without blocking Tk. close() additionally waits
    for owned resources to be released and raises PreviewError if that cannot be
    confirmed within ten seconds.
    """
    def __init__(self, *, decoder=None, mci_factory=None):
        self._decoder = decoder or _decode_excerpt
        self._mci_factory = mci_factory or _MciPlayer
        self._lock = threading.RLock()
        self._commands = queue.Queue()
        self._thread = None
        self._generation = 0
        self._cancel = None
        self._closed = False
        self._close_error = ""
        self._close_done = threading.Event()
        self._snapshot = {"state": "idle", "position_ms": 0, "error": ""}

    def _publish(self, generation, state, position_ms=0, error=""):
        with self._lock:
            if generation == self._generation:
                self._snapshot = {"state": state, "position_ms": int(position_ms), "error": error}

    def play(self, source, start_ms, end_ms):
        with self._lock:
            if self._closed:
                raise PreviewError("回听播放器已关闭，请重新打开程序后重试。")
            if self._cancel is not None:
                self._cancel.set()
            self._generation += 1
            generation = self._generation
            cancel = self._cancel = threading.Event()
            valid = (isinstance(start_ms, int) and not isinstance(start_ms, bool)
                     and isinstance(end_ms, int) and not isinstance(end_ms, bool)
                     and 0 <= start_ms < end_ms)
            if not valid:
                self._snapshot = {"state": "error", "position_ms": 0,
                                  "error": "回听时间范围无效。请选择开始时间早于结束时间的有效段落。"}
                self._commands.put(("discard", generation, None))
                return
            try:
                source = Path(source)
            except (TypeError, ValueError):
                self._snapshot = {"state": "error", "position_ms": 0,
                                  "error": "原文件路径无效。请重新选择对应的视频或音频文件。"}
                self._commands.put(("discard", generation, None))
                return
            self._snapshot = {"state": "preparing", "position_ms": start_ms, "error": ""}
            self._commands.put(("play", generation, (source, start_ms, end_ms, cancel)))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="audio-preview-worker", daemon=False)
                self._thread.start()

    def pause(self):
        with self._lock:
            if not self._closed:
                self._commands.put(("pause", self._generation, None))

    def resume(self):
        with self._lock:
            if not self._closed:
                self._commands.put(("resume", self._generation, None))

    def stop(self):
        with self._lock:
            if self._closed:
                return
            if self._cancel is not None:
                self._cancel.set()
            self._generation += 1
            generation = self._generation
            if self._thread is None:
                self._snapshot = {"state": "idle", "position_ms": 0, "error": ""}
            else:
                self._commands.put(("stop", generation, None))

    def poll(self):
        with self._lock:
            return dict(self._snapshot)

    def close(self):
        with self._lock:
            self._closed = True
            if self._cancel is not None:
                self._cancel.set()
            self._generation += 1
            self._close_error = ""
            self._close_done.clear()
            thread = self._thread
            if thread is not None and thread.is_alive():
                self._commands.put(("close", self._generation, None))
            else:
                self._close_done.set()
        if not self._close_done.wait(timeout=10):
            raise PreviewError("回听尚未完全停止。请稍候再关闭窗口，避免遗留临时音频。")
        if self._close_error:
            raise PreviewError(self._close_error)
        if thread is not None:
            thread.join(timeout=1)
            if thread.is_alive():
                raise PreviewError("回听线程尚未退出，请稍候再关闭窗口。")
        with self._lock:
            self._snapshot = {"state": "idle", "position_ms": 0, "error": ""}

    def _run(self):
        player, temporary, active = None, None, None

        def dispose():
            nonlocal player, temporary, active
            # A failed close retains ownership and its WAV for the next stop/close
            # attempt; never start another decoder while cleanup is unresolved.
            if player is not None:
                player.close()
                player = None
            if temporary is not None:
                temporary.cleanup()
                temporary = None
            active = None

        while True:
            try:
                command, generation, request = self._commands.get(timeout=.05)
            except queue.Empty:
                command, generation, request = "tick", self._generation, None
            with self._lock:
                current = self._generation
            if generation != current:
                continue
            try:
                if command in ("play", "stop", "close", "discard"):
                    dispose()
                if command == "close":
                    self._publish(generation, "idle")
                    self._close_done.set()
                    return
                if command == "discard":
                    continue
                if command == "stop":
                    self._publish(generation, "idle")
                    continue
                if command == "play":
                    source, start_ms, end_ms, cancel = request
                    if cancel.is_set():
                        continue
                    if not source.is_file():
                        raise PreviewError("找不到原视频或音频文件。请将文件恢复到原位置，或重新选择文件并生成对应文字稿。")
                    temporary = tempfile.TemporaryDirectory(prefix="tingji-preview-")
                    audio = Path(temporary.name) / "excerpt.wav"
                    self._decoder(source, start_ms, end_ms, audio, cancel)
                    if cancel.is_set():
                        raise _Cancelled()
                    actual_ms = _validate_wave(audio, end_ms - start_ms)
                    end_ms = min(end_ms, start_ms + actual_ms)
                    player = self._mci_factory()
                    player.open(audio)
                    if cancel.is_set():
                        raise _Cancelled()
                    player.play()
                    active = (generation, start_ms, end_ms)
                    self._publish(generation, "playing", start_ms)
                elif active is not None:
                    active_generation, start_ms, end_ms = active
                    if active_generation != generation:
                        continue
                    state = self.poll()["state"]
                    if state == "error":
                        continue
                    if command == "pause" and state == "playing":
                        player.pause()
                    elif command == "resume" and state == "paused":
                        player.resume()
                    info = player.poll()
                    position = min(end_ms, max(start_ms, start_ms + int(info["position_ms"])))
                    if info["mode"] == "stopped":
                        dispose()
                        self._publish(generation, "finished", end_ms)
                    else:
                        self._publish(generation, "paused" if info["mode"] == "paused" else "playing", position)
            except _Cancelled:
                try:
                    dispose()
                except Exception as exc:
                    self._publish(generation, "error", error=f"回听资源尚未释放，请再次停止或关闭回听。{str(exc)[:200]}")
            except Exception as exc:
                error = str(exc) if isinstance(exc, PreviewError) else "回听失败。请检查原文件、Windows 输出设备和临时目录可写权限后重试。"
                try:
                    dispose()
                except Exception:
                    error += " 回听资源尚未释放，请再次停止或关闭回听。"
                self._publish(generation, "error", error=error)
                if command == "close":
                    self._close_error = error
                    self._close_done.set()
