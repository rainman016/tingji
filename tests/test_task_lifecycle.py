import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from core import AppError, Cancelled, new_document
from local_service import LocalService, reconcile_interrupted_document, run_local_pipeline
from process_control import owner_is_alive, process_owner, start_owned_process
from media_fixture import copy_audio, make_source


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StubService:
    def __init__(self, *args):
        pass

    def transcribe(self, audio, count, language="zh", checkpoint_dir=None):
        return {"segments": [{"start_ms": 0, "end_ms": 1000, "speaker": "0", "text": "保留的文字"}],
                "speakers": {"0": "说话人 1"}, "device": "cpu", "seconds": 1}

    def summarize(self, document, style):
        return "摘要"


class LifecycleTests(unittest.TestCase):
    def test_persisted_stages_and_detached_snapshots_arrive_before_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            doc = new_document(make_source(folder, duration_ms=1000))
            def snapshot(value):
                saved = json.loads((Path(folder) / (doc["id"] + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(saved, value)
                self.assertIn("updated_at", value)
                self.assertIn("task_started_at", value)
                self.assertEqual(value["task_owner"], process_owner())
                events.append(value)
            class Service(StubService):
                def summarize(inner, document, style):
                    self.assertEqual([event["status"] for event in events],
                                     ["校验恢复进度", "提取音频", "本地识别中", "已转写", "生成摘要"])
                    self.assertEqual(events[3]["segments"][0]["text"], "保留的文字")
                    return "摘要"
            with patch("local_service.ROOT", Path(folder)):
                run_local_pipeline(doc, folder, threading.Event(), lambda _: None,
                                   service_factory=Service, extractor=copy_audio, on_document=snapshot, auto_summary=True)
            self.assertEqual(events[2]["duration_ms"], 1000)
            self.assertEqual(events[-1]["status"], "已完成")
            events[3]["segments"][0]["text"] = "仅修改快照"
            self.assertEqual(doc["segments"][0]["text"], "保留的文字")
            self.assertEqual(events[4]["segments"][0]["text"], "保留的文字")

    def test_summary_failure_publishes_saved_transcript_and_failure(self):
        class FailingService(StubService):
            def summarize(self, *args):
                raise AppError("测试摘要失败")
        with tempfile.TemporaryDirectory() as folder, patch("local_service.ROOT", Path(folder)):
            doc, events = new_document(make_source(folder, duration_ms=1000)), []
            with self.assertRaisesRegex(AppError, "测试摘要失败"):
                run_local_pipeline(doc, folder, threading.Event(), lambda _: None,
                                   service_factory=FailingService, extractor=copy_audio,
                                   on_document=events.append, auto_summary=True)
            self.assertEqual(events[-1]["status"], "摘要待重试")
            self.assertTrue(events[-1]["segments"])
            self.assertEqual(json.loads((Path(folder) / (doc["id"] + ".json")).read_text(encoding="utf-8")), doc)

    def test_cancel_after_extraction_persists_stopped_without_starting_asr(self):
        cancel = threading.Event()
        def extract(source, target, event):
            duration = copy_audio(source, target, event)
            cancel.set()
            return duration
        with tempfile.TemporaryDirectory() as folder, patch("local_service.ROOT", Path(folder)), \
             patch.object(StubService, "transcribe") as transcribe:
            events = []
            doc = new_document(make_source(folder, duration_ms=1000))
            with self.assertRaises(Cancelled):
                run_local_pipeline(doc, folder, cancel, lambda _: None, service_factory=StubService,
                                   extractor=extract, on_document=events.append, auto_summary=True)
            transcribe.assert_not_called()
            self.assertEqual(events[-1]["status"], "已停止")
            self.assertEqual(events[-1]["task_owner"], process_owner())

    def test_reconciliation_never_changes_original_or_a_live_owners_status(self):
        doc = new_document("sample.mp4")
        doc.update(status="本地识别中", task_owner=process_owner())
        original = copy.deepcopy(doc)
        result = reconcile_interrupted_document(doc)
        self.assertEqual(result["status"], "本地识别中")
        self.assertEqual(doc, original)
        self.assertIsNot(result, doc)

    def test_missing_owner_is_uncertain_and_reused_pid_is_interrupted(self):
        doc = new_document("sample.mp4")
        doc["status"] = "提取音频"
        self.assertEqual(reconcile_interrupted_document(doc)["status"], "状态待核实")
        doc["task_owner"] = {"pid": 1234, "creation_time": "old"}
        with patch("process_control._process_stamp", return_value=(True, "new")):
            result = reconcile_interrupted_document(doc)
        self.assertEqual(result["status"], "已中断")
        self.assertIn("重新开始转写", result["error"])
        self.assertEqual(doc["status"], "提取音频")
        with patch("process_control._process_stamp", return_value=(None, None)):
            self.assertEqual(reconcile_interrupted_document(doc)["status"], "状态待核实")

    def test_unchanged_progress_reports_elapsed_time_without_fake_percentage(self):
        class FakeProcess:
            returncode = 0
            def __init__(self):
                self.polls = iter((None, 0))
            def poll(self):
                return next(self.polls)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        def start(command, **kwargs):
            Path(command[3]).write_text(json.dumps({"ok": True, "data": {"done": True}}), encoding="utf-8")
            Path(command[4]).write_text(json.dumps({"message": "正在识别文字"}), encoding="utf-8")
            return FakeProcess()
        reports = []
        with tempfile.TemporaryDirectory() as folder, patch("local_service.ROOT", Path(folder)), \
             patch("local_service.start_owned_process", side_effect=start), \
             patch("local_service.time.monotonic", side_effect=(100, 111)):
            result = LocalService(threading.Event(), reports.append).run_worker({"mode": "asr"})
        self.assertEqual(result, {"done": True})
        self.assertIn("正在识别文字", reports[-1])
        self.assertIn("0:11", reports[-1])
        self.assertIn("不代表完成率", reports[-1])


CHILD_CODE = """import json, sys, time
from pathlib import Path
from process_control import process_owner
Path(sys.argv[1]).write_text(json.dumps(process_owner()), encoding='utf-8')
time.sleep(120)
"""
PARENT_CODE = """import os, subprocess, sys, time
flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
child = subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]], creationflags=flags)
if sys.argv[3] == 'early':
    raise SystemExit(0)
time.sleep(120)
"""


@unittest.skipUnless(os.name == "nt", "Windows Job Object ownership")
class ProcessTreeTests(unittest.TestCase):
    def await_child(self, marker):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                return json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(.02)
        self.fail("测试子进程没有写入身份信息")

    def command(self, marker, early=False):
        return [sys.executable, "-c", PARENT_CODE, str(marker), CHILD_CODE, "early" if early else "wait"]

    def test_closing_owned_tree_reaps_descendants_and_preserves_bystander(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "child.json"
            # The bystander is an independently owned test process, never added to the job.
            bystander = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(120)"],
                                         creationflags=subprocess.CREATE_NO_WINDOW)
            tree = None
            try:
                tree = start_owned_process(self.command(marker), cwd=PROJECT_ROOT,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                child = self.await_child(marker)
                self.assertTrue(owner_is_alive(child))
                tree.close()
                self.assertFalse(owner_is_alive(child))
                self.assertIsNotNone(tree.poll())
                self.assertIsNone(bystander.poll())
                tree.close()  # Idempotent, including after the original tree is gone.
                self.assertIsNone(bystander.poll())
            finally:
                if tree:
                    tree.close()
                if bystander.poll() is None:
                    bystander.terminate()
                bystander.wait(timeout=5)

    def test_descendants_are_reaped_even_after_parent_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "child.json"
            with start_owned_process(self.command(marker, early=True), cwd=PROJECT_ROOT,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW) as tree:
                child = self.await_child(marker)
                deadline = time.monotonic() + 8
                while tree.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(tree.poll(), 0)
                self.assertTrue(owner_is_alive(child))
                kernel = tree._job.kernel
                with patch.object(kernel, "WaitForSingleObject", wraps=kernel.WaitForSingleObject) as wait:
                    tree.close()
                    self.assertGreaterEqual(wait.call_count, 1)
                self.assertFalse(owner_is_alive(child))
            self.assertFalse(owner_is_alive(child))

    def test_service_cancel_and_timeout_both_reap_tree_and_keep_diagnostic(self):
        for reason in ("cancel", "timeout"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as folder:
                marker = Path(folder) / "child.json"
                cancel = threading.Event()
                children = []
                def start(command, **kwargs):
                    kwargs["cwd"] = PROJECT_ROOT
                    tree = start_owned_process(self.command(marker), **kwargs)
                    try:
                        children.append(self.await_child(marker))
                    except BaseException:
                        tree.close()
                        raise
                    if reason == "cancel":
                        cancel.set()
                    return tree
                with patch("local_service.ROOT", Path(folder)), \
                     patch("local_service.start_owned_process", side_effect=start):
                    with self.assertRaises(Cancelled if reason == "cancel" else AppError):
                        LocalService(cancel, lambda _: None).run_worker({"mode": "asr"}, timeout=0)
                self.assertFalse(owner_is_alive(children[0]))
                log = Path(folder) / ".tmp" / "last-local-error.log"
                self.assertTrue(log.is_file())
                self.assertLess(log.stat().st_size, 12000)
                self.assertFalse(list((Path(folder) / ".tmp").glob("local-job-*")))


if __name__ == "__main__":
    unittest.main()
