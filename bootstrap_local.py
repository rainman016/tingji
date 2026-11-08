"""Explicit, sequential installer. Never invoked automatically during transcription."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time

import requests

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
SPEECH_MODELS = {name: f"funasr/{name}" for name in ("paraformer-zh", "fsmn-vad", "ct-punc", "campplus")}
QWEN_REPO = "Qwen/Qwen3-4B-GGUF"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def allowed_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("不安全的模型文件路径")
    return path


def download(session, url, target, *, expected_size=None, expected_hash=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (not expected_size or target.stat().st_size == expected_size):
        if not expected_hash or digest(target) == expected_hash:
            print("已就绪：" + target.name, flush=True)
            return
    partial = target.with_suffix(target.suffix + ".part")
    if expected_size and expected_size > 32 * 1024 * 1024:
        return download_ranges(session, url, target, expected_size, expected_hash)
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with session.get(url, stream=True, headers=headers, timeout=(20, 90)) as response:
                if response.status_code == 416 and offset and expected_size == offset:
                    pass
                else:
                    response.raise_for_status()
                    if response.status_code != 206:
                        offset = 0
                    total = expected_size or (int(response.headers.get("Content-Length", 0)) + offset)
                    loaded, last = offset, time.monotonic()
                    with partial.open("ab" if offset else "wb") as stream:
                        for block in response.iter_content(2 * 1024 * 1024):
                            if not block:
                                continue
                            stream.write(block)
                            loaded += len(block)
                            if time.monotonic() - last >= 10:
                                print(f"下载 {target.name}: {loaded / 1024**2:.0f} / {total / 1024**2:.0f} MB", flush=True)
                                last = time.monotonic()
            if expected_size and partial.stat().st_size != expected_size:
                raise ValueError("文件大小校验未通过")
            if expected_hash and digest(partial) != expected_hash:
                partial.unlink(missing_ok=True)
                raise ValueError("文件校验未通过")
            os.replace(partial, target)
            print("完成：" + target.name, flush=True)
            return
        except (requests.RequestException, ValueError) as exc:
            print(f"下载未完成（{type(exc).__name__}），尝试 {attempt + 1}/3。可重新运行继续下载。", flush=True)
            if attempt == 2:
                raise RuntimeError("模型下载失败，已保留可续传部分。") from None
            time.sleep(2)


def download_ranges(session, url, target, expected_size, expected_hash):
    """Bound requests to avoid buffering a multi-GB response in network gateways."""
    from concurrent.futures import ThreadPoolExecutor
    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected_size:
        raise ValueError("续传文件大小异常，请核查 .part 文件。")
    print(f"分段下载 {target.name}: {offset / 1024**2:.0f} / {expected_size / 1024**2:.0f} MB", flush=True)
    last = time.monotonic()
    def fetch(bounds):
        start, end = bounds
        client = requests.Session()
        client.trust_env = False
        for attempt in range(3):
            try:
                with client.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=(20, 60)) as response:
                    response.raise_for_status()
                    content_range = response.headers.get("Content-Range", "")
                    if response.status_code != 206 or not content_range.startswith(f"bytes {start}-{end}/"):
                        raise ValueError("下载源没有正确响应分段请求。")
                    block = response.content
                    if len(block) != end - start + 1:
                        raise ValueError("分段长度不符。")
                client.close()
                return block
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    raise RuntimeError("分段下载失败，重新运行可从已有进度继续。") from None
                time.sleep(2)
        client.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        while offset < expected_size:
            bounds = [(start, min(expected_size - 1, start + 8 * 1024 * 1024 - 1))
                      for start in range(offset, min(expected_size, offset + 32 * 1024 * 1024), 8 * 1024 * 1024)]
            # map preserves range order; only complete contiguous groups are appended.
            blocks = list(pool.map(fetch, bounds))
            with partial.open("ab") as stream:
                for block in blocks:
                    stream.write(block)
                    offset += len(block)
            if time.monotonic() - last >= 10:
                print(f"下载 {target.name}: {offset / 1024**2:.0f} / {expected_size / 1024**2:.0f} MB", flush=True)
                last = time.monotonic()
    if expected_hash and digest(partial) != expected_hash:
        partial.unlink(missing_ok=True)
        raise ValueError("文件校验失败，已清理本次损坏的下载，可重新运行安装。")
    os.replace(partial, target)
    print("完成：" + target.name, flush=True)


def model_info(session, repo):
    response = session.get(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=(20, 60))
    response.raise_for_status()
    return response.json()


def install_models(asr_only=False, plan=False):
    MODELS.mkdir(exist_ok=True)
    session = requests.Session()
    # Public downloads require no tokens and do not consult netrc or credential files.
    session.trust_env = False
    manifest = {}
    for folder, repo in SPEECH_MODELS.items():
        info = model_info(session, repo)
        revision = info["sha"]
        files = []
        for entry in info["siblings"]:
            name = entry["rfilename"]
            allowed_path(name)
            if not any(fnmatch.fnmatch(name, pattern) for pattern in ("*.pt", "*.bin", "*.json", "*.yaml", "*.yml", "*.mvn", "*.txt", "seg_dict", "jieba_usr_dict", "LICENSE*", "README*")):
                continue
            lfs = entry.get("lfs") or {}
            size = entry.get("size") or lfs.get("size")
            sha = lfs.get("sha256") or lfs.get("oid")
            files.append({"file": name, "size": size, "sha256": sha})
            print(f"{repo}/{name} ({(size or 0) / 1024**2:.1f} MB)", flush=True)
            if not plan:
                download(session, f"https://huggingface.co/{repo}/resolve/{revision}/{name}?download=true",
                         MODELS / folder / name, expected_size=size, expected_hash=sha)
        manifest[folder] = {"repository": repo, "revision": revision, "files": files}
    if not asr_only:
        info = model_info(session, QWEN_REPO)
        entry = next(x for x in info["siblings"] if x["rfilename"].lower().endswith("q4_k_m.gguf"))
        lfs = entry.get("lfs") or {}
        size, sha = entry.get("size") or lfs.get("size"), lfs.get("sha256") or lfs.get("oid")
        print(f"Qwen 摘要模型：{(size or 0) / 1024**3:.2f} GB", flush=True)
        if not plan:
            download(session, f"https://huggingface.co/{QWEN_REPO}/resolve/{info['sha']}/{entry['rfilename']}?download=true",
                     MODELS / "qwen3-4b-q4_k_m.gguf", expected_size=size, expected_hash=sha)
            for item in info["siblings"]:
                if item["rfilename"].upper() in ("LICENSE", "LICENSE.TXT", "README.MD"):
                    download(session, f"https://huggingface.co/{QWEN_REPO}/resolve/{info['sha']}/{item['rfilename']}",
                             MODELS / "qwen-license" / item["rfilename"])
        manifest["summary"] = {"repository": QWEN_REPO, "revision": info["sha"], "file": entry["rfilename"], "size": size, "sha256": sha}
    if not plan:
        (MODELS / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    session.close()


def install_dependencies(asr_only=False):
    from prepare_runtime import main
    main(asr_only=asr_only)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-only", action="store_true")
    parser.add_argument("--asr-only", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if not args.models_only and not args.plan:
        install_dependencies(args.asr_only)
    install_models(args.asr_only, args.plan)
    print("本地模型准备完成。" if not args.plan else "仅查看完毕，未下载模型。", flush=True)
