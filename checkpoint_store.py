"""Atomic, checksummed local transcription checkpoints and per-job locking."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from core import AppError, Cancelled


UNIT_SCHEMA = 1
_KEY = re.compile(r"[a-z0-9_-]{1,80}")


class CheckpointInUse(AppError):
    """The checkpoint already belongs to another active pipeline."""


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("已停止检查恢复文件，已保存进度仍保留。")


def _stat_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _cross_view_identity(info):
    # Python/Windows path stat can expose creation time as st_ctime while fstat
    # exposes the change time after a rename. Compare ctime within each view.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def fingerprint(path, cancel=None):
    """Hash the complete file, refusing a file changed or replaced during reading.

    The returned identity excludes location and timestamps so moving the same
    recording is supported. Timestamps are only used to detect concurrent edits.
    """
    _cancelled(cancel)
    source = Path(path)
    digest = hashlib.sha256()
    try:
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            raise AppError("恢复文件不是普通文件，请重新关联原音视频。")
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _cross_view_identity(before) != _cross_view_identity(opened):
                raise AppError("校验期间文件发生变化，请停止修改文件后重试。")
            while True:
                _cancelled(cancel)
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            finished = os.fstat(stream.fileno())
        after = source.stat()
    except OSError as exc:
        raise AppError("无法读取恢复文件，请检查原音视频及恢复目录是否可用。") from exc
    _cancelled(cancel)
    if (_stat_identity(opened) != _stat_identity(finished)
            or _stat_identity(before) != _stat_identity(after)):
        raise AppError("校验期间文件发生变化，请停止修改文件后重试。")
    return {"size": before.st_size, "sha256": digest.hexdigest()}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _reject_constant(_value):
    raise ValueError("Non-finite JSON number")


class CheckpointStore:
    """One job's cache. The pipeline holds lock() while its worker writes units.

    Construction does not create files. Missing records return None; existing
    corrupt records raise AppError instead of silently discarding completed work.
    """

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.audio_path = self.directory / "audio.wav"

    fingerprint = staticmethod(fingerprint)

    @contextmanager
    def lock(self):
        """Acquire an OS-owned nonblocking lock; process exit releases ownership."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle = (self.directory / ".lock").open("a+b", buffering=0)
        except OSError as exc:
            raise AppError("无法打开恢复目录，请检查磁盘空间和目录权限。") from exc
        acquired = False
        try:
            # Windows byte-range locking needs a stable byte. Never truncate or
            # unlink this inode: another process may already be locking it.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise CheckpointInUse("这份文稿正在其他窗口处理，请等待该任务结束后再继续。") from exc
            yield self
        finally:
            try:
                if acquired:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    @staticmethod
    def _read(path):
        try:
            with path.open("r", encoding="utf-8") as stream:
                value = json.load(stream, parse_constant=_reject_constant)
        except FileNotFoundError:
            return None
        except (UnicodeError, ValueError) as exc:
            raise AppError("恢复数据损坏，已保留原文件。请保留现有文稿，重新创建转写任务。") from exc
        except OSError as exc:
            raise AppError("无法读取恢复数据，请检查恢复目录权限后重试。") from exc
        if not isinstance(value, dict):
            raise AppError("恢复数据格式无效，已保留原文件。请重新创建转写任务。")
        return value

    @staticmethod
    def _write(path, value):
        try:
            content = _encode(value)
        except (TypeError, ValueError) as exc:
            raise AppError("恢复结果包含无法保存的数据，上一份完整进度仍保留。") from exc
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".checkpoint-", suffix=".tmp", dir=path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(.02 * 2 ** attempt)
        except OSError as exc:
            raise AppError("无法保存恢复进度，上一份完整进度仍保留。请检查磁盘空间和目录权限。") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # An orphan temporary file is never read as a checkpoint.
                    pass

    def load_manifest(self):
        return self._read(self.directory / "manifest.json")

    def save_manifest(self, data):
        if not isinstance(data, dict):
            raise AppError("恢复清单必须是有效的任务记录。")
        self.load_manifest()
        self._write(self.directory / "manifest.json", data)

    def _unit_path(self, key):
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            raise AppError("恢复分块编号无效。")
        return self.directory / "units" / (key + ".json")

    def load_unit(self, key):
        value = self._read(self._unit_path(key))
        if value is None:
            return None
        data, checksum = value.get("data"), value.get("checksum")
        if (type(value.get("schema")) is not int or value["schema"] != UNIT_SCHEMA
                or not isinstance(data, dict) or not isinstance(checksum, str)
                or re.fullmatch(r"[a-f0-9]{64}", checksum) is None):
            raise AppError("恢复分块版本或格式无效，原文件已保留。请重新创建转写任务。")
        try:
            actual = hashlib.sha256(_encode(data)).hexdigest()
        except (TypeError, ValueError) as exc:
            raise AppError("恢复分块内容无效，原文件已保留。请重新创建转写任务。") from exc
        if not hmac.compare_digest(actual, checksum):
            raise AppError("恢复分块校验失败，原文件已保留。请重新创建转写任务。")
        return data

    def save_unit(self, key, data):
        path = self._unit_path(key)
        if not isinstance(data, dict):
            raise AppError("恢复分块必须是有效的结果记录。")
        self.load_unit(key)
        try:
            checksum = hashlib.sha256(_encode(data)).hexdigest()
        except (TypeError, ValueError) as exc:
            raise AppError("恢复结果包含无法保存的数据，上一份完整进度仍保留。") from exc
        self._write(path, {"schema": UNIT_SCHEMA, "checksum": checksum, "data": data})
