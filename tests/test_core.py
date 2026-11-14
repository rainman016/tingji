import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from core import AppError, Cancelled, export_text, new_document, parse_transcript, save_document, summary_chunks, timestamp
from pipeline import run_pipeline
from service import CloudService, trusted_url

PAYLOAD = {"transcripts": [{"sentences": [
    {"begin_time": 1100, "end_time": 2400, "speaker_id": 0, "text": "你好，世界。"},
    {"begin_time": 2500, "end_time": 4200, "speaker_id": 1, "text": "明天下午讨论。"}]}]}


def example():
    doc = new_document("测试视频.mp4")
    doc["segments"], doc["speakers"] = parse_transcript(PAYLOAD)
    return doc


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def close(self):
        pass


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


class CoreTests(unittest.TestCase):
    def test_zero_speaker_and_order(self):
        rows, names = parse_transcript(PAYLOAD)
        self.assertEqual(rows[0]["speaker"], "0")
        self.assertEqual(names, {"0": "说话人 1", "1": "说话人 2"})

    def test_missing_speaker_does_not_invent_identity(self):
        data = copy.deepcopy(PAYLOAD)
        del data["transcripts"][0]["sentences"][0]["speaker_id"]
        rows, names = parse_transcript(data)
        self.assertEqual(names[rows[0]["speaker"]], "未区分说话人")

    def test_invalid_timestamp_rejected(self):
        data = copy.deepcopy(PAYLOAD)
        data["transcripts"][0]["sentences"][0]["end_time"] = 100
        with self.assertRaises(AppError):
            parse_transcript(data)

    def test_empty_speech_rejected(self):
        with self.assertRaises(AppError):
            parse_transcript({"transcripts": []})

    def test_export_uses_corrections_and_names(self):
        doc = example()
        doc["segments"][0]["text"] = "修改后的文字。"
        doc["speakers"]["0"] = "小林"
        for kind in ("srt", "txt", "md", "json"):
            self.assertIn("修改后的文字。", export_text(doc, kind))
            self.assertIn("小林", export_text(doc, kind))
        self.assertIn("00:00:01,100 --> 00:00:02,400", export_text(doc, "srt"))
        self.assertEqual(timestamp(3600123, True), "01:00:00,123")

    def test_chunks_keep_long_recording_tail(self):
        doc = example()
        doc["segments"][0]["text"] = "长" * 30000
        doc["segments"][1]["text"] = "最终关键结论"
        chunks = summary_chunks(doc, 12000)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(part) <= 12000 for part in chunks))
        self.assertIn("最终关键结论", chunks[-1])
        self.assertEqual("".join(chunks).count("长"), 30000)

    def test_atomic_save_and_stale_summary(self):
        doc = example()
        doc["summary_stale"] = True
        self.assertIn("原文已修改", export_text(doc, "md", include_summary=True))
        self.assertNotIn("原文已修改", export_text(doc, "md"))
        with tempfile.TemporaryDirectory() as folder:
            path = save_document(doc, folder)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["id"], doc["id"])
            doc["summary"] = "更新"
            save_document(doc, folder)
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


class ServiceTests(unittest.TestCase):
    def test_upload_streams_without_api_key_to_storage(self):
        policy = {"upload_host": "https://bucket.oss-cn-beijing.aliyuncs.com", "upload_dir": "private",
                  "oss_access_key_id": "temporary-id", "signature": "temporary-signature", "policy": "policy",
                  "x_oss_object_acl": "private", "x_oss_forbid_overwrite": True, "max_file_size_mb": 100}
        session = Session([Response({"data": policy}), Response({})])
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "sample.mp3"
            audio.write_bytes(b"sample-audio")
            self.assertEqual(CloudService("test-key", session=session).upload(audio), "oss://private/sample.mp3")
        self.assertNotIn("Authorization", session.calls[1][2]["headers"])
        self.assertIn("multipart/form-data", session.calls[1][2]["headers"]["Content-Type"])

    def test_submit_contract(self):
        session = Session([Response({"output": {"task_id": "task-123"}})])
        service = CloudService("test-key", session=session)
        self.assertEqual(service.submit("oss://private/audio.mp3", 3), "task-123")
        _, _, request = session.calls[0]
        self.assertEqual(request["headers"]["X-DashScope-OssResourceResolve"], "enable")
        self.assertEqual(request["headers"]["X-DashScope-Async"], "enable")
        self.assertTrue(request["json"]["parameters"]["diarization_enabled"])
        self.assertEqual(request["json"]["parameters"]["speaker_count"], 3)

    def test_success_download_never_receives_api_key(self):
        session = Session([Response({"output": {"task_status": "SUCCEEDED", "results": [
            {"subtask_status": "SUCCEEDED", "transcription_url": "https://result.oss-cn-beijing.aliyuncs.com/result"}]}}), Response(PAYLOAD)])
        service = CloudService("test-key", session=session)
        self.assertEqual(service.wait_result("task-123"), PAYLOAD)
        self.assertNotIn("Authorization", session.calls[-1][2]["headers"])

    def test_failed_subtask_not_treated_as_success(self):
        session = Session([Response({"output": {"task_status": "SUCCEEDED", "results": [{"subtask_status": "FAILED"}]}})])
        with self.assertRaises(AppError):
            CloudService("test-key", session=session).wait_result("task-123")

    def test_cancellation_prevents_requests(self):
        stop = threading.Event()
        stop.set()
        session = Session([])
        with self.assertRaises(Cancelled):
            CloudService("test-key", cancel=stop, session=session).wait_result("task-123")
        self.assertEqual(session.calls, [])

    def test_post_is_not_retried(self):
        session = Session([Response({}, 503), Response({})])
        with self.assertRaises(AppError):
            CloudService("test-key", session=session).submit("oss://audio")
        self.assertEqual(len(session.calls), 1)

    def test_errors_do_not_echo_secrets(self):
        session = Session([Response({"message": "test-key private details"}, 401)])
        with self.assertRaises(AppError) as error:
            CloudService("test-key", session=session).submit("oss://audio")
        self.assertNotIn("test-key", str(error.exception))

    def test_url_and_workspace_validation(self):
        for url in ["http://x.aliyuncs.com/a", "https://aliyuncs.com.attacker.test/a", "https://127.0.0.1/a", "https://user@x.aliyuncs.com/a"]:
            with self.assertRaises(AppError):
                trusted_url(url)
        with self.assertRaises(AppError):
            CloudService("test-key", workspace="invalid/host")
        service = CloudService("test-key", "新加坡", "workspace123")
        self.assertEqual(service.origin, "https://workspace123.ap-southeast-1.maas.aliyuncs.com")

    def test_summary_preserves_evidence_in_prompt(self):
        session = Session([Response({"choices": [{"finish_reason": "stop", "message": {"content": "摘要 [00:00:01]"}}]})])
        result = CloudService("test-key", session=session).summarize(example())
        self.assertIn("摘要", result)
        body = session.calls[0][2]["json"]
        self.assertIn("[00:00:01]", body["messages"][1]["content"])
        self.assertFalse(body["enable_thinking"])


class FakeCloud:
    fail_summary = False
    uploads = 0
    submits = 0
    folder = None
    def __init__(self, *args):
        pass
    def upload(self, path):
        type(self).uploads += 1
        return "oss://example/audio"
    def submit(self, *args):
        type(self).submits += 1
        return "test-task"
    def wait_result(self, task_id):
        saved = json.loads(next(Path(self.folder).glob("*.json")).read_text(encoding="utf-8"))
        assert saved["task_id"] == task_id
        return PAYLOAD
    def summarize(self, *args):
        if self.fail_summary:
            raise AppError("摘要暂时不可用")
        return "摘要结果"
    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def setUp(self):
        FakeCloud.fail_summary, FakeCloud.uploads, FakeCloud.submits = False, 0, 0
        self.folder = tempfile.TemporaryDirectory()
        FakeCloud.folder = self.folder.name
    def tearDown(self):
        self.folder.cleanup()
    def run_job(self, doc, **kwargs):
        return run_pipeline(doc, "not-a-real-key", self.folder.name, threading.Event(), lambda _: None,
                            service_factory=FakeCloud, extractor=lambda *_: 4200, **kwargs)
    def test_full_pipeline_persists_before_poll_and_no_key(self):
        doc = self.run_job(new_document("test.mp4"))
        saved = next(Path(self.folder.name).glob("*.json")).read_text(encoding="utf-8")
        self.assertNotIn("not-a-real-key", saved)
        self.assertEqual(doc["status"], "已完成")
        self.assertEqual(FakeCloud.submits, 1)
    def test_resume_does_not_upload_or_submit(self):
        doc = new_document("missing-original.mp4")
        doc["task_id"] = "test-task"
        save_document(doc, self.folder.name)
        self.run_job(doc)
        self.assertEqual((FakeCloud.uploads, FakeCloud.submits), (0, 0))
    def test_summary_failure_keeps_transcript_and_retries_only_summary(self):
        FakeCloud.fail_summary = True
        doc = new_document("test.mp4")
        with self.assertRaises(AppError):
            self.run_job(doc)
        self.assertEqual(len(doc["segments"]), 2)
        self.assertEqual(doc["status"], "摘要待重试")
        FakeCloud.fail_summary = False
        self.run_job(doc, summary_only=True)
        self.assertEqual((FakeCloud.uploads, FakeCloud.submits), (1, 1))

    def test_ambiguous_submission_requires_manual_check(self):
        class TimeoutCloud(FakeCloud):
            def submit(self, *args):
                raise AppError("提交超时")
        doc = new_document("test.mp4")
        with self.assertRaises(AppError):
            run_pipeline(doc, "not-a-real-key", self.folder.name, threading.Event(), lambda _: None,
                         service_factory=TimeoutCloud, extractor=lambda *_: 4200)
        self.assertEqual(doc["status"], "提交结果待核查")
        self.assertFalse(doc["task_id"])


if __name__ == "__main__":
    unittest.main()
