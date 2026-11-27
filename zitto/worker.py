"""Background process of the window: downloads and separates what is waiting in the library.

    python -m zitto.worker WORKDIR      prepare every waiting song, then end
    python -m zitto.worker --add LINK   put a playlist in the list
    python -m zitto.worker --models     have on the disk the models in use, and not those left out

Prints one JSON object per line. Being a process of its own, it can be stopped at any time,
the window never loads torch, and the GPU memory is free again when it ends.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import audio, library
from .models import LABELS, PIECES, STEMS, complete, current
from .source import SourceBlocked, SourceError, Status, read_playlist

DONE, BLOCKED, BUSY = 0, 2, 3  # exit codes
WORKING_PEAK = 0.5  # see remover.py
SILENT = 1e-4


def split(mix: np.ndarray, engine, workdir: Path, status: Status) -> dict[str, np.ndarray]:
    """All the parts of `mix`. They add up to it exactly."""
    def stems(audio_in, model, parts, text):
        if np.abs(audio_in).max() < SILENT:  # nothing to look for, e.g. no voice at all
            return {part: np.zeros_like(audio_in) for part in parts}
        if not engine.is_downloaded(model):
            status(f"Downloading a model (first time only): {model.name}")
        status(text)
        return engine.stems(audio_in, model, parts, workdir)

    models = current()
    found, rest = {}, mix
    for model in models.main:
        parts = [part for part in model.parts if part != "other"]
        text = "Separating the voice" if parts == ["voice"] else "Separating the instruments"
        got = stems(rest, model, parts, text)
        rest = rest - sum(got.values())
        found |= got
    if models.wind:
        wind = stems(rest, models.wind, ["wind"], "Separating the wind instruments")["wind"]
        found["wind"] = wind
        rest = rest - wind
    found["other"] = rest
    for part, (*pieces, last) in PIECES.items():
        model = models.opener(part)
        if model and part in found:
            rest = found.pop(part)
            what = "voices" if part == "voice" else LABELS[part].lower()
            got = stems(rest, model, pieces, f"Separating the {what}")
            found |= got | {last: rest - sum(got.values())}
    return {name: found[name] for name in STEMS if name in found}


def prepare(song: library.Song, engine, workdir: Path, status: Status) -> None:
    with tempfile.TemporaryDirectory(dir=workdir, ignore_cleanup_errors=True) as own:
        track = library.fetch(song.source, Path(own), status)
        status("Decoding")
        mix = audio.decode(track.path, Path(own))
    peak = float(np.abs(mix).max())
    if peak == 0:
        raise SourceError("The track is silent.")
    scale = min(1.0, WORKING_PEAK / peak)
    parts = split(mix * scale, engine, workdir, status)
    status("Saving")
    top = max(float(np.abs(p).max()) for p in parts.values())
    if top > 0.99:
        scale *= 0.99 / top
        parts = {name: p * (0.99 / top) for name, p in parts.items()}
    library.save_stems(song.key, parts, 1 / scale)


def emit(event: str, **data) -> None:
    print(json.dumps({"event": event, **data}))


def work(workdir: Path, engine=None) -> int:
    lock = _lock()
    if lock is None:
        return BUSY
    with lock:
        tried = set()
        while song := next((s for s in library.waiting() if s.key not in tried), None):
            tried.add(song.key)
            emit("song", key=song.key)
            try:
                if engine is None:
                    from .engine import Engine
                    engine = Engine()
                prepare(song, engine, workdir, lambda text: emit("status", text=text))
            except SourceBlocked as e:
                emit("blocked", text=str(e))
                return BLOCKED
            except Exception as e:  # one bad song must not cost the others
                library.fail(song.key, str(e) or type(e).__name__)
                emit("failed", key=song.key)
            else:
                emit("ready", key=song.key)
    return DONE


def _lock():
    """Only one worker at a time, whatever the number of windows."""
    library.ROOT.mkdir(parents=True, exist_ok=True)
    file = open(library.ROOT / "worker.lock", "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    if file.seek(0, 2) == 0:
        file.write(b"Held by the process that is preparing the songs.")
        file.flush()
    return file


def add(link: str) -> int:
    try:
        playlist = read_playlist(link, lambda text: None)
        saved = library.add_playlist(link, playlist.title, playlist.songs)
        emit("added", id=saved.id, title=saved.title, count=len(saved.songs))
        return DONE
    except Exception as e:
        emit("error", text=str(e) or type(e).__name__)
        return 1


def download() -> int:
    from .engine import Engine
    models, engine = current().all(), Engine()
    for model in complete().all():
        if model not in models:
            engine.remove(model)
    for n, model in enumerate(models, 1):
        emit("status", text=f"Model {n} of {len(models)}: {model.name}")
        try:
            engine.download(model)
        except Exception as e:
            emit("error", text=str(e) or type(e).__name__)
            return 1
    return DONE


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    if sys.argv[1] == "--add":
        return add(sys.argv[2])
    if sys.argv[1] == "--models":
        return download()
    return work(Path(sys.argv[1]))


if __name__ == "__main__":
    sys.exit(main())
