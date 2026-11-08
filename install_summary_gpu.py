"""Install an official pinned Vulkan wheel beside, never over, the CPU environment."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests
from bootstrap_local import download, digest
from summary_backend import ROOT, VERSION, SITE_NAME, WHEEL_SHA256, REQUIRED
from process_control import start_owned_process

WHEEL_NAME = f'llama_cpp_python-{VERSION}-py3-none-win_amd64.whl'
WHEEL_URL = f'https://github.com/abetlen/llama-cpp-python/releases/download/v{VERSION}-vulkan/{WHEEL_NAME}'
WHEEL_SIZE = 42052135


def replace_with_retry(source, destination):
    # Windows can briefly hold freshly extracted files during an atomic rename.
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 4:
                raise
            time.sleep(.1 * 2 ** attempt)


def extract_wheel(wheel, destination):
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.infolist()
        selected = []
        if sum(entry.file_size for entry in entries) > 500 * 1024 * 1024:
            raise ValueError('显卡引擎解压大小异常。')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('显卡引擎包包含不安全路径。')
            if path.parts and path.parts[0] in ('bin', 'include', 'lib'):
                # Official SDK duplicates are unnecessary for the Python runtime.
                continue
            if path.parts and path.parts[0] != 'llama_cpp' and not path.parts[0].endswith('.dist-info'):
                raise ValueError('显卡引擎包含意外的顶层目录。')
            selected.append(entry)
        archive.extractall(destination, members=selected)
    if not all((Path(destination) / name).is_file() for name in REQUIRED):
        raise ValueError('显卡引擎文件不完整。')


def probe_runtime(root, destination):
    env = os.environ.copy(); env['PYTHONUTF8'] = '1'
    command = [str(root / '.venv' / 'Scripts' / 'python.exe'), '-B',
               str(root / 'summary_backend.py'), str(destination)]
    with start_owned_process(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as process:
        stdout, stderr = process.process.communicate(timeout=180)
        if process.returncode:
            raise RuntimeError('显卡实测未通过，未启用新引擎：' + stderr.decode('utf-8', 'replace')[-500:])
    return json.loads(stdout.decode('utf-8').strip().splitlines()[-1])


def install(root=ROOT):
    root = Path(root).resolve()
    runtime = root / '.runtime'; runtime.mkdir(exist_ok=True)
    wheel = root / '.tmp' / 'wheels' / WHEEL_NAME
    with requests.Session() as session:
        session.trust_env = False
        download(session, WHEEL_URL, wheel, expected_size=WHEEL_SIZE, expected_hash=WHEEL_SHA256)
    if digest(wheel) != WHEEL_SHA256:
        raise ValueError('显卡引擎SHA256校验失败。')
    destination = runtime / SITE_NAME
    if not destination.exists():
        stage = Path(tempfile.mkdtemp(prefix='.summary-gpu-stage-', dir=runtime))
        try:
            extract_wheel(wheel, stage)
            replace_with_retry(stage, destination)
        finally:
            if stage.is_dir() and stage.resolve().parent == runtime.resolve() and stage.name.startswith('.summary-gpu-stage-'):
                shutil.rmtree(stage)
    probe = probe_runtime(root, destination)
    if probe.get('device') != 'gpu' or probe.get('offloaded_layers', 0) <= 0:
        raise RuntimeError('显卡卸载验证失败，未启用新引擎。')
    manifest = {'schema': 1, 'version': VERSION, 'site': SITE_NAME,
                'wheel_sha256': WHEEL_SHA256, 'source': WHEEL_URL, 'probe': probe}
    target = runtime / 'summary-gpu.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    replace_with_retry(temporary, target)
    print(json.dumps(probe, ensure_ascii=False), flush=True)
    print('显卡摘要引擎已启用，原CPU引擎保留。', flush=True)


if __name__ == '__main__':
    install()
