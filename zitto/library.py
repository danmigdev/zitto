"""The songs: what has been added, downloaded and separated, and in which playlist.

Everything is on disk, so closing the program loses nothing:

    playlists.json
    <key>/song.json      in the list: source, title...          (written by the window)
    <key>/download.json  the original audio, next to it         (written by whoever downloads)
    <key>/stems.json     separated, one .flac for each part     (written last, by the worker)
    <key>/error.json     why it failed                          (written by the worker)
    <key>/player.json    how it was left in the player          (written by the window)
"""

from __future__ import annotations

import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .audio import RATE
from .settings import HOME, read, write
from .source import (SourceError, Status, Track, digest, file_credits, full_name, get_track, is_url, link_source,
                     need_link_source)

ROOT = Path(os.environ.get("ZITTO_LIBRARY") or HOME / "library")

_last_order = 0.0


@dataclass(frozen=True)
class Song:
    key: str
    title: str  # as shown: "Artist - Title", or the name given by the user
    source: str
    ready: bool
    error: str | None
    single: bool  # added on its own, not only as part of a playlist
    order: float


@dataclass(frozen=True)
class SavedPlaylist:
    id: str
    title: str
    songs: tuple[str, ...]


@dataclass(frozen=True)
class StemSet:
    names: tuple[str, ...]
    data: list[np.ndarray]  # int16, (samples, 2)
    gain: float  # brings the parts back to the level of the original


# --- identity ------------------------------------------------------------------

def clean(source: str) -> str:
    """One spelling for each source: the plain form of a link, the full path of a file."""
    source = source.strip().strip('"')
    if is_url(source):
        opener = link_source(source)
        return opener.clean(source) if opener else source
    return str(Path(source).expanduser().resolve())


def key_for(source: str) -> str:
    """Identifies a song: path and content date for a file, what its plugin says for a link."""
    source = clean(source)
    if is_url(source):
        return need_link_source(source).key(source)
    path = Path(source)
    if not path.is_file():
        raise SourceError(f"File not found: {path}")
    info = path.stat()
    return digest(f"{path}|{info.st_size}|{info.st_mtime_ns}")


def folder(key: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(f"Not a song: {key}")
    return ROOT / key


# --- songs ---------------------------------------------------------------------

def get(key: str) -> Song | None:
    place = folder(key)
    song = read(place / "song.json")
    if not song:
        return None
    known = read(place / "download.json")
    title = song.get("name") or full_name(known.get("artist", ""), known.get("title", "")) or song.get("title")
    title = title or song["source"]
    return Song(key, title, song["source"], (place / "stems.json").is_file(),
                read(place / "error.json").get("reason"), bool(song.get("single")), song.get("order", 0.0))


def songs() -> list[Song]:
    """Every song in the list, in the order they were added."""
    if not ROOT.is_dir():
        return []
    keys = [p.name for p in ROOT.iterdir() if (p / "song.json").is_file()]
    return sorted((s for s in map(get, keys) if s), key=lambda s: s.order)


def add(source: str, title: str = "", single: bool = True) -> Song:
    """Put a song in the list. Adding it again keeps its work and retries it if it had failed."""
    key = key_for(source)
    source = clean(source)
    path = folder(key) / "song.json"
    known = read(path)
    global _last_order
    _last_order = max(time.time(), _last_order + 1e-5)  # the clock may not move between two songs
    write(path, {
        "source": source,
        "title": title or known.get("title") or (source if is_url(source) else full_name(*file_credits(Path(source)))),
        "name": known.get("name", ""),
        "order": known.get("order") or _last_order,
        "single": single or bool(known.get("single")),
        "wanted": known.get("wanted", 0),
    })
    retry(key)
    return get(key)


def rename(key: str, name: str) -> None:
    """Give a song another name; an empty one brings back its own."""
    path = folder(key) / "song.json"
    if song := read(path):
        write(path, song | {"name": " ".join(name.split())})


def want(key: str) -> None:
    """Prepare this song before the others."""
    path = folder(key) / "song.json"
    if song := read(path):
        write(path, song | {"wanted": time.time()})


def waiting() -> list[Song]:
    """Songs still to be prepared, the next one first."""
    todo = [s for s in songs() if not s.ready and not s.error]
    return sorted(todo, key=lambda s: (-read(folder(s.key) / "song.json").get("wanted", 0), s.order))


def fail(key: str, reason: str) -> None:
    write(folder(key) / "error.json", {"reason": reason})


def retry(key: str) -> None:
    (folder(key) / "error.json").unlink(missing_ok=True)


def delete(key: str) -> None:
    _write_playlists([SavedPlaylist(p.id, p.title, tuple(k for k in p.songs if k != key)) for p in playlists()])
    shutil.rmtree(folder(key), ignore_errors=True)


# --- playlists -----------------------------------------------------------------

def playlists() -> list[SavedPlaylist]:
    return [SavedPlaylist(p["id"], p["title"], tuple(p["songs"]))
            for p in read(ROOT / "playlists.json").get("playlists", [])]


def _write_playlists(items: list[SavedPlaylist]) -> None:
    write(ROOT / "playlists.json",
          {"playlists": [{"id": p.id, "title": p.title, "songs": list(p.songs)} for p in items]})


def add_playlist(link: str, title: str, found) -> SavedPlaylist:
    """Put a playlist and its songs in the list; adding it again takes in what is new."""
    list_id = digest(need_link_source(link).list_id(link))
    keys = dict.fromkeys(add_to_playlist(song, name) for name, song in found)
    new = SavedPlaylist(list_id, title, tuple(keys))
    known = playlists()
    if any(p.id == new.id for p in known):
        _write_playlists([new if p.id == new.id else p for p in known])
    else:
        _write_playlists([*known, new])
    return new


def add_to_playlist(link: str, title: str) -> str:
    key = key_for(link)
    # A song already there is left alone: in particular, one that failed is not tried again.
    return key if get(key) else add(link, title, single=False).key


def delete_playlist(list_id: str) -> list[str]:
    """Remove a playlist with the songs that are nowhere else. Returns the songs removed."""
    known = playlists()
    gone = next((p for p in known if p.id == list_id), None)
    if gone is None:
        return []
    others = [p for p in known if p.id != list_id]
    kept = {k for p in others for k in p.songs} | {s.key for s in songs() if s.single}
    _write_playlists(others)
    removed = [k for k in gone.songs if k not in kept]
    for key in removed:
        shutil.rmtree(folder(key), ignore_errors=True)
    return removed


# --- audio ---------------------------------------------------------------------

def fetch(source: str, workdir: Path, status: Status = print) -> Track:
    """The audio of a source. A link is downloaded only once."""
    if not is_url(source.strip().strip('"')):
        return get_track(source, workdir, status)
    place = folder(key_for(source))
    saved = read(place / "download.json")
    if saved.get("file") and (place / saved["file"]).is_file():
        track = Track(place / saved["file"], saved["title"], saved.get("artist", ""))
        status(f"Already downloaded: {track.name}")
        return track

    track = get_track(source, workdir, status)
    kept = place / f"original{track.path.suffix}"
    partial = place / f"original.{os.getpid()}.part"
    try:
        place.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(track.path, partial)
        os.replace(partial, kept)
        write(place / "download.json", {"title": track.title, "artist": track.artist, "file": kept.name})
    except OSError:
        partial.unlink(missing_ok=True)  # it could not be kept: go on with the temporary file
        return track
    return Track(kept, track.title, track.artist)


def save_stems(key: str, parts: dict[str, np.ndarray], gain: float) -> None:
    """Store the parts (float, peak below 1). `gain` is what brings them to their real level."""
    place = folder(key)
    place.mkdir(parents=True, exist_ok=True)

    def store(item):
        name, data = item
        sf.write(place / f"{name}.flac", np.round(data * 32767).astype(np.int16), RATE, subtype="PCM_16")

    with ThreadPoolExecutor() as pool:
        list(pool.map(store, parts.items()))
    write(place / "stems.json", {"names": list(parts), "gain": gain * 32768 / 32767})


def load_stems(key: str) -> StemSet:
    place = folder(key)
    info = read(place / "stems.json")
    if not info:
        raise SourceError("This song has not been separated yet.")

    def load(name):
        data, rate = sf.read(place / f"{name}.flac", dtype="int16", always_2d=True)
        if rate != RATE:
            raise SourceError(f"Unexpected sample rate in {name}.flac")
        return data

    with ThreadPoolExecutor() as pool:
        data = list(pool.map(load, info["names"]))
    if len({d.shape for d in data}) != 1:
        raise SourceError("The parts of this song do not match. Delete it and add it again.")
    return StemSet(tuple(info["names"]), data, float(info["gain"]))


def load_state(key: str) -> dict:
    return read(folder(key) / "player.json")


def save_state(key: str, state: dict) -> None:
    if (folder(key) / "song.json").is_file():  # it may have been deleted meanwhile
        write(folder(key) / "player.json", state)
