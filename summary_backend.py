"""Isolated Vulkan summary runtime; CPU and GPU DLLs never share a worker."""
from __future__ import annotations

import ctypes
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time

from core import SummaryGPUUnavailable

ROOT = Path(__file__).resolve().parent
VERSION = '0.3.36'
SITE_NAME = 'summary-vulkan-' + VERSION
WHEEL_SHA256 = 'b3d58d70e9fa6216f3d1919bb6141a98eb22eb889e14a44d080dd250e969fab4'
REQUIRED = ('llama_cpp/__init__.py', 'llama_cpp/lib/llama.dll',
            'llama_cpp/lib/ggml.dll', 'llama_cpp/lib/ggml-base.dll',
            'llama_cpp/lib/ggml-cpu.dll', 'llama_cpp/lib/ggml-vulkan.dll')
_DLL_HANDLES = []
_CALLBACKS = []


def gpu_runtime_status(root=None):
    root = Path(root) if root is not None else ROOT
    result = {'ready': False, 'backend': 'cpu'}
    try:
        data = json.loads((root / '.runtime' / 'summary-gpu.json').read_text(encoding='utf-8'))
        if (data.get('schema') != 1 or data.get('version') != VERSION or
                data.get('site') != SITE_NAME or data.get('wheel_sha256') != WHEEL_SHA256 or
                data.get('probe', {}).get('offloaded_layers', 0) <= 0):
            return result
        site = root / '.runtime' / SITE_NAME
        if all((site / name).is_file() and (site / name).stat().st_size > 0 for name in REQUIRED):
            result.update(ready=True, backend='vulkan', site=str(site),
                          device=data['probe'].get('gpu_name', 'GPU'), version=VERSION)
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result


def select_backend(request, status=None):
    if request.get('device') == 'cpu' or request.get('summary_backend') == 'cpu':
        return 'cpu'
    status = status if status is not None else gpu_runtime_status()
    if request.get('summary_backend') == 'gpu' and not status['ready']:
        raise SummaryGPUUnavailable('显卡摘要引擎尚未安装完整。')
    return 'vulkan' if status['ready'] else 'cpu'


def _gpu_devices(package, site):
    package.llama_backend_init()
    library = ctypes.CDLL(str(site / 'llama_cpp' / 'lib' / 'ggml.dll'))
    base = ctypes.CDLL(str(site / 'llama_cpp' / 'lib' / 'ggml-base.dll'))
    library.ggml_backend_dev_count.restype = ctypes.c_size_t
    library.ggml_backend_dev_get.argtypes = [ctypes.c_size_t]
    library.ggml_backend_dev_get.restype = ctypes.c_void_p
    for name in ('ggml_backend_dev_name', 'ggml_backend_dev_description'):
        function = getattr(base, name)
        function.argtypes = [ctypes.c_void_p]; function.restype = ctypes.c_char_p
    base.ggml_backend_dev_type.argtypes = [ctypes.c_void_p]
    base.ggml_backend_dev_type.restype = ctypes.c_int
    devices = []
    for i in range(library.ggml_backend_dev_count()):
        device = library.ggml_backend_dev_get(i)
        if base.ggml_backend_dev_type(device) not in (1, 2):
            continue
        devices.append({'name': base.ggml_backend_dev_name(device).decode('utf-8', 'replace'),
                        'description': base.ggml_backend_dev_description(device).decode('utf-8', 'replace')})
    return devices


def load_summary_model(request, progress, *, site_override=None, context=6144):
    started = time.monotonic()
    status = gpu_runtime_status()
    if site_override is not None:
        status = {'ready': True, 'site': str(site_override)}
    backend = select_backend(request, status)
    model_path = str(ROOT / 'models' / 'qwen3-4b-q4_k_m.gguf')
    if backend == 'cpu':
        progress('正在加载本地摘要模型（CPU）…')
        from llama_cpp import Llama
        model = Llama(model_path=model_path, n_ctx=context, n_batch=128, n_ubatch=128,
                      n_threads=6, n_threads_batch=6, n_gpu_layers=0, verbose=False)
        return model, {'device': 'cpu', 'backend': 'llama-cpp-python-cpu', 'version': VERSION,
                       'load_seconds': round(time.monotonic() - started, 3)}
    model = None
    try:
        site = Path(status['site']).resolve()
        if 'llama_cpp' in sys.modules:
            raise SummaryGPUUnavailable('当前进程已加载摘要引擎，需要新进程启用显卡。')
        sys.path.insert(0, str(site))
        if os.name == 'nt':
            _DLL_HANDLES.append(os.add_dll_directory(str(site / 'llama_cpp' / 'lib')))
        os.environ['LLAMA_CPP_LIB_PATH'] = str(site / 'llama_cpp' / 'lib')
        package = importlib.import_module('llama_cpp')
        if Path(package.__file__).resolve().parent.parent != site or package.__version__ != VERSION:
            raise SummaryGPUUnavailable('显卡摘要引擎路径或版本不符。')
        if not package.llama_supports_gpu_offload():
            raise SummaryGPUUnavailable('当前摘要引擎没有显卡卸载能力。')
        messages = []
        @package.llama_log_callback
        def capture(_level, message, _data):
            if message:
                text = message.decode('utf-8', 'replace')[:2000]
                if 'offloaded ' in text or (_level in (1, 2, 3) and len(messages) < 200):
                    messages.append(text)
        _CALLBACKS.append(capture)
        package.llama_log_set(capture, None)
        devices = _gpu_devices(package, site)
        if not devices:
            raise SummaryGPUUnavailable('没有可用的Vulkan显卡设备。')
        index = next((i for i, item in enumerate(devices) if 'nvidia' in item['description'].lower()), 0)
        device = devices[index]['description']
        progress('正在加载本地摘要模型（显卡 · ' + device + '）…')
        model = package.Llama(model_path=model_path, n_ctx=context, n_batch=512, n_ubatch=256,
                              n_threads=6, n_threads_batch=6, n_gpu_layers=-1,
                              main_gpu=index, split_mode=package.LLAMA_SPLIT_MODE_NONE, verbose=False)
        proof = re.findall(r'offloaded\s+(\d+)/(\d+)\s+layers to GPU', ''.join(messages))
        if not proof or int(proof[-1][0]) <= 0:
            raise SummaryGPUUnavailable('模型没有实际卸载到显卡，拒绝将CPU运行标记为显卡加速。')
        layers, total = map(int, proof[-1])
        info = {'device': 'gpu', 'backend': 'vulkan', 'version': VERSION, 'gpu_name': device,
                'offloaded_layers': layers, 'total_layers': total,
                'load_seconds': round(time.monotonic() - started, 3)}
        progress(f'显卡摘要已就绪 · {layers}/{total} 层已卸载到显卡')
        return model, info
    except Exception as exc:
        if model is not None:
            model.close()
        if isinstance(exc, SummaryGPUUnavailable):
            raise
        raise SummaryGPUUnavailable('显卡摘要无法启动：' + str(exc)[:300]) from exc


if __name__ == '__main__':
    from local_worker import block_network
    block_network()
    model, info = load_summary_model({'summary_backend': 'gpu'}, lambda _: None,
                                     site_override=Path(sys.argv[1]), context=1024)
    try:
        response = model.create_completion(prompt='说明一次测试成功。\n', max_tokens=16, temperature=0)
        info['probe_text'] = response['choices'][0]['text'][:80]
    finally:
        model.close()
    print(json.dumps(info, ensure_ascii=False), flush=True)
