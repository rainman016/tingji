"""Explicit local model smoke test against a public sample; no private media is accessed."""
import argparse
import json
from pathlib import Path
import threading
import time

from core import new_document, export_text
from local_service import ROOT, run_local_pipeline


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--no-summary", action="store_true")
    parser.add_argument("--speakers", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cpu"), default="auto")
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    args = parser.parse_args()
    document = new_document(args.audio.resolve())
    directory = ROOT / ".tmp" / "verification"
    directory.mkdir(exist_ok=True)
    started = time.monotonic()
    run_local_pipeline(document, directory, threading.Event(), lambda text: print(text, flush=True),
                       auto_summary=not args.no_summary, speaker_count=args.speakers, device=args.device, language=args.language)
    print(json.dumps({"seconds_total": round(time.monotonic() - started, 2),
                      "duration_ms": document["duration_ms"],
                      "segments": len(document["segments"]), "speakers": len(document["speakers"]),
                      "inference": document.get("local_inference"), "text": [s["text"] for s in document["segments"]],
                      "summary": document["summary"]}, ensure_ascii=False, indent=2), flush=True)
    suffix = f"-{args.language}-{args.speakers}-speakers-{args.device}" if args.language != "zh" or args.speakers or args.device != "auto" else ""
    if args.language == "en":
        suffix += "-" + args.audio.stem
    (directory / f"sample-transcript{suffix}.md").write_text(export_text(document, "md"), encoding="utf-8")
