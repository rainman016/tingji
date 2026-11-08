"""Create a small, public/synthetic two-voice video for manual integration verification."""
import os
from pathlib import Path
import subprocess
import wave

import imageio_ffmpeg
import requests

ROOT = Path(__file__).resolve().parent
FOLDER = ROOT / ".tmp" / "verification"
FOLDER.mkdir(exist_ok=True)
flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def main():
    session = requests.Session()
    session.trust_env = False
    response = session.get("https://isv-data.oss-cn-hangzhou.aliyuncs.com/ics/MaaS/ASR/test_audio/asr_example_zh.wav", timeout=30)
    response.raise_for_status()
    (FOLDER / "public-chinese.wav").write_bytes(response.content)
    session.close()
    english = FOLDER / "synthetic-english.wav"
    # The only file path comes from this project; PowerShell literal quoting preserves it.
    quoted = str(english).replace("'", "''")
    code = ("Add-Type -AssemblyName System.Speech; "
            "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$voice.SelectVoice('Microsoft Zira Desktop'); "
            f"$voice.SetOutputToWaveFile('{quoted}'); "
            "$voice.Speak('Tomorrow we will test the local video transcription program. I will prepare three recordings.'); "
            "$voice.Dispose()")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", code], check=True, timeout=30, creationflags=flags)
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    for source, target in [(FOLDER / "public-chinese.wav", FOLDER / "cn-16k.wav"), (english, FOLDER / "en-16k.wav")]:
        subprocess.run([executable, "-nostdin", "-loglevel", "error", "-y", "-i", str(source),
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)], check=True, timeout=20, creationflags=flags)
    merged = FOLDER / "two-speakers.wav"
    with wave.open(str(merged), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        for name in ["cn-16k.wav", "en-16k.wav", "cn-16k.wav", "en-16k.wav"]:
            with wave.open(str(FOLDER / name), "rb") as source:
                output.writeframes(source.readframes(source.getnframes()))
            output.writeframes(b"\0" * 32000)
    subprocess.run([executable, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x2164bb:s=320x180:r=10",
                    "-i", str(merged), "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(FOLDER / "two-speakers.mp4")],
                   check=True, timeout=30, creationflags=flags)
    print("Public/synthetic verification video created.")


if __name__ == "__main__":
    main()
