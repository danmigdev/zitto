"""Songs, playlists, downloads and the worker. No GPU, models or network."""

import contextlib
import io
import json
import os

import numpy as np
import pytest

from zitto import audio, library, models, remover, settings, source, worker
from zitto.mixer import FADE, Mixer, mixdown
from zitto.models import BAND, DRUMS, FULL, GUITAR, LEAD, STATED_LEAD, VOICE, WIND
from zitto.targets import resolve
from test_zitto import FakeEngine
from test_player import render

LINK = "https://songs.example/alpha"
OTHER = "https://songs.example/beta"
LIST = "https://songs.example/list/one"
SHARES = {"voice": 0.4, "drums": 0.2, "bass": 0.1, "guitar": 0.05, "piano": 0.05, "lead": 0.75, "wind": 0.5,
          "kick": 0.4, "snare": 0.3, "toms": 0.1, "acoustic": 0.6}

pytestmark = pytest.mark.usefixtures("links")


def engine():
    return FakeEngine({name: (lambda m, s=s: m * s) for name, s in SHARES.items()})


@pytest.fixture
def song_file(tmp_path):
    mix = np.random.default_rng(1).uniform(-0.9, 0.9, (5000, 2)).astype(np.float32)
    path = tmp_path / "song.wav"
    audio.write_wav(path, mix)
    return path, mix


@pytest.fixture
def downloads(monkeypatch, song_file):
    """Counts the downloads; every link gives the same audio."""
    calls = []

    def get_track(link, workdir, status):
        calls.append(link)
        path = workdir / "download.wav"
        path.write_bytes(song_file[0].read_bytes())
        return source.Track(path, "Song: title")

    monkeypatch.setattr(library, "get_track", get_track)
    return calls


def run_worker(tmp_path, fake=None):
    """Runs the worker in this process; returns its events and exit code."""
    out = io.StringIO()
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    with contextlib.redirect_stdout(out):
        code = worker.work(work, fake or engine())
    return [json.loads(line) for line in out.getvalue().splitlines()], code


# --- identity --------------------------------------------------------------------

@pytest.mark.parametrize("link", [
    LINK, "https://songs.example/alpha?list=one", "  https://SONGS.example/alpha  ",
])
def test_every_form_of_a_link_is_the_same_song(link):
    assert library.key_for(link) == "song-alpha" and library.clean(link) == LINK


def test_a_plugin_cannot_reach_other_folders():
    with pytest.raises(ValueError):
        library.add("https://songs.example/../../evil")


@pytest.mark.parametrize("key", ["..", "a/b", "a\\b", "", "C:"])
def test_only_song_folders_can_be_reached(key):
    with pytest.raises(ValueError):
        library.folder(key)


def test_a_changed_file_is_another_song(song_file):
    before = library.key_for(str(song_file[0]))
    audio.write_wav(song_file[0], np.full((200, 2), 0.1, np.float32))
    assert library.key_for(str(song_file[0])) != before


def test_missing_file():
    with pytest.raises(source.SourceError, match="not found"):
        library.add("nope.mp3")


# --- downloads ---------------------------------------------------------------------

def test_a_link_is_downloaded_once(downloads, tmp_path):
    first = library.fetch(LINK, tmp_path, lambda s: None)
    said = []
    again = library.fetch(LINK + "?list=one", tmp_path, said.append)
    assert again == first and first.path.parent.name == "song-alpha" and first.title == "Song: title"
    assert downloads == [LINK] and "Already downloaded" in said[0]


def test_downloaded_again_when_the_file_is_gone(downloads, tmp_path):
    library.fetch(LINK, tmp_path, lambda s: None).path.unlink()
    library.fetch(LINK, tmp_path, lambda s: None)
    assert len(downloads) == 2


def test_a_download_that_cannot_be_kept_is_still_used(downloads, tmp_path, monkeypatch):
    def full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(library.shutil, "copyfile", full)
    assert library.fetch(LINK, tmp_path, lambda s: None).path == tmp_path / "download.wav"


def test_removing_twice_from_the_command_line_downloads_once(downloads, tmp_path):
    for what in ("voice", "drums"):
        remover.remove(LINK, resolve([what]), tmp_path / "out", "flac", engine=engine(), status=lambda s: None)
    assert len(downloads) == 1 and library.songs() == []  # and does not fill the list of the window


def test_the_artist_is_kept_with_the_download(tmp_path, monkeypatch):
    path = tmp_path / "x.wav"
    path.write_bytes(b"x")
    monkeypatch.setattr(library, "get_track", lambda *a: source.Track(path, "Dreams", "Alfa"))
    song = library.add(LINK)
    assert song.title == LINK  # nothing is known yet
    assert library.fetch(LINK, tmp_path, lambda s: None).name == "Alfa - Dreams"
    assert library.get(song.key).title == "Alfa - Dreams"
    assert library.fetch(LINK, tmp_path, lambda s: None).artist == "Alfa"


def test_a_given_name_wins_and_survives_adding_again():
    song = library.add(LINK, "Dreams")
    library.rename(song.key, " Alfa  - Dreams ")
    assert library.get(song.key).title == "Alfa - Dreams"
    assert library.add(LINK).title == "Alfa - Dreams"
    library.rename(song.key, "")
    assert library.get(song.key).title == "Dreams"


# --- separation ----------------------------------------------------------------------

def test_the_parts_add_up_to_the_original(song_file, tmp_path):
    path, mix = song_file
    song = library.add(str(path))
    assert song.title == "song" and not song.ready and song.single
    events, code = run_worker(tmp_path)
    assert code == worker.DONE and [e["event"] for e in events if e["event"] != "status"] == ["song", "ready"]
    assert library.get(song.key).ready and library.waiting() == []

    stems = library.load_stems(song.key)
    assert stems.names == models.stems()
    mixer = Mixer(stems.data, stems.gain)
    mixer.play()
    np.testing.assert_allclose(render(mixer, 5000)[FADE:], mix[FADE:], atol=4e-4)
    levels = dict.fromkeys(stems.names, 0.0) | {"lead": 1.0}
    lead = mixdown(stems.data, stems.gain, list(levels.values()))
    np.testing.assert_allclose(lead, mix * 0.4 * 0.75, atol=1e-4)


def test_voices_come_from_the_voice_and_wind_from_what_is_left(tmp_path):
    mix = np.full((100, 2), 0.4, np.float32)
    fake = engine()
    parts = worker.split(mix, fake, tmp_path, lambda s: None)
    assert [c[0] for c in fake.calls] == [FULL, LEAD, WIND, DRUMS, GUITAR] and tuple(parts) == models.stems()
    np.testing.assert_allclose(fake.calls[1][2], mix * 0.4, rtol=1e-5)
    np.testing.assert_allclose(fake.calls[2][2], mix * 0.2, rtol=1e-5)
    np.testing.assert_allclose(fake.calls[3][2], mix * 0.2, rtol=1e-5)  # the drums
    np.testing.assert_allclose(parts["cymbals"], mix * 0.2 * 0.2, rtol=1e-5)  # what the other pieces leave
    np.testing.assert_allclose(fake.calls[4][2], mix * 0.05, rtol=1e-5)  # the guitar
    np.testing.assert_allclose(parts["electric"], mix * 0.05 * 0.4, rtol=1e-4)
    np.testing.assert_allclose(sum(parts.values()), mix, rtol=1e-5)


def test_several_models_work_each_on_what_the_others_left(tmp_path, monkeypatch):
    monkeypatch.setenv("ZITTO_MODELS", "stated")
    mix = np.full((100, 2), 0.4, np.float32)
    fake = engine()
    parts = worker.split(mix, fake, tmp_path, lambda s: None)
    assert [c[0] for c in fake.calls] == [VOICE, BAND, STATED_LEAD, WIND] and "drums" in parts
    np.testing.assert_allclose(fake.calls[1][2], mix * 0.6, rtol=1e-5)  # without the voice
    np.testing.assert_allclose(fake.calls[2][2], mix * 0.4, rtol=1e-5)
    np.testing.assert_allclose(fake.calls[3][2], mix * 0.6 * 0.7, rtol=1e-5)
    np.testing.assert_allclose(sum(parts.values()), mix, rtol=1e-5)


@pytest.mark.parametrize("without", [["lead"], ["wind"], ["drums"], ["guitar"], ["lead", "wind"],
                                     ["lead", "wind", "drums", "guitar"]])
def test_a_song_separated_without_some_models(song_file, tmp_path, without):
    settings.save(without=without)
    path, mix = song_file
    song, fake = library.add(str(path)), engine()
    run_worker(tmp_path, fake)
    left_out = {{"lead": LEAD, "wind": WIND, "drums": DRUMS, "guitar": GUITAR}[name] for name in without}
    assert [c[0] for c in fake.calls] == [m for m in (FULL, LEAD, WIND, DRUMS, GUITAR) if m not in left_out]
    stems = library.load_stems(song.key)
    assert stems.names == models.stems() and ("voice" in stems.names) == ("lead" in without)
    np.testing.assert_allclose(mixdown(stems.data, stems.gain, [1.0] * len(stems.data)), mix, atol=4e-4)


def test_models_do_not_run_on_silence(tmp_path):
    fake = FakeEngine({n: (lambda m: m * 0) for n in SHARES} | {"drums": lambda m: m})
    said = []
    parts = worker.split(np.full((100, 2), 0.4, np.float32), fake, tmp_path, said.append)
    assert [c[0] for c in fake.calls] == [FULL, DRUMS] and not parts["lead"].any()
    assert said == ["Separating the instruments", "Separating the drums"]


def test_loud_parts_do_not_clip(song_file, tmp_path):
    path, mix = song_file
    loud = engine()
    loud._stems["voice"] = lambda m: m * 3  # parts can be louder than the mix
    loud._stems["drums"] = lambda m: m * -2
    song = library.add(str(path))
    run_worker(tmp_path, loud)
    stems = library.load_stems(song.key)
    assert max(np.abs(d.astype(int)).max() for d in stems.data) < 32767
    np.testing.assert_allclose(mixdown(stems.data, stems.gain, [1.0] * len(stems.data)), mix, atol=2e-3)


# --- worker ------------------------------------------------------------------------

def test_a_failed_song_does_not_stop_the_others_and_is_not_repeated(downloads, song_file, tmp_path, monkeypatch):
    bad = library.add(OTHER)
    good = library.add(str(song_file[0]))
    real = library.get_track

    def get_track(link, *args):
        if link == OTHER:
            raise source.SourceError("Not available")
        return real(link, *args)

    monkeypatch.setattr(library, "get_track", get_track)
    events, _ = run_worker(tmp_path)
    assert [(e["event"], e["key"]) for e in events if "key" in e and e["event"] != "song"] == [
        ("failed", bad.key), ("ready", good.key)]
    assert library.get(bad.key).error == "Not available" and library.waiting() == []
    assert run_worker(tmp_path)[0] == []
    library.retry(bad.key)
    assert [s.key for s in library.waiting()] == [bad.key]


def test_adding_a_failed_song_again_retries_it():
    song = library.add(LINK)
    library.fail(song.key, "x")
    assert library.add(LINK).error is None


def test_when_a_site_refuses_the_songs_stay_waiting(tmp_path, monkeypatch):
    library.add(LINK)
    library.add(OTHER)

    def blocked(*args):
        raise source.SourceBlocked("too many requests")

    monkeypatch.setattr(library, "get_track", blocked)
    events, code = run_worker(tmp_path)
    assert code == worker.BLOCKED and len(library.waiting()) == 2
    assert [e["event"] for e in events] == ["song", "blocked"]


def test_only_one_worker_at_a_time(tmp_path):
    lock = worker._lock()
    try:
        assert run_worker(tmp_path) == ([], worker.BUSY)
    finally:
        lock.close()
    assert run_worker(tmp_path) == ([], worker.DONE)
    assert (library.ROOT / "worker.lock").stat().st_size > 0


def test_an_interrupted_separation_is_done_again(song_file, tmp_path, monkeypatch):
    song = library.add(str(song_file[0]))
    real = settings.write

    def interrupted(path, data):
        if path.name == "stems.json":
            raise KeyboardInterrupt
        real(path, data)

    monkeypatch.setattr(library, "write", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_worker(tmp_path)
    assert not library.get(song.key).ready and [s.key for s in library.waiting()] == [song.key]
    monkeypatch.setattr(library, "write", real)
    run_worker(tmp_path)
    assert library.get(song.key).ready


# --- list --------------------------------------------------------------------------

def test_songs_keep_the_order_and_the_wanted_one_goes_first():
    keys = [library.add(f"https://songs.example/{c}").key for c in "zyxab"]
    assert [s.key for s in library.songs()] == keys == [s.key for s in library.waiting()]
    library.want(keys[3])
    assert [s.key for s in library.waiting()] == [keys[3], *keys[:3], keys[4]]


def entries(*letters):
    return [(f"Song {c}", f"https://songs.example/{c}") for c in letters]


def test_playlist_is_registered_in_order_and_updated():
    saved = library.add_playlist(LIST, "Mine", entries("c", "a", "a"))
    assert [library.get(k).title for k in saved.songs] == ["Song c", "Song a"]
    assert not library.get(saved.songs[0]).single
    again = library.add_playlist(LIST + "?from=x", "Mine, renamed", entries("c", "b", "a"))
    assert again.id == saved.id and [p.title for p in library.playlists()] == ["Mine, renamed"]
    assert len(library.songs()) == 3


def test_updating_a_playlist_does_not_retry_what_failed():
    saved = library.add_playlist(LIST, "Mine", entries("a"))
    library.fail(saved.songs[0], "Not available")
    library.add_playlist(LIST, "Mine", entries("a", "b"))
    assert library.get(saved.songs[0]).error == "Not available" and len(library.waiting()) == 1


def test_deleting_a_playlist_keeps_songs_that_are_elsewhere():
    first = library.add_playlist(LIST, "One", entries("a", "b", "c"))
    library.add_playlist("https://songs.example/list/two", "Two", entries("b"))
    alone = library.add(entries("c")[0][1])  # also added on its own
    assert library.delete_playlist(first.id) == [first.songs[0]]
    assert {s.title for s in library.songs()} == {"Song b", "Song c"} and library.get(alone.key)
    assert [p.title for p in library.playlists()] == ["Two"]


def test_deleting_a_song(song_file, tmp_path):
    saved = library.add_playlist(LIST, "One", entries("a", "b"))
    library.delete(saved.songs[0])
    assert library.playlists()[0].songs == saved.songs[1:] and library.get(saved.songs[0]) is None
    own = library.add(str(song_file[0]))
    library.delete(own.key)
    assert song_file[0].exists()  # the user's file is never touched


def test_player_state_is_kept_unless_the_song_was_deleted():
    song = library.add(LINK)
    library.save_state(song.key, {"loop": [1, 2]})
    assert library.load_state(song.key) == {"loop": [1, 2]}
    library.delete(song.key)
    library.save_state(song.key, {"loop": [1, 2]})
    assert not library.folder(song.key).exists()


def test_add_command_reports_what_it_did(monkeypatch, capsys):
    monkeypatch.setattr(worker, "read_playlist", lambda link, status: source.Playlist("Mine", tuple(entries("a", "b"))))
    assert worker.add(LIST) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 2

    def broken(link, status):
        raise source.SourceError("The playlist does not exist")

    monkeypatch.setattr(worker, "read_playlist", broken)
    assert worker.add(LIST) == 1
    assert json.loads(capsys.readouterr().out) == {"event": "error", "text": "The playlist does not exist"}


# --- settings ------------------------------------------------------------------------

def test_settings(tmp_path):
    assert settings.load() == {}
    settings.save(midi_port="Pad 0")
    settings.save(midi={"play": ["note", 0, 1]})
    assert settings.load() == {"midi_port": "Pad 0", "midi": {"play": ["note", 0, 1]}}
    settings.PATH.write_text("{broken", encoding="utf-8")
    assert settings.load() == {}


def test_a_failed_write_leaves_the_old_content(tmp_path, monkeypatch):
    path = tmp_path / "a.json"
    settings.write(path, {"a": 1})

    def denied(*args):
        raise PermissionError

    monkeypatch.setattr(os, "replace", denied)
    monkeypatch.setattr(settings.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        settings.write(path, {"a": 2})
    assert settings.read(path) == {"a": 1} and [p.name for p in tmp_path.glob("a.json*")] == ["a.json"]
