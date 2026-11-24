"""Decoding and encoding through ffmpeg. Audio is float32, shape (samples, 2), 44.1 kHz."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 44100
ENCODERS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s24le"],
}


class AudioError(RuntimeError):
    pass


def _ffmpeg(*args: str) -> None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        run = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=flags)
    except FileNotFoundError as e:
        how = "brew install ffmpeg" if sys.platform == "darwin" else "winget install Gyan.FFmpeg"
        raise AudioError(f"ffmpeg was not found. Install it with: {how}") from e
    if run.returncode:
        raise AudioError(f"ffmpeg failed: {run.stderr.strip()[-500:]}")


def decode(path: Path, workdir: Path) -> np.ndarray:
    wav = workdir / "decoded.wav"
    _ffmpeg("-i", str(path), "-map", "0:a:0", "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_f32le", str(wav))
    audio = read_wav(wav)
    if len(audio) == 0:
        raise AudioError(f"No audio in {path.name}")
    return audio


def read_wav(path: Path) -> np.ndarray:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != RATE:
        raise AudioError(f"Unexpected sample rate {rate} in {path.name}")
    return np.repeat(audio, 2, axis=1) if audio.shape[1] == 1 else audio


def write_wav(path: Path, audio: np.ndarray) -> None:
    sf.write(path, audio, RATE, subtype="FLOAT")


def encode(audio: np.ndarray, out: Path, workdir: Path, tags_from: Path | None = None, title: str = "") -> None:
    """Write audio to mp3/flac/wav, copying title and artist tags when available.

    The file appears under its final name only when complete.
    """
    fmt = out.suffix.lower().lstrip(".")
    if fmt not in ENCODERS:
        raise AudioError(f"Unsupported output format '.{fmt}'. Use: {', '.join(ENCODERS)}")
    wav = workdir / "encode.wav"
    write_wav(wav, audio)
    tags = ["-i", str(tags_from), "-map", "0:a", "-map_metadata", "1"] if tags_from else []
    if title:
        tags += ["-metadata", f"title={title}"]
    partial = out.with_name(f"{out.stem}.part{out.suffix}")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg("-i", str(wav), *tags, *ENCODERS[fmt], str(partial))
        os.replace(partial, out)
    except OSError as e:
        raise AudioError(f"Cannot write {out}: {e.strerror or e}") from e
    finally:
        partial.unlink(missing_ok=True)
