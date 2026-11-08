"""Local media processing and transcript documents. No credentials are persisted."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path


class AppError(Exception):
    pass


class Cancelled(AppError):
    pass


class SummaryGPUUnavailable(AppError):
    """A GPU summary attempt failed before a trustworthy result was available."""


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("已停止处理，已经保存的结果仍保留。")


def timestamp(ms, srt=False):
    ms = max(0, round(ms))
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    value = f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{value},{millis:03}" if srt else value


def new_document(source, region="北京", workspace=""):
    return {"schema": 1, "id": uuid.uuid4().hex, "title": Path(source).stem,
            "source": str(source), "created_at": datetime.now().isoformat(timespec="seconds"),
            "region": region, "workspace": workspace, "status": "准备中", "task_id": "",
            "duration_ms": 0, "segments": [], "speakers": {}, "summary": "",
            "summary_stale": False, "language_mode": "auto", "error": ""}


def save_document(document, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"[a-f0-9]{32}", document["id"]):
        raise AppError("文稿编号无效。")
    target = directory / (document["id"] + ".json")
    # Atomic replacement preserves the last complete result if the application stops.
    fd, temporary = tempfile.mkstemp(prefix=".save-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def parse_transcript(payload):
    segments, speakers = [], {}
    for channel in payload.get("transcripts", []):
        for sentence in channel.get("sentences", []):
            text = str(sentence.get("text", "")).strip()
            if not text:
                continue
            begin, end = sentence.get("begin_time"), sentence.get("end_time")
            if not isinstance(begin, (int, float)) or not isinstance(end, (int, float)) or end <= begin:
                raise AppError("服务返回了无效时间戳，未生成可能错位的字幕。")
            speaker = str(sentence["speaker_id"]) if sentence.get("speaker_id") is not None else "unknown"
            if speaker not in speakers:
                speakers[speaker] = "未区分说话人" if speaker == "unknown" else f"说话人 {len(speakers) + 1}"
            segments.append({"start_ms": max(0, round(begin)), "end_ms": round(end),
                             "speaker": speaker, "text": text})
    segments.sort(key=lambda row: row["start_ms"])
    if not segments:
        raise AppError("没有识别到可转写的语音。请确认文件包含清晰的人声。")
    return segments, speakers


def transcript_text(document):
    return "\n".join(f"[{timestamp(s['start_ms'])}] {document['speakers'].get(s['speaker'], s['speaker'])}：{s['text']}"
                     for s in document["segments"])


def export_text(document, kind):
    if kind == "srt":
        entries = []
        for i, s in enumerate(document["segments"], 1):
            text = " ".join(s["text"].splitlines()).strip()
            speaker = document["speakers"].get(s["speaker"], s["speaker"])
            entries.append(f"{i}\n{timestamp(s['start_ms'], True)} --> {timestamp(s['end_ms'], True)}\n{speaker}：{text}\n")
        return "\n".join(entries)
    if kind == "txt":
        return document["title"] + "\n\n" + transcript_text(document) + "\n"
    if kind == "md":
        warning = "\n> 原文已修改，摘要尚未更新。\n" if document.get("summary_stale") else ""
        return f"# {document['title']}\n{warning}\n## 摘要\n\n{document['summary'] or '尚未生成摘要。'}\n\n## 文字稿\n\n{transcript_text(document)}\n"
    if kind == "json":
        return json.dumps(document, ensure_ascii=False, indent=2)
    raise ValueError("Unknown export format")


def summary_chunks(document, limit=12000):
    # Split on transcript lines where possible; never drop the end of long recordings.
    chunks, current = [], ""
    for line in transcript_text(document).splitlines():
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current.strip())
    return chunks


MAX_MEDIA_SECONDS = 4 * 60 * 60


def probe_media_duration(source, cancel=None):
    """Inspect headers before any long decode; return the input duration in milliseconds."""
    import imageio_ffmpeg
    source = Path(source).resolve()
    if not source.is_file():
        raise AppError("找不到视频文件，请重新选择。")
    if cancel is not None:
        check_cancel(cancel)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner",
                                 "-protocol_whitelist", "file,pipe", "-i", str(source)],
                                capture_output=True, timeout=20, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise AppError("读取视频信息超时，请确认文件保存在本机后重试。") from exc
    if cancel is not None:
        check_cancel(cancel)
    details = result.stderr.decode("utf-8", "replace")
    if "Audio:" not in details:
        raise AppError("文件中没有可读取的音轨。请确认视频有声音，或重新导出为 MP4 后重试。")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", details)
    if not match:
        raise AppError("无法读取视频时长。请尝试重新导出为 MP4，或选择音频文件。")
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if duration > MAX_MEDIA_SECONDS + .2:
        raise AppError(f"视频时长为 {timestamp(round(duration * 1000))}，超过当前 4 小时上限。请分成较短片段后重试。")
    if duration < .1:
        raise AppError("音频过短，无法识别。")
    return round(duration * 1000)


def extract_audio(source, target, cancel, report=None):
    import imageio_ffmpeg
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    source = Path(source).resolve()
    input_duration = probe_media_duration(source, cancel)
    if report:
        report(f"视频时长 {timestamp(input_duration)}，正在本机提取音频…")
    # Cap extraction, then reject overlong recordings; never silently transcribe a prefix.
    encoding = ["-c:a", "pcm_s16le"] if Path(target).suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-b:a", "48k"]
    args = [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0", "-vn", "-t", str(MAX_MEDIA_SECONDS + 2),
            "-ac", "1", "-ar", "16000", *encoding, str(target)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags)
        try:
            while proc.poll() is None:
                if cancel.wait(.2):
                    raise Cancelled("已停止提取音频。")
            if proc.returncode:
                raise AppError("无法提取音频。请确认视频可播放且包含音轨，或尝试转换为 MP4。")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    check_cancel(cancel)
    try:
        probe = subprocess.run([executable, "-hide_banner", "-i", str(target)],
                               capture_output=True, timeout=20, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise AppError("读取音频时长超时，请重试。") from exc
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr.decode("utf-8", "replace"))
    if not match:
        raise AppError("无法确定提取后的音频时长，请检查文件后重试。")
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if duration > MAX_MEDIA_SECONDS + .2:
        raise AppError("实际音轨超过 4 小时上限，请分成较短片段后重试。")
    if duration < .1:
        raise AppError("音频过短，无法识别。")
    return round(duration * 1000)
