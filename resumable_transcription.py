"""Bounded ASR and speaker feature checkpoints; no model imports at module load."""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import json
import math
import numbers

from core import timestamp

SAMPLE_RATE = 16000
CHUNK_SECONDS = 120
PLAN_VERSION = 1


class MemoryStore:
    """Use the same worker path for callers that do not request persistence."""
    def __init__(self):
        self.units = {}

    def load_unit(self, key):
        return copy.deepcopy(self.units.get(key))

    def save_unit(self, key, data):
        self.units[key] = copy.deepcopy(data)


def worker_store(request):
    if request.get("checkpoint_dir"):
        from checkpoint_store import CheckpointStore
        return CheckpointStore(request["checkpoint_dir"])
    return MemoryStore()


def _quiet_boundary(audio, target, lower, upper):
    """Prefer a nearby >=200ms quiet interval, without overlapping audio blocks."""
    import numpy as np
    window = SAMPLE_RATE // 50  # 20ms, exact integer frame boundaries.
    left = max(lower, target - 3 * SAMPLE_RATE)
    right = min(upper, target + 3 * SAMPLE_RATE)
    audio.seek(left)
    samples = audio.read(right - left, dtype="float32")
    usable = len(samples) // window
    if usable < 10:
        return target
    frames = samples[:usable * window].reshape(usable, window)
    quiet = np.sqrt(np.mean(frames * frames, axis=1)) < .008
    candidates, begin = [], None
    for index, is_quiet in enumerate([*quiet, False]):
        if is_quiet and begin is None:
            begin = index
        elif not is_quiet and begin is not None:
            if index - begin >= 10:
                interval_start, interval_end = left + begin * window, left + index * window
                # Stay inside the quiet interval rather than at a speech edge.
                candidates.append(max(interval_start + window, min(target, interval_end - window)))
            begin = None
    return min(candidates, key=lambda value: abs(value - target)) if candidates else target


def load_plan(audio, store):
    total = len(audio)
    if audio.samplerate != SAMPLE_RATE or audio.channels != 1 or total <= 0:
        raise ValueError("恢复转写需要有效的 16kHz 单声道音频。")
    plan = store.load_unit("plan")
    if plan is not None:
        if not isinstance(plan, dict):
            raise ValueError("恢复点的音频分段计划损坏，不能安全继续；请新建转写任务。")
        boundaries = plan.get("boundaries")
        if (plan.get("algorithm_version") != PLAN_VERSION or
                plan.get("chunk_seconds") != CHUNK_SECONDS or
                plan.get("sample_rate") != SAMPLE_RATE or plan.get("total_frames") != total or
                not isinstance(boundaries, list) or len(boundaries) < 2 or
                any(type(value) is not int for value in boundaries) or
                boundaries[0] != 0 or boundaries[-1] != total or
                any(left >= right for left, right in zip(boundaries, boundaries[1:]))):
            raise ValueError("恢复点的音频分段计划不匹配，不能安全继续；请新建转写任务。")
        return plan
    boundaries, cursor = [0], 0
    while cursor + CHUNK_SECONDS * SAMPLE_RATE < total:
        target = cursor + CHUNK_SECONDS * SAMPLE_RATE
        boundary = _quiet_boundary(audio, target, cursor + SAMPLE_RATE, total - 1)
        boundaries.append(boundary)
        cursor = boundary
    boundaries.append(total)
    plan = {"algorithm_version": PLAN_VERSION, "chunk_seconds": CHUNK_SECONDS,
            "sample_rate": SAMPLE_RATE, "total_frames": total, "boundaries": boundaries}
    store.save_unit("plan", plan)
    return plan


def _absolute_segments(rows, start_frame, end_frame):
    start_ms = round(start_frame * 1000 / SAMPLE_RATE)
    end_ms = round(end_frame * 1000 / SAMPLE_RATE)
    result = []
    if not isinstance(rows, list):
        raise ValueError("识别模型未返回有效的分段列表。")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError("识别模型返回了无效文字段落。")
        if not row["text"].strip():
            continue
        start, end = row.get("start_ms"), row.get("end_ms")
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, numbers.Real) or not isinstance(end, numbers.Real) or
                not math.isfinite(start) or not math.isfinite(end) or end <= start):
            raise ValueError("识别模型返回了无效时间戳，当前片段未保存。")
        start, end = max(start_ms, start_ms + round(start)), min(end_ms, start_ms + round(end))
        if end <= start:
            raise ValueError("识别段落时间超出当前片段，当前片段未保存。")
        result.append({"start_ms": start, "end_ms": end, "text": row["text"].strip(), "speaker": "unknown"})
    return sorted(result, key=lambda row: (row["start_ms"], row["end_ms"]))


def _cached_segments(unit, start_frame, end_frame):
    if (not isinstance(unit, dict) or unit.get("start_frame") != start_frame or
            unit.get("end_frame") != end_frame or not isinstance(unit.get("segments"), list)):
        raise ValueError("已保存片段与分段计划不一致，不能安全恢复。")
    lower = round(start_frame * 1000 / SAMPLE_RATE)
    upper = round(end_frame * 1000 / SAMPLE_RATE)
    for row in unit["segments"]:
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str) or not row["text"].strip() or
                type(row.get("start_ms")) is not int or type(row.get("end_ms")) is not int or
                not lower <= row["start_ms"] < row["end_ms"] <= upper):
            raise ValueError("已保存片段包含无效内容或时间戳，不能安全恢复。")
    return [dict(row, speaker="unknown") for row in unit["segments"]]


def transcribe_chunks(audio_path, store, progress, recognizer_factory, label):
    """Commit only whole blocks; recognizer_factory is entered at most once."""
    import soundfile as sf
    with sf.SoundFile(audio_path) as audio, ExitStack() as stack:
        plan = load_plan(audio, store)
        blocks = list(zip(plan["boundaries"], plan["boundaries"][1:]))
        cached = [store.load_unit(f"asr-{index:05d}") for index in range(len(blocks))]
        reused = sum(unit is not None for unit in cached)
        if reused:
            progress(f"正在恢复{label}转写：已保存 {reused}/{len(blocks)} 个片段，将复用完整结果。")
        recognizer, segments = None, []
        for index, (start, end) in enumerate(blocks):
            if cached[index] is not None:
                rows = _cached_segments(cached[index], start, end)
            else:
                if recognizer is None:
                    recognizer = stack.enter_context(recognizer_factory())
                progress(f"{label}识别 {index + 1}/{len(blocks)}："
                         f"{timestamp(start * 1000 / SAMPLE_RATE)} — {timestamp(end * 1000 / SAMPLE_RATE)}")
                audio.seek(start)
                clip = audio.read(end - start, dtype="float32")
                if len(clip) != end - start:
                    raise ValueError("转写音频读取不完整，请检查恢复文件是否损坏。")
                rows = _absolute_segments(recognizer(clip, round(len(clip) * 1000 / SAMPLE_RATE)), start, end)
                store.save_unit(f"asr-{index:05d}", {"start_frame": start, "end_frame": end, "segments": rows})
            segments.extend(rows)
            action = "已复用" if cached[index] is not None else "已保存"
            progress(f"{action} {index + 1}/{len(blocks)} 个转写片段 · "
                     f"{timestamp(end * 1000 / SAMPLE_RATE)} / {timestamp(len(audio) * 1000 / SAMPLE_RATE)}")
    if not segments:
        raise ValueError("没有识别到语音，请确认音轨包含清晰人声并选择正确的原音语言。")
    return sorted(segments, key=lambda row: (row["start_ms"], row["end_ms"]))


def segment_signature(segment):
    body = {key: segment[key] for key in ("start_ms", "end_ms", "text")}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _valid_embedding(vector, expected_size=None):
    if (not isinstance(vector, list) or not vector or len(vector) > 4096 or
            (expected_size is not None and len(vector) != expected_size) or
            any(isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value)
                for value in vector) or not any(abs(value) > 1e-10 for value in vector)):
        raise ValueError("说话人特征无效或不完整，不能安全恢复分组。")
    return [float(value) for value in vector]


def speaker_embeddings(audio_path, segments, store, progress, encoder_factory):
    """Persist each independent feature; final labels are always computed globally."""
    import numpy as np
    import soundfile as sf
    vectors, encoder, reused = [], None, 0
    with sf.SoundFile(audio_path) as audio, ExitStack() as stack:
        if audio.samplerate != SAMPLE_RATE or audio.channels != 1 or not len(audio):
            raise ValueError("说话人分组需要有效的 16kHz 单声道音频。")
        for index, segment in enumerate(segments):
            signature = segment_signature(segment)
            unit = store.load_unit(f"speaker-{index:05d}")
            if unit is not None:
                if not isinstance(unit, dict) or unit.get("signature") != signature:
                    raise ValueError("已保存声纹与当前文字段落不一致，不能安全恢复。")
                vector = _valid_embedding(unit.get("embedding"), len(vectors[0]) if vectors else None)
                reused += 1
            else:
                if encoder is None:
                    encoder = stack.enter_context(encoder_factory())
                midpoint = (segment["start_ms"] + segment["end_ms"]) / 2000
                length = max(1.5, min(4, (segment["end_ms"] - segment["start_ms"]) / 1000))
                start = max(0, int((midpoint - length / 2) * SAMPLE_RATE))
                audio.seek(min(start, max(0, len(audio) - 1)))
                clip = audio.read(int(length * SAMPLE_RATE), dtype="float32")
                if len(clip) < SAMPLE_RATE:
                    clip = np.pad(clip, (0, SAMPLE_RATE - len(clip)))
                vector = _valid_embedding(list(encoder(clip)), len(vectors[0]) if vectors else None)
                store.save_unit(f"speaker-{index:05d}", {"signature": signature, "embedding": vector})
            vectors.append(vector)
            if index % 20 == 0 or index + 1 == len(segments):
                progress(f"说话人特征已保存：{index + 1}/{len(segments)} 段 · 复用 {reused} 段")
    return vectors
