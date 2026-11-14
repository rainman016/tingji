import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from core import AppError, SummaryGPUUnavailable
from local_service import LocalService
import summary_backend as backend
import install_summary_gpu as installer


class SummaryGPUBehaviorTests(unittest.TestCase):
    def test_archived_summary_keeps_actual_device_and_fallback_provenance(self):
        from summary_state import snapshot, restore
        doc={'summary':'旧版摘要','summary_inference':{'device':'gpu','seconds':12}}
        archived=snapshot(doc)
        doc.update(summary='另一版本',summary_inference={'device':'cpu','fallback_reason':'device lost'})
        restore(doc,archived)
        self.assertEqual(doc['summary_inference'],{'device':'gpu','seconds':12})
        self.assertEqual(doc['previous_summary']['metadata']['summary_inference'],
                         {'device':'cpu','fallback_reason':'device lost'})

    def test_cpu_choice_always_wins_even_with_gpu_installed(self):
        self.assertEqual(backend.select_backend({'device': 'cpu'}, {'ready': True}), 'cpu')

    def test_auto_uses_gpu_only_when_runtime_is_ready(self):
        self.assertEqual(backend.select_backend({}, {'ready': True}), 'vulkan')
        self.assertEqual(backend.select_backend({}, {'ready': False}), 'cpu')
        with self.assertRaises(SummaryGPUUnavailable):
            backend.select_backend({'summary_backend': 'gpu'}, {'ready': False})

    def test_activation_requires_pinned_manifest_and_complete_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); site = root / '.runtime' / backend.SITE_NAME
            self.assertFalse(backend.gpu_runtime_status(root)['ready'])
            for name in backend.REQUIRED:
                target = site / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'fixture')
            manifest = {'schema': 1, 'site': backend.SITE_NAME, 'version': backend.VERSION,
                        'wheel_sha256': backend.WHEEL_SHA256, 'probe': {'offloaded_layers': 37, 'device': 'gpu'}}
            path = root / '.runtime' / 'summary-gpu.json'; path.write_text(json.dumps(manifest))
            self.assertTrue(backend.gpu_runtime_status(root)['ready'])
            manifest['site'] = '../outside'; path.write_text(json.dumps(manifest))
            self.assertFalse(backend.gpu_runtime_status(root)['ready'])
            manifest['site'] = backend.SITE_NAME; path.write_text(json.dumps(manifest)); (site / backend.REQUIRED[-1]).unlink()
            self.assertFalse(backend.gpu_runtime_status(root)['ready'])

    def test_gpu_failure_retries_cpu_in_a_separate_worker_request(self):
        reports = []
        service = LocalService(threading.Event(), reports.append)
        service.summary_checkpoint_dir = Path('summary-cache-fixture')
        with patch('local_service.local_status', return_value={'summary': True}), \
             patch('local_service.gpu_runtime_status', return_value={'ready': True}), \
             patch.object(service, 'run_worker', side_effect=[SummaryGPUUnavailable('device lost'),
                  {'summary': '完成摘要', 'inference': {'device': 'cpu'}}]) as worker:
            self.assertEqual(service.summarize({}, '通用摘要'), '完成摘要')
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(worker.call_args_list[0].args[0]['summary_backend'], 'gpu')
        self.assertEqual(worker.call_args_list[1].args[0]['device'], 'cpu')
        self.assertEqual(worker.call_args_list[0].args[0]['summary_checkpoint_dir'],
                         worker.call_args_list[1].args[0]['summary_checkpoint_dir'])
        self.assertIn('CPU', reports[0])
        self.assertEqual(service.last_summary_inference['fallback_reason'], 'device lost')

    def test_logical_summary_failure_does_not_trigger_expensive_cpu_retry(self):
        service = LocalService(threading.Event(), lambda _: None)
        with patch('local_service.local_status', return_value={'summary': True}), \
             patch('local_service.gpu_runtime_status', return_value={'ready': True}), \
             patch.object(service, 'run_worker', side_effect=AppError('摘要未完整生成')) as worker:
            with self.assertRaises(AppError): service.summarize({}, '通用摘要')
        self.assertEqual(worker.call_count, 1)

    def test_cancelled_gpu_attempt_is_not_restarted_on_cpu(self):
        cancel = threading.Event(); cancel.set()
        service = LocalService(cancel, lambda _: None)
        with patch('local_service.local_status', return_value={'summary': True}), \
             patch('local_service.gpu_runtime_status', return_value={'ready': True}), \
             patch.object(service, 'run_worker', side_effect=SummaryGPUUnavailable('stopped')) as worker:
            with self.assertRaises(SummaryGPUUnavailable): service.summarize({}, '通用摘要')
        self.assertEqual(worker.call_count, 1)

    def test_crashed_gpu_worker_without_result_is_classified_for_fallback(self):
        class ExitedProcess:
            returncode = 1
            def poll(self): return 0
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with tempfile.TemporaryDirectory() as folder, patch('local_service.ROOT', Path(folder)), \
             patch('local_service.start_owned_process', return_value=ExitedProcess()):
            service = LocalService(threading.Event(), lambda _: None)
            with self.assertRaises(SummaryGPUUnavailable):
                service.run_worker({'mode': 'summary', 'summary_backend': 'gpu'})


class SummaryGPUInstallerTests(unittest.TestCase):
    def test_transient_windows_locks_during_commit_do_not_break_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); original_replace = os.replace; blocked = set()
            def replace(source, destination):
                destination = Path(destination)
                if destination not in blocked:
                    blocked.add(destination)
                    error = PermissionError('temporary file lock'); error.winerror = 5
                    raise error
                return original_replace(source, destination)
            with patch.object(installer.os, 'replace', side_effect=replace), \
                 patch.object(installer.time, 'sleep') as sleep:
                self.install_fixture(root)
            self.assertEqual(sleep.call_count, 2)
            self.assertTrue(backend.gpu_runtime_status(root)['ready'])

    def test_persistent_windows_lock_fails_without_replacing_previous_activation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); active = root / '.runtime' / 'summary-gpu.json'
            active.parent.mkdir(); active.write_text('old activation')
            error = PermissionError('persistent file lock'); error.winerror = 32
            with patch.object(installer.os, 'replace', side_effect=error) as replace, \
                 patch.object(installer.time, 'sleep') as sleep:
                with self.assertRaises(PermissionError): self.install_fixture(root)
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(sleep.call_count, 4)
            self.assertEqual(active.read_text(), 'old activation')
            self.assertFalse(list(active.parent.glob('.summary-gpu-stage-*')))

    def test_unrelated_file_error_is_not_retried(self):
        error = OSError('disk error'); error.winerror = 112
        with patch.object(installer.os, 'replace', side_effect=error) as replace, \
             patch.object(installer.time, 'sleep') as sleep:
            with self.assertRaises(OSError): installer.replace_with_retry('stage', 'target')
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()

    def test_official_sdk_duplicates_are_skipped_but_runtime_is_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'test.whl'; stage = Path(folder) / 'stage'
            with zipfile.ZipFile(archive, 'w') as output:
                for name in backend.REQUIRED: output.writestr(name, b'fixture')
                for name in ('bin/llama.dll', 'include/llama.h', 'lib/llama.lib'): output.writestr(name, b'sdk')
            installer.extract_wheel(archive, stage)
            self.assertTrue(all((stage / name).is_file() for name in backend.REQUIRED))
            self.assertFalse((stage / 'bin').exists())

    def test_archive_cannot_escape_target_or_install_other_packages(self):
        for name in ('../escape.dll', 'C:/escape.dll', 'other_package/code.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                archive = Path(folder) / 'test.whl'
                with zipfile.ZipFile(archive, 'w') as output: output.writestr(name, b'fixture')
                with self.assertRaises(ValueError): installer.extract_wheel(archive, Path(folder) / 'stage')

    def install_fixture(self, root, fail_probe=False):
        def download(_session, url, target, **kwargs):
            self.assertEqual(url, installer.WHEEL_URL)
            self.assertEqual(kwargs['expected_hash'], backend.WHEEL_SHA256)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target, 'w') as archive:
                for name in backend.REQUIRED: archive.writestr(name, b'fixture')
        with patch.object(installer, 'download', side_effect=download), \
             patch.object(installer, 'digest', return_value=backend.WHEEL_SHA256), \
             patch.object(installer, 'probe_runtime', side_effect=RuntimeError('no GPU') if fail_probe else None,
                          return_value={'device': 'gpu', 'offloaded_layers': 37}):
            installer.install(root)

    def test_isolated_install_preserves_existing_cpu_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); cpu = root / '.venv' / 'cpu-sentinel'; cpu.parent.mkdir(); cpu.write_text('keep')
            self.install_fixture(root)
            self.assertEqual(cpu.read_text(), 'keep')
            self.assertTrue(backend.gpu_runtime_status(root)['ready'])

    def test_failed_probe_keeps_previous_activation_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); active = root / '.runtime' / 'summary-gpu.json'; active.parent.mkdir(); active.write_text('old activation')
            with self.assertRaises(RuntimeError): self.install_fixture(root, fail_probe=True)
            self.assertEqual(active.read_text(), 'old activation')


if __name__ == '__main__': unittest.main()
