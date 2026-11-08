"""Explicit installer for the offline English recognizer."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import requests
from bootstrap_local import ROOT, MODELS, allowed_path, download, model_info

REPOSITORY = "Systran/faster-whisper-small.en"


def install(models_only=False):
    if not models_only:
        env = os.environ.copy()
        env["TEMP"] = env["TMP"] = str(ROOT / ".tmp")
        (ROOT / ".tmp").mkdir(exist_ok=True)
        subprocess.run([str(Path(sys.base_prefix) / "python.exe"), "-m", "pip", "--isolated", "--python", str(ROOT / ".venv"),
                        "install", "--no-cache-dir", "--disable-pip-version-check", "--no-input", "--retries", "1", "--timeout", "90",
                        "-r", str(ROOT / "requirements-english.txt")], check=True, env=env)
    with requests.Session() as session:
        session.trust_env = False
        info = model_info(session, REPOSITORY)
        manifest = {"repository": REPOSITORY, "revision": info["sha"], "files": []}
        for entry in info["siblings"]:
            name = entry["rfilename"]
            allowed_path(name)
            if name not in {"model.bin", "config.json", "tokenizer.json", "vocabulary.json",
                            "vocabulary.txt", "preprocessor_config.json", "README.md", "LICENSE"}:
                continue
            lfs = entry.get("lfs") or {}
            size, sha = entry.get("size") or lfs.get("size"), lfs.get("sha256") or lfs.get("oid")
            download(session, f"https://huggingface.co/{REPOSITORY}/resolve/{info['sha']}/{name}?download=true",
                     MODELS / "whisper-small-en" / name, expected_size=size, expected_hash=sha)
            manifest["files"].append({"file": name, "size": size, "sha256": sha})
        (MODELS / "english-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("英文识别模型安装完成。", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-only", action="store_true")
    install(parser.parse_args().models_only)
