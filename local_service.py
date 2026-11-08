"""Local-only inference supervisor. No provider APIs and no implicit downloads."""
from __future__ import annotations

import importlib.util
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

from core import AppError, Cancelled, SummaryGPUUnavailable
from checkpoint_store import CheckpointInUse
from process_control import owner_is_alive, process_owner, start_owned_process
from summary_backend import gpu_runtime_status
from transcription_recovery import (LanguageConfirmationRequired, prepared_transcription,
                                    checkpoint_for, recovery_info, release_completed_audio)

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
ASR_FOLDERS = ("paraformer-zh", "fsmn-vad", "ct-punc", "campplus")
SUMMARY_FILE = MODELS / "qwen3-4b-q4_k_m.gguf"
ACTIVE_STATUSES = {"校验恢复进度", "提取音频", "检测语言", "本地识别中", "生成摘要"}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reconcile_interrupted_document(document):
    """Reconcile a historical snapshot for display without changing its saved file."""
    result = copy.deepcopy(document)
    if result.get("status") not in ACTIVE_STATUSES:
        return result
    alive = owner_is_alive(result.get("task_owner"))
    if alive is True:
        return result
    stage = result["status"]
    result["interrupted_stage"] = stage
    retry = "已保存的文字稿可继续使用，可重新生成摘要。" if result.get("segments") else "有恢复进度时可继续转写；旧任务没有恢复进度时请重新开始转写。"
    if alive is False:
        result["status"] = "已中断"
        result["error"] = f"上次任务在“{stage}”阶段中断，原计算进程已不存在。{retry}"
    else:
        result["status"] = "状态待核实"
        result["error"] = (f"无法确认“{stage}”任务是否仍在其他窗口运行，当前仅供查看和导出。"
                           "请核实旧窗口；如需重转，可重新选择源文件创建新任务。")
    return result


def _preserve_diagnostic(log_file, scratch, stage, error):
    """Read only a bounded log tail; diagnostic trouble must not replace the error."""
    try:
        with log_file.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 6000))
            tail = stream.read(6000).decode("utf-8", "replace")
        message = f"{_utc_now()}\n阶段：{stage[:300]}\n原因：{str(error)[:600]}\n{tail}"
        (scratch / "last-local-error.log").write_text(message, encoding="utf-8")
    except OSError:
        pass


def local_status():
    missing = []
    for name in ASR_FOLDERS:
        folder = MODELS / name
        weights = list(folder.glob("*.pt")) + list(folder.glob("*.bin"))
        if not (folder / "config.yaml").is_file() or not any(p.stat().st_size > 100000 for p in weights):
            missing.append(name)
    dependencies = all(importlib.util.find_spec(name) for name in ("torch", "torchaudio", "funasr"))
    summary_ready = SUMMARY_FILE.is_file() and SUMMARY_FILE.stat().st_size > 100_000_000
    summary_ready = summary_ready and bool(importlib.util.find_spec("llama_cpp"))
    english_folder = MODELS / "whisper-small-en"
    english_ready = all((english_folder / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))
    english_ready = english_ready and (english_folder / "model.bin").stat().st_size > 100_000_000
    english_ready = english_ready and bool(importlib.util.find_spec("faster_whisper")) and "campplus" not in missing and dependencies
    from language_detector import language_model_ready
    detector_ready = language_model_ready(MODELS) and bool(importlib.util.find_spec("faster_whisper"))
    return {"asr": not missing and dependencies, "english": english_ready, "summary": summary_ready,
            "summary_gpu": gpu_runtime_status()['ready'], "language_detection": detector_ready, "missing": missing}


def offline_environment():
    env = os.environ.copy()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HOME": str(ROOT / ".cache" / "huggingface"), "MODELSCOPE_CACHE": str(MODELS),
                "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "OMP_NUM_THREADS": "6"})
    return env


class LocalService:
    def __init__(self, cancel, report, device="auto"):
        self.cancel, self.report, self.device = cancel, report, device

    def run_worker(self, request, timeout=7200):
        if self.cancel.is_set():
            raise Cancelled("已停止本地计算，已完成的文字稿仍保留。")
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="local-job-", dir=scratch) as temp:
            directory = Path(temp)
            request_file, result_file = directory / "request.json", directory / "result.json"
            progress_file, log_file = directory / "progress.json", directory / "worker.log"
            request_file.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            executable = Path(sys.executable)
            if executable.name.lower() == "pythonw.exe":
                executable = executable.with_name("python.exe")
            command = [str(executable), str(ROOT / "local_worker.py"), str(request_file), str(result_file), str(progress_file)]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            started, last_heartbeat = time.monotonic(), 0
            last_progress = "正在等待本地引擎启动"
            try:
                with log_file.open("wb") as logs:
                    with start_owned_process(command, cwd=ROOT, stdout=logs, stderr=logs,
                                             creationflags=flags, env=offline_environment()) as proc:
                        while proc.poll() is None:
                            if self.cancel.wait(.2):
                                raise Cancelled("已停止本地计算，已完成的文字稿仍保留。")
                            elapsed = time.monotonic() - started
                            if elapsed > timeout:
                                raise AppError("本地计算超时。请尝试较短视频，或关闭其他占用资源的程序。")
                            try:
                                progress = json.loads(progress_file.read_text(encoding="utf-8"))["message"]
                                if isinstance(progress, str) and progress != last_progress:
                                    self.report(progress)
                                    last_progress = progress
                            except (OSError, ValueError, KeyError):
                                pass
                            if elapsed - last_heartbeat >= 10:
                                minutes, seconds = divmod(int(elapsed), 60)
                                self.report(f"{last_progress} · 本阶段已运行 {minutes}:{seconds:02d}"
                                            "（耗时提示，不代表完成率）")
                                last_heartbeat = elapsed
                        if self.cancel.is_set():
                            raise Cancelled("已停止本地计算，已完成的文字稿仍保留。")
                        try:
                            result = json.loads(result_file.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            if request.get('mode') == 'summary' and request.get('summary_backend') == 'gpu':
                                raise SummaryGPUUnavailable('显卡摘要进程异常退出，未留下完整结果。') from None
                            raise AppError("本地计算进程退出但未留下完整结果。可重试；诊断已保存在 .tmp/last-local-error.log。") from None
                        if not isinstance(result, dict) or not result.get("ok") or proc.returncode:
                            error = result.get("error") if isinstance(result, dict) else None
                            if isinstance(result, dict) and result.get('error_code') == 'summary_gpu_unavailable':
                                raise SummaryGPUUnavailable(error or '显卡摘要无法运行。')
                            raise AppError(error or "本地计算失败，请检查模型文件。")
                        if "data" not in result:
                            raise AppError("本地计算结果不完整，请重试。")
                        return result["data"]
            except Exception as exc:
                _preserve_diagnostic(log_file, scratch, last_progress, exc)
                raise

    def detect_language(self, audio):
        if not local_status()["language_detection"]:
            raise AppError("自动检测模型尚未就绪，请运行「安装语言检测模型.cmd」，或手动选择原音语言。")
        return self.run_worker({"mode": "detect_language", "audio": str(Path(audio).resolve())}, timeout=180)

    def transcribe(self, audio, speaker_count=0, language="zh", checkpoint_dir=None):
        state = local_status()
        if language == "en" and not state["english"]:
            raise AppError("英文模型尚未就绪，请运行“安装英文模型.cmd”，完成后重新打开程序。")
        if language != "en" and not state["asr"]:
            raise AppError("本地识别模型尚未安装完整，请先运行“安装本地模型.cmd”。")
        timeout = 7200
        try:
            with wave.open(str(audio), "rb") as media:
                timeout = max(timeout, min(28800, media.getnframes() / media.getframerate() * 2 + 600))
        except (OSError, EOFError, wave.Error):
            pass
        request = {"mode": "english" if language == "en" else "asr", "audio": str(Path(audio).resolve()),
                   "device": self.device, "speaker_count": speaker_count}
        if checkpoint_dir:
            request["checkpoint_dir"] = str(Path(checkpoint_dir).resolve())
        return self.run_worker(request, timeout=timeout)

    def summarize(self, document, style):
        if not local_status()["summary"]:
            raise AppError("本地摘要模型尚未就绪。文字稿已保留，可先导出，安装摘要模型后再生成摘要。")
        accelerated = self.device != 'cpu' and gpu_runtime_status()['ready']
        request = {"mode": "summary", "document": document, "style": style, "device": self.device,
                   "summary_backend": 'gpu' if accelerated else 'cpu'}
        if getattr(self, 'summary_checkpoint_dir', None):
            request['summary_checkpoint_dir'] = str(self.summary_checkpoint_dir)
        try:
            result = self.run_worker(request)
        except SummaryGPUUnavailable as exc:
            if self.device == 'cpu' or self.cancel.is_set():
                raise
            self.report('显卡摘要暂不可用，正在切换CPU（速度较慢）：' + str(exc)[:180])
            result = self.run_worker(dict(request, device='cpu', summary_backend='cpu'))
            result.setdefault('inference', {})['fallback_reason'] = str(exc)
        self.last_summary_inference = result.get('inference', {})
        return result['summary']


def run_local_pipeline(document, directory, cancel, report, *, summary_only=False,
                       auto_summary=True, style="通用摘要", speaker_count=0, device="auto", language="zh",
                       service_factory=LocalService, extractor=None, on_document=None, resume=False,
                       expected_document=None):
    store = checkpoint_for(directory, document["id"])
    # Own every document write, including cancellation, failures and summary.
    with store.lock():
        target = Path(directory) / (document["id"] + ".json")
        if target.exists():
            try:
                latest = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AppError("无法核对已保存文稿，请重新打开最新版本后重试。") from exc
            if expected_document is not None and latest != expected_document:
                raise AppError("这份文稿已被其他窗口更新，请重新打开最新版本后再开始。")
            if resume and (latest.get("transcription_complete") or latest.get("segments")):
                raise AppError("这份文稿已经完成转写，请重新打开最新结果；不要继续旧进度。")
        return _run_local_pipeline(document, directory, cancel, report, summary_only=summary_only,
                                   auto_summary=auto_summary, style=style, speaker_count=speaker_count,
                                   device=device, language=language, service_factory=service_factory,
                                   extractor=extractor, on_document=on_document, resume=resume, store=store)


def _run_local_pipeline(document, directory, cancel, report, *, summary_only,
                        auto_summary, style, speaker_count, device, language,
                        service_factory, extractor, on_document, resume, store):
    from core import extract_audio, save_document
    extractor = extractor or (lambda source, target, event: extract_audio(source, target, event, report=report))
    service = service_factory(cancel, report, device)
    document["error"] = ""
    document["task_owner"] = process_owner()
    document["task_started_at"] = _utc_now()
    document.pop("interrupted_stage", None)

    def persist_stage(status):
        document["status"] = status
        document["updated_at"] = _utc_now()
        save_document(document, directory)
        if on_document is not None:
            on_document(copy.deepcopy(document))

    if not summary_only:
        document.pop("region", None)
        document.pop("workspace", None)
    try:
        if cancel.is_set():
            raise Cancelled("已停止本地计算，已完成的文字稿仍保留。")
        if not summary_only:
            with prepared_transcription(document, directory, cancel, report, service, extractor,
                                        persist_stage, language=language, speaker_count=speaker_count,
                                        resume=resume, models=MODELS, store=store) as (store, resolved):
                result = service.transcribe(store.audio_path, speaker_count, language=resolved,
                                            checkpoint_dir=store.directory)
                document["segments"] = result["segments"]
                document["speakers"] = result["speakers"]
                document["local_inference"] = {k: result[k] for k in ("device", "seconds") if k in result}
                document["task_id"] = ""
                document["transcription_complete"] = True
                persist_stage("已转写")
                release_completed_audio(store)
        if cancel.is_set():
            raise Cancelled("已停止本地计算，已完成的文字稿仍保留。")
        if auto_summary or summary_only:
            from summary_state import source_digest, snapshot
            import uuid
            source = source_digest(document)
            job = document.get('summary_job', {})
            if job.get('source') != source or job.get('style') != style or job.get('complete'):
                job = {'run_id': uuid.uuid4().hex, 'source': source, 'style': style, 'complete': False}
            document['summary_job'] = job
            service.summary_checkpoint_dir = store.directory / 'summary'
            report("准备本地摘要…")
            persist_stage("生成摘要")
            from summary_validation import validate_summary
            raw_summary = service.summarize(document, style)
            checked = validate_summary(raw_summary, document)
            if document.get('summary'):
                document['previous_summary'] = snapshot(document)
            document["summary"] = checked.pop("text")
            document["summary_validation"] = checked
            document["summary_original"] = raw_summary
            document["summary_engine"] = "local-qwen3-4b"
            inference = getattr(service, 'last_summary_inference', None)
            if isinstance(inference, dict):
                document['summary_inference'] = inference
            document["summary_stale"] = False
            document['summary_source'] = source
            document['summary_style'] = style
            document['summary_review'] = {'status': 'generated'}
            document['summary_job']['complete'] = True
        persist_stage("已完成" if document["summary"] else "已转写")
    except Exception as exc:
        if isinstance(exc, CheckpointInUse):
            # Another window owns this job; do not overwrite its durable state.
            raise
        status = ("等待确认语言" if isinstance(exc, LanguageConfirmationRequired) else
                  "已停止" if isinstance(exc, Cancelled) else ("摘要待重试" if document["segments"] else "待重试"))
        document["error"] = str(exc) if isinstance(exc, AppError) else "本地处理失败，请检查安装状态或尝试较短视频。"
        persist_stage(status)
        raise
    return document
