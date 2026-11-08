"""Small, valid PCM media used by pipeline tests without model inference."""
from pathlib import Path
import shutil
import wave


def make_source(directory, *, duration_ms=3000, name="source.wav", amplitude=0):
    path = Path(directory) / name
    with wave.open(str(path), "wb") as media:
        media.setnchannels(1)
        media.setsampwidth(2)
        media.setframerate(16000)
        media.writeframes(int(amplitude).to_bytes(2, "little", signed=True) * (duration_ms * 16))
    return path


def copy_audio(source, target, _cancel):
    shutil.copyfile(source, target)
    with wave.open(str(target), "rb") as media:
        return round(media.getnframes() * 1000 / media.getframerate())
