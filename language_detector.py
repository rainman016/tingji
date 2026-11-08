"""Bounded, offline source-language detection with explicit uncertainty."""
from __future__ import annotations

import math
from pathlib import Path
import wave

ROOT = Path(__file__).resolve().parent
MODEL_FOLDER = "whisper-tiny"
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
SAMPLE_RATE = 16000
WINDOW_SECONDS = 30
MIN_SPEECH_MS = 3000


def language_model_ready(models_dir=None):
    folder = Path(models_dir) / MODEL_FOLDER if models_dir is not None else ROOT / "models" / MODEL_FOLDER
    try:
        return all((folder / name).is_file() and (folder / name).stat().st_size > 0
                   for name in REQUIRED_FILES) and (folder / "model.bin").stat().st_size >= 50_000_000
    except OSError:
        return False


def sample_offsets(frame_count):
    """At most three bounded windows; short audio is never padded into speech."""
    last = max(0, int(frame_count) - WINDOW_SECONDS * SAMPLE_RATE)
    return sorted({0, last // 2, last})


def choose_language(samples):
    """Use model probabilities as routing evidence, never as accuracy guarantees."""
    evidence = [sample for sample in samples
                if sample.get("speech_ms", 0) >= MIN_SPEECH_MS
                and isinstance(sample.get("confidence"), (int, float))
                and math.isfinite(sample["confidence"])
                and 0 <= sample["confidence"] <= 1
                and sample.get("language")]
    confidence = max((sample["confidence"] for sample in evidence), default=0.0)
    result = {"language": None, "confidence": round(confidence, 4),
              "needs_confirmation": True, "reason": "", "samples": samples}
    if not evidence:
        result["reason"] = "取样中没有足够清晰的人声，请手动选择中文或英文后继续。"
        return result
    strongest = max(evidence, key=lambda sample: sample["confidence"])
    language = strongest["language"]
    if language not in ("zh", "en") and confidence >= .65:
        result["reason"] = "取样可能包含中文、英文之外的语言；当前转写仅支持中文和英文，请核实原音语言。"
        return result
    if any(sample["language"] != language and sample["confidence"] >= .65 for sample in evidence):
        result["reason"] = "不同位置的取样语言不一致，可能包含多种语言，请核实并手动选择原音语言。"
        return result
    strong = [sample for sample in evidence if sample["language"] == language
              and sample["confidence"] >= .8 and sample.get("margin", 0) >= .2]
    if language not in ("zh", "en") or not strong or (len(evidence) == 1 and confidence < .9):
        result["reason"] = "暂时无法可靠判断原音语言，请手动选择中文或英文后继续。"
        return result
    result.update(language=language, needs_confirmation=False,
                  reason="取样人声判断为" + ("英文" if language == "en" else "中文") + "，将使用对应识别模型。")
    result["confidence"] = round(sum(sample["confidence"] for sample in strong) / len(strong), 4)
    return result


def detect_language(request, progress, *, model_factory=None, vad=None):
    """Detect from standard PCM without whole-recording reads or automatic downloads.

    A caller owns cancellation by terminating this isolated worker. Optional
    factories are for silent tests; production calls supply only request/progress.
    """
    import numpy as np
    if vad is None:
        from faster_whisper.vad import get_speech_timestamps
        vad = get_speech_timestamps
    if model_factory is None:
        if not language_model_ready():
            raise ValueError("自动识别语言模型尚未安装完整，请运行“安装语言检测模型.cmd”，或手动选择中文/英文。")
        from faster_whisper import WhisperModel
        model_factory = WhisperModel
    progress("正在从不同位置提取人声，判断原音语言…")
    samples, clips = [], []
    try:
        with wave.open(str(request["audio"]), "rb") as audio:
            if (audio.getframerate() != SAMPLE_RATE or audio.getnchannels() != 1
                    or audio.getsampwidth() != 2 or audio.getcomptype() != "NONE"):
                raise ValueError("语言检测需要 16kHz 单声道 PCM 音频，请重新提取音轨。")
            for offset in sample_offsets(audio.getnframes()):
                audio.setpos(offset)
                raw = audio.readframes(WINDOW_SECONDS * SAMPLE_RATE)
                pcm = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
                sample = {"offset_ms": round(offset * 1000 / SAMPLE_RATE), "speech_ms": 0,
                          "language": None, "confidence": 0.0, "margin": 0.0}
                if len(pcm):
                    regions = vad(pcm, sampling_rate=SAMPLE_RATE,
                                  min_speech_duration_ms=250, min_silence_duration_ms=300,
                                  speech_pad_ms=0)
                    pieces = []
                    cursor = 0
                    for region in regions:
                        # Never double-count overlapping VAD windows or read past this sample.
                        start = max(cursor, min(len(pcm), int(region["start"])))
                        end = max(start, min(len(pcm), int(region["end"])))
                        if end > start:
                            pieces.append(pcm[start:end])
                            cursor = end
                    if pieces:
                        speech = np.concatenate(pieces)[:WINDOW_SECONDS * SAMPLE_RATE]
                        sample["speech_ms"] = round(len(speech) * 1000 / SAMPLE_RATE)
                        if sample["speech_ms"] >= MIN_SPEECH_MS:
                            clips.append((sample, speech))
                samples.append(sample)
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError("无法读取待检测音频，请重新提取音轨后重试。") from exc
    if not clips:
        return choose_language(samples)
    progress("正在本机识别原音语言（CPU）…")
    model = model_factory(str(ROOT / "models" / MODEL_FOLDER), device="cpu", compute_type="int8",
                          cpu_threads=2, num_workers=1, local_files_only=True)
    try:
        if not {"zh", "en"}.issubset(set(model.supported_languages)):
            raise ValueError("语言检测模型不是多语版本，请重新运行“安装语言检测模型.cmd”。")
        for number, (sample, pcm) in enumerate(clips, 1):
            language, probability, candidates = model.detect_language(
                audio=pcm, vad_filter=False, language_detection_segments=1,
                language_detection_threshold=.8)
            probability = float(probability)
            alternatives = [float(score) for name, score in candidates if name != language]
            if (math.isfinite(probability) and 0 <= probability <= 1
                    and all(math.isfinite(score) and 0 <= score <= 1 for score in alternatives)):
                alternative = max(alternatives, default=0.0)
                sample.update(language=language, confidence=probability,
                              margin=max(0.0, probability - alternative))
            progress(f"正在判断原音语言：{number} / {len(clips)} 处人声取样")
    finally:
        del model
    return choose_language(samples)
