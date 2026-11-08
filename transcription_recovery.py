"""Durable audio preparation and validation for resumable local transcription."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import os
import re
import wave

from checkpoint_store import CheckpointStore, fingerprint
from core import AppError, check_cancel


class LanguageConfirmationRequired(AppError):
    pass


def checkpoint_for(directory, document_id):
    if not re.fullmatch(r"[a-f0-9]{32}", document_id):
        raise AppError("文稿编号无效，不能读取恢复进度。")
    return CheckpointStore(Path(directory) / ".checkpoints" / document_id)


def recovery_info(document, directory):
    info = {"available": False, "completed_ms": 0, "total_ms": 0,
            "completed_chunks": 0, "total_chunks": 0, "reason": ""}
    if not document or document.get("transcription_complete") or document.get("segments"):
        return info
    try:
        store = checkpoint_for(directory, document["id"])
        manifest = store.load_manifest()
        if not manifest:
            if document.get("status") in ("已停止", "已中断", "待重试"):
                info["reason"] = "这份旧任务没有恢复进度，请重新开始转写。"
            return info
        if manifest.get("version") != 1:
            raise AppError("恢复进度版本不兼容，请重新转写为新文稿。")
        if manifest.get("audio") and not store.audio_path.is_file():
            raise AppError("恢复音频缓存缺失，请重新转写；已有文稿仍保留。")
        info.update(available=True, total_ms=manifest.get("duration_ms", 0),
                    language=manifest.get("language"), settings=manifest.get("settings", {}))
        plan = store.load_unit("plan")
        if plan:
            boundaries, rate = plan["boundaries"], plan["sample_rate"]
            if (type(rate) is not int or rate != 16000 or not isinstance(boundaries, list)
                    or len(boundaries) < 2 or any(type(value) is not int for value in boundaries)
                    or boundaries[0] != 0 or any(left >= right for left, right in zip(boundaries, boundaries[1:]))):
                raise AppError("恢复分段计划损坏，请重新转写为新文稿。")
            info["total_chunks"] = len(boundaries) - 1
            for index in range(len(boundaries) - 1):
                unit = store.load_unit(f"asr-{index:05d}")
                if unit is None:
                    break
                if unit.get("start_frame") != boundaries[index] or unit.get("end_frame") != boundaries[index + 1]:
                    raise AppError("识别片段与恢复计划不一致，请重新转写为新文稿。")
                info["completed_chunks"] += 1
                info["completed_ms"] = round(boundaries[index + 1] * 1000 / rate)
        return info
    except (OSError, ValueError, KeyError, TypeError, AppError) as exc:
        info.update(available=False, reason=str(exc) if isinstance(exc, AppError) else "恢复进度损坏，请重新转写为新文稿。")
        return info


def model_identity(models, language):
    names = ("whisper-small-en", "campplus") if language == "en" else ("paraformer-zh", "fsmn-vad", "ct-punc", "campplus")
    files = []
    for name in names:
        folder = Path(models) / name
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            if path.is_file() and path.suffix in (".bin", ".pt", ".json", ".yaml", ".txt"):
                stat = path.stat()
                files.append([name + "/" + path.name, stat.st_size, stat.st_mtime_ns])
    return {"algorithm": "chunk-asr-global-speakers-v1", "language": language, "files": files}


@contextmanager
def prepared_transcription(document, directory, cancel, report, service, extractor,
                           persist_stage, *, language, speaker_count, resume, models, store=None):
    supplied_store = store
    store = store or checkpoint_for(directory, document["id"])
    with nullcontext(store) if supplied_store is not None else store.lock():
        manifest = store.load_manifest()
        if manifest and not resume:
            raise AppError("该文稿已有恢复进度。请点击「继续转写」，或重新转写为新文稿。")
        if resume and not manifest:
            raise AppError("没有找到可恢复进度，请重新开始转写。")
        if manifest and manifest.get("version") != 1:
            raise AppError("恢复进度版本不兼容，请重新转写为新文稿。")
        report("正在校验源文件，确认恢复进度属于同一音视频…" if resume else "正在校验源文件并准备恢复记录…")
        persist_stage("校验恢复进度")
        source = Path(document["source"])
        if not source.is_file():
            raise AppError("找不到原音视频，请重新关联同一文件后继续。")
        source_id = fingerprint(source, cancel)
        settings = {"speaker_count": speaker_count}
        if manifest:
            if manifest.get("source") != source_id:
                raise AppError("原音视频内容已改变，不能复用旧进度。请重新转写为新文稿。")
            if manifest.get("settings") != settings:
                raise AppError("说话人数与已保存进度不同。请恢复原设置后继续，或重新转写为新文稿。")
            if manifest.get("language") and language not in ("auto", manifest["language"]):
                raise AppError("所选语言与已保存进度不同，请恢复原语言后继续，或重新转写为新文稿。")
        else:
            manifest = {"version": 1, "source": source_id, "settings": settings,
                        "audio": None, "language": None}
            store.save_manifest(manifest)
        document["transcription_settings"] = settings
        document["transcription_complete"] = False
        document["language_mode"] = language
        if manifest.get("audio"):
            report("正在校验已保存的音频缓存…")
            if not store.audio_path.is_file() or fingerprint(store.audio_path, cancel) != manifest["audio"]:
                raise AppError("恢复音频缓存缺失或内容损坏，请重新转写为新文稿。")
        else:
            persist_stage("提取音频")
            temporary = store.directory / "audio.partial.wav"
            try:
                extractor(source, temporary, cancel)
                check_cancel(cancel)
                with wave.open(str(temporary), "rb") as audio:
                    if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2) or not audio.getnframes():
                        raise AppError("恢复音频格式无效，需要 16kHz 单声道 PCM 音频。")
                    duration_ms = round(audio.getnframes() * 1000 / audio.getframerate())
                if fingerprint(source, cancel) != source_id:
                    raise AppError("准备音频期间源文件发生变化，请重新开始。")
                audio_id = fingerprint(temporary, cancel)
                os.replace(temporary, store.audio_path)
                manifest.update(audio=audio_id, duration_ms=duration_ms)
                store.save_manifest(manifest)
            finally:
                temporary.unlink(missing_ok=True)
        document["duration_ms"] = manifest["duration_ms"]
        resolved = manifest.get("language")
        if not resolved and language == "auto":
            persist_stage("检测语言")
            report("正在从多个位置抽取人声，判断原音语言…")
            detection = service.detect_language(store.audio_path)
            document["language_detection"] = detection
            manifest["language_detection"] = detection
            store.save_manifest(manifest)
            if detection.get("needs_confirmation") or detection.get("language") not in ("zh", "en"):
                document.pop("language", None)
                raise LanguageConfirmationRequired(detection.get("reason") or "无法可靠判断原音语言，请手动选择中文或英文后继续。")
            resolved = detection["language"]
        elif not resolved:
            resolved = language
        elif manifest.get("language_detection"):
            document["language_detection"] = manifest["language_detection"]
        if resolved not in ("en", "zh"):
            raise AppError("请选择自动检测、中文或英文。")
        identity = model_identity(models, resolved)
        if manifest.get("model") and manifest["model"] != identity and store.load_unit("asr-00000") is not None:
            raise AppError("识别模型版本已变化，不能混用旧片段，请重新转写为新文稿。")
        manifest.update(language=resolved, model=identity)
        store.save_manifest(manifest)
        document["language"] = resolved
        document["engine"] = "local-whisper-small-en" if resolved == "en" else "local-funasr"
        check_cancel(cancel)
        persist_stage("本地识别中")
        yield store, resolved


def release_completed_audio(store):
    # Only the generated WAV is removed, after the final transcript is durable.
    # Keep checkpoint text and manifest for diagnosis; never remove documents.
    try:
        store.audio_path.unlink(missing_ok=True)
    except OSError:
        pass
