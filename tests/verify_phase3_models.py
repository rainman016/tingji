"""Explicit model smoke test using synthetic WAVs; never part of unit discovery.

Run only after the user authorizes model execution. Arguments: generated audio
directory containing en.wav and zh.wav. Results remain in that directory.
"""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Cancelled, new_document
from local_service import LocalService, run_local_pipeline
from transcription_recovery import checkpoint_for, recovery_info


class BoundedService(LocalService):
    def run_worker(self, request, timeout=7200):
        return super().run_worker(request, timeout=min(timeout, 180))


def main(directory):
    started = time.monotonic()
    cancel = threading.Event()
    service = BoundedService(cancel, lambda message: None, "auto")
    report = {}
    for language in ("en", "zh"):
        detection = service.detect_language(directory / f"{language}.wav")
        assert detection["language"] == language and not detection["needs_confirmation"], detection
        report[language + "_detection"] = {key: detection[key] for key in ("language", "confidence", "needs_confirmation")}
        print(language, "detection", report[language + "_detection"], flush=True)
    with wave.open(str(directory / "en.wav"), "rb") as original:
        params = original.getparams()
        frames = original.readframes(original.getnframes())
    total = 125 * params.framerate * params.nchannels * params.sampwidth
    with wave.open(str(directory / "en-repeated.wav"), "wb") as output:
        output.setparams(params)
        output.writeframes((frames * (total // len(frames) + 1))[:total])
    document = new_document(directory / "en-repeated.wav")
    results = directory / "results"
    store = checkpoint_for(results, document["id"])
    stopped = threading.Event()
    triggered = threading.Event()

    def interrupt_after_commit():
        while not stopped.wait(.05):
            if store.load_unit("asr-00000") is not None:
                triggered.set()
                cancel.set()
                return

    monitor = threading.Thread(target=interrupt_after_commit, name="smoke-cancel-monitor")
    monitor.start()
    try:
        try:
            run_local_pipeline(document, results, cancel, lambda message: None, language="auto",
                               auto_summary=False, service_factory=BoundedService)
            raise AssertionError("Expected cancellation after the first durable ASR block")
        except Cancelled:
            assert triggered.is_set()
    finally:
        stopped.set()
        monitor.join(timeout=2)
        assert not monitor.is_alive()
    before = store.load_unit("asr-00000")
    first_unit_stamp = (store.directory / "units" / "asr-00000.json").stat().st_mtime_ns
    assert before and before["segments"]
    committed = recovery_info(document, results)
    print("interrupted", committed, flush=True)
    cancel.clear()
    messages = []
    run_local_pipeline(document, results, cancel, messages.append, language="auto", resume=True,
                       auto_summary=False, service_factory=BoundedService)
    assert store.load_unit("asr-00000") == before
    # Progress snapshots are advisory and can be overwritten between polls.
    # A durable unit's unchanged timestamp proves it was not recomputed/saved.
    assert (store.directory / "units" / "asr-00000.json").stat().st_mtime_ns == first_unit_stamp
    assert document["transcription_complete"] and document["language"] == "en"
    assert not store.audio_path.exists()
    assert all(0 <= row["start_ms"] < row["end_ms"] <= document["duration_ms"] for row in document["segments"])
    assert all(row["speaker"] in document["speakers"] for row in document["segments"])
    report["english_resume"] = {"saved_ms": committed["completed_ms"], "duration_ms": document["duration_ms"],
                                "segments": len(document["segments"]), "speakers": len(document["speakers"]),
                                "first_chunk_unchanged": True, "audio_released": True,
                                "sample": document["segments"][0]["text"][:140]}
    print("English resumed", report["english_resume"], flush=True)
    chinese = new_document(directory / "zh.wav")
    run_local_pipeline(chinese, results, cancel, lambda message: None, language="auto", speaker_count=1,
                       auto_summary=False, service_factory=BoundedService)
    assert chinese["language"] == "zh" and chinese["segments"] and chinese["transcription_complete"]
    report["chinese_transcription"] = {"segments": len(chinese["segments"]), "sample": chinese["segments"][0]["text"][:120]}
    report["seconds"] = round(time.monotonic() - started, 2)
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
