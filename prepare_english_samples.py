"""Make two bounded local samples from an explicitly supplied video."""
import argparse
from pathlib import Path
import subprocess
import os

import imageio_ffmpeg
from core import probe_media_duration, timestamp

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    duration = probe_media_duration(args.source) / 1000
    print("Full video passed preflight:", timestamp(round(duration * 1000)), flush=True)
    output = Path(__file__).resolve().parent / ".tmp" / "verification"
    output.mkdir(parents=True, exist_ok=True)
    for name, start, length in [("english-front", min(60, duration/4), 90),
                                ("english-tail", max(0, duration-60), 60)]:
        target = output / (name + ".wav")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(start), "-protocol_whitelist", "file,pipe", "-i", str(args.source.resolve()),
                        "-t", str(length), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
                       check=True, timeout=30, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        print(name, "source start", timestamp(round(start*1000)), flush=True)
