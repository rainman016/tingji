"""Explicit installer for the multilingual language detector, never called by inference."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import requests

from bootstrap_local import ROOT, MODELS, allowed_path, download, model_info
from language_detector import MODEL_FOLDER, REQUIRED_FILES

REPOSITORY = "Systran/faster-whisper-tiny"
ALLOWED_FILES = set(REQUIRED_FILES) | {"preprocessor_config.json", "README.md", "LICENSE", "LICENSE.txt"}


def install(models_only=False):
    if not models_only:
        env = os.environ.copy()
        (ROOT / ".tmp").mkdir(exist_ok=True)
        env["TEMP"] = env["TMP"] = str(ROOT / ".tmp")
        subprocess.run([str(Path(sys.base_prefix) / "python.exe"), "-m", "pip", "--isolated",
                        "--python", str(ROOT / ".venv"), "install", "--no-cache-dir",
                        "--disable-pip-version-check", "--no-input", "--retries", "1", "--timeout", "90",
                        "-r", str(ROOT / "requirements-english.txt")], check=True, env=env)
    with requests.Session() as session:
        session.trust_env = False
        info = model_info(session, REPOSITORY)
        revision = info["sha"]
        entries = {entry["rfilename"]: entry for entry in info["siblings"]}
        for name in entries:
            allowed_path(name)
        if not set(REQUIRED_FILES).issubset(entries):
            raise ValueError("语言检测模型文件清单不完整，未开始下载，请稍后重试。")
        selected = []
        for name, entry in entries.items():
            if name not in ALLOWED_FILES:
                continue
            lfs = entry.get("lfs") or {}
            size = entry.get("size") or lfs.get("size")
            sha = lfs.get("sha256") or lfs.get("oid")
            if name in REQUIRED_FILES and (not isinstance(size, int) or size <= 0):
                raise ValueError("语言检测模型缺少文件大小信息，未开始下载。")
            if name == "model.bin" and (not isinstance(sha, str) or len(sha) != 64
                                        or any(char not in "0123456789abcdef" for char in sha.lower())):
                raise ValueError("语言检测模型缺少有效的 SHA256 校验信息，未开始下载。")
            selected.append((name, size, sha))
        manifest = {"repository": REPOSITORY, "revision": revision,
                    "purpose": "source-language-detection", "files": []}
        for name, size, sha in selected:
            download(session, f"https://huggingface.co/{REPOSITORY}/resolve/{revision}/{name}?download=true",
                     MODELS / MODEL_FOLDER / name, expected_size=size, expected_hash=sha)
            manifest["files"].append({"file": name, "size": size, "sha256": sha})
        MODELS.mkdir(parents=True, exist_ok=True)
        target = MODELS / "language-manifest.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    print("自动识别语言模型安装完成。重新打开听记后可选择“自动检测”。", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-only", action="store_true", help="仅下载检测模型，不安装已有推理依赖")
    install(parser.parse_args().models_only)
