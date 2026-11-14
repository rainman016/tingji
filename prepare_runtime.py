"""Agent/setup helper: official wheel download with bounded ranges, then sequential install."""
import html
from importlib.metadata import PackageNotFoundError, version
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urldefrag, unquote, urljoin

import requests
from bootstrap_local import ROOT, download

CUDA_FLAVOR = "cu126"
TORCH_VERSION = "2.8.0"
RUNTIME_VERSION = f"{TORCH_VERSION}+{CUDA_FLAVOR}"
RUNTIME_INDEX = f"https://download.pytorch.org/whl/{CUDA_FLAVOR}"


def main(asr_only=True):
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("安装器需要 Windows x64 / Python 3.12，请先创建正确的项目虚拟环境。")
    base_python = Path(sys.base_prefix) / "python.exe"
    pip = [str(base_python), "-m", "pip", "--isolated", "--python", str(ROOT / ".venv"), "install", "--no-cache-dir", "--disable-pip-version-check", "--no-input", "--retries", "1", "--timeout", "120"]
    env = os.environ.copy()
    env["TEMP"] = env["TMP"] = str(ROOT / ".tmp")
    try:
        gpu_ready = all(version(name) == RUNTIME_VERSION for name in ("torch", "torchaudio"))
    except PackageNotFoundError:
        gpu_ready = False
    if not gpu_ready:
        with requests.Session() as session:
            session.trust_env = False
            index = RUNTIME_INDEX + "/torch/"
            response = session.get(index, timeout=30)
            response.raise_for_status()
            wheel_name = f"torch-{RUNTIME_VERSION}-cp312-cp312-win_amd64.whl"
            link = next(html.unescape(x) for x in re.findall(r'href="([^"]+)"', response.text) if wheel_name in unquote(x))
            url, fragment = urldefrag(urljoin(index, link))
            probe = session.get(url, headers={"Range": "bytes=0-0"}, timeout=30)
            probe.raise_for_status()
            total = int(probe.headers["Content-Range"].split("/")[-1])
            target = ROOT / ".tmp" / "wheels" / wheel_name
            download(session, url, target, expected_size=total, expected_hash=fragment.removeprefix("sha256="))
        subprocess.run(pip + [str(target), f"torchaudio=={RUNTIME_VERSION}", "--index-url", RUNTIME_INDEX], check=True, env=env)
    else:
        print("复用已安装的显卡运行组件。", flush=True)
    print("显卡依赖安装完成，开始安装识别依赖。", flush=True)
    subprocess.run(pip + ["-r", str(ROOT / "requirements-local.txt")], check=True, env=env)
    if not asr_only:
        subprocess.run(pip + ["llama-cpp-python==0.3.36", "--only-binary=llama-cpp-python", "--extra-index-url",
                              "https://abetlen.github.io/llama-cpp-python/whl/cpu"], check=True, env=env)
    print("本地依赖安装完成。", flush=True)


if __name__ == "__main__":
    main()
