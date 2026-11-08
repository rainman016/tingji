from pathlib import Path
import tempfile
import uuid

from core import AppError, Cancelled, check_cancel, extract_audio, parse_transcript, save_document
from service import CloudService


def run_pipeline(document, key, directory, cancel, report, *, summary_only=False,
                 auto_summary=True, style="通用摘要", speaker_count=0,
                 service_factory=CloudService, extractor=extract_audio):
    """One sequential job. Persist the cloud id before polling to support recovery."""
    cloud = service_factory(key, document["region"], document.get("workspace", ""), cancel, report)
    document["error"] = ""
    try:
        if not summary_only and not document["segments"]:
            if not document.get("task_id"):
                report("正在本机提取音频…")
                document["status"] = "提取音频"
                save_document(document, directory)
                with tempfile.TemporaryDirectory(prefix="video-notes-") as folder:
                    audio = Path(folder) / (uuid.uuid4().hex + ".mp3")
                    document["duration_ms"] = extractor(document["source"], audio, cancel)
                    check_cancel(cancel)
                    report("准备上传音频…")
                    url = cloud.upload(audio)
                    check_cancel(cancel)
                    # Do not retry this POST automatically: timeout may mean the job exists.
                    document["status"] = "提交中"
                    save_document(document, directory)
                    document["task_id"] = cloud.submit(url, speaker_count)
                    document["status"] = "转写中"
                    save_document(document, directory)
            report("获取云端转写结果…")
            payload = cloud.wait_result(document["task_id"])
            document["segments"], document["speakers"] = parse_transcript(payload)
            document["status"] = "已转写"
            save_document(document, directory)
        if auto_summary or summary_only:
            document["status"] = "生成摘要"
            save_document(document, directory)
            document["summary"] = cloud.summarize(document, style)
            document["summary_stale"] = False
        document["status"] = "已完成" if document["summary"] else "已转写"
        save_document(document, directory)
    except Exception as exc:
        if isinstance(exc, Cancelled):
            document["status"] = "已停止"
        elif document["segments"]:
            document["status"] = "摘要待重试"
        elif document["status"] == "提交中" and not document.get("task_id"):
            document["status"] = "提交结果待核查"
        else:
            document["status"] = "待重试"
        document["error"] = str(exc) if isinstance(exc, AppError) else "处理未完成，请重试；若问题持续，请检查文件与账号设置。"
        save_document(document, directory)
        raise
    finally:
        cloud.close()
    return document
