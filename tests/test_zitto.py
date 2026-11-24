"""Unit tests: no GPU, models or network. The ffmpeg round trip needs ffmpeg on PATH."""

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from zitto import audio, remover, source, worker
from zitto.engine import Engine
from zitto.models import DEFAULT, DRUMS, FULL, LEAD, STATED, WIND
from zitto.targets import UnknownTarget, plan, resolve


class FakeEngine:
    """Returns known stems and records what it was given."""

    def __init__(self, stems):
        self._stems = stems
        self.calls = []

    def is_downloaded(self, model):
        return True

    def stems(self, mix, model, names, workdir):
        self.calls.append((model, list(names), mix.copy()))
        return {name: self._stems[name](mix) for name in names}


def keys(targets):
    return [t.key for t in targets]


def test_resolve_aliases_in_catalogue_order():
    assert keys(resolve(["batteria", "voce"])) == ["voice", "drums"]


def test_resolve_accepts_commas_and_removes_duplicates():
    assert keys(resolve(["voice,vocals", "Drums"])) == ["voice", "drums"]


def test_voice_already_includes_the_lead_voice():
    assert keys(resolve(["lead-voice", "voice"])) == ["voice"]


@pytest.mark.parametrize("names", [["kazoo"], [""], [","]])
def test_resolve_rejects(names):
    with pytest.raises(UnknownTarget):
        resolve(names)


def test_plan_runs_each_model_once():
    steps = plan(resolve(["voice", "drums", "bass"]))
    assert [(model, keys(group)) for model, group in steps] == [(FULL, ["voice", "drums", "bass"])]


def test_subtract_leaves_the_rest_untouched(tmp_path):
    rng = np.random.default_rng(0)
    voice = rng.uniform(-0.6, 0.6, (1000, 2)).astype(np.float32)
    backing = rng.uniform(-0.4, 0.4, (1000, 2)).astype(np.float32)
    mix = voice + backing
    scale = remover.WORKING_PEAK / np.abs(mix).max()
    engine = FakeEngine({"voice": lambda m: voice * scale})
    rest = remover.subtract(mix, resolve(["voice"]), engine, tmp_path, lambda s: None)
    np.testing.assert_allclose(rest, backing, atol=1e-5)
    assert np.abs(engine.calls[0][2]).max() <= remover.WORKING_PEAK + 1e-6


def test_different_models_run_in_cascade(tmp_path):
    mix = np.full((100, 2), 0.4, dtype=np.float32)
    engine = FakeEngine({"lead": lambda m: m * 0.5, "drums": lambda m: m * 0.5})
    rest = remover.subtract(mix, resolve(["lead-voice", "drums"]), engine, tmp_path, lambda s: None)
    assert [c[0] for c in engine.calls] == [LEAD, FULL]
    np.testing.assert_allclose(engine.calls[1][2], 0.2, rtol=1e-5)
    np.testing.assert_allclose(rest, 0.1, rtol=1e-5)


@pytest.fixture
def separator(monkeypatch):
    """In place of audio-separator and torch: what they were given."""
    given = {"downloaded": [], "cuda": False}

    class Separator:
        def __init__(self, **options):
            given.update(options)

        def load_model(self, file):
            given["file"] = file

        def download_model_files(self, file):
            if file == given.get("broken"):
                raise RuntimeError("no network")
            given["downloaded"].append(file)

    torch, package, inner = ModuleType("torch"), ModuleType("audio_separator"), ModuleType("audio_separator.separator")
    torch.cuda = ModuleType("torch.cuda")
    torch.cuda.is_available = lambda: given["cuda"]
    inner.Separator = Separator
    for name, module in (("torch", torch), ("audio_separator", package), ("audio_separator.separator", inner)):
        monkeypatch.setitem(sys.modules, name, module)
    return given


@pytest.mark.parametrize("cuda", [True, False])
def test_reduced_precision_only_on_the_gpu(tmp_path, separator, cuda):
    # On a CPU that cannot compute it, the wind model does not finish.
    separator["cuda"] = cuda
    Engine(tmp_path)._load(FULL, tmp_path)
    assert separator["file"] == FULL.file and separator["use_autocast"] is cuda


def test_all_the_models_can_be_downloaded_before_they_are_needed(separator, capsys):
    assert worker.download() == worker.DONE
    assert separator["downloaded"] == [FULL.file, LEAD.file, WIND.file, DRUMS.file]
    assert f"Model 4 of 4: {DRUMS.name}" in capsys.readouterr().out
    assert None not in STATED.all() and len(STATED.all()) == 4 and len(DEFAULT.all()) == 4


def test_a_model_that_cannot_be_downloaded(separator, capsys):
    separator["broken"] = LEAD.file
    assert worker.download() != worker.DONE
    assert separator["downloaded"] == [FULL.file] and "no network" in capsys.readouterr().out


def test_where_everything_is_kept(tmp_path):
    code = ("from zitto import cli, engine, library, settings; "
            "print(engine.MODELS_DIR, library.ROOT, settings.PATH, cli.DEFAULT_OUT, sep='\\n')")

    def places(**added):
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZITTO_")} | added
        out = subprocess.run([sys.executable, "-P", "-c", code], env=env, capture_output=True, text=True, check=True)
        return [Path(line) for line in out.stdout.splitlines()]

    here, files = Path(audio.__file__).resolve().parent.parent, ("models", "library", "settings.json", "output")
    assert places() == [here / f for f in files]
    assert places(ZITTO_HOME=str(tmp_path)) == [tmp_path / f for f in files]
    assert places(ZITTO_HOME=str(tmp_path), ZITTO_LIBRARY=str(tmp_path / "l"), ZITTO_OUTPUT=str(tmp_path / "o")) == [
        tmp_path / f for f in ("models", "l", "settings.json", "o")]


def test_silence_does_not_reach_the_engine(tmp_path):
    engine = FakeEngine({})
    rest = remover.subtract(np.zeros((10, 2), np.float32), resolve(["voice"]), engine, tmp_path)
    assert not rest.any() and not engine.calls


def test_limit_only_acts_on_clipping():
    quiet = np.array([[0.5, -0.5]])
    assert remover.limit(quiet) is quiet
    assert np.abs(remover.limit(np.array([[2.0, -1.0]]))).max() == pytest.approx(0.999)


def test_unique_does_not_overwrite(tmp_path):
    first = tmp_path / "a.mp3"
    assert remover.unique(first) == first
    first.write_text("x")
    assert remover.unique(first).name == "a (2).mp3"


@pytest.mark.parametrize("text, expected", [
    ("https://songs.example/a", True), (" HTTP://x.com ", True), (r"C:\music\a.mp3", False), ("song.mp3", False),
])
def test_is_url(text, expected):
    assert source.is_url(text) is expected


@pytest.mark.parametrize("title, expected", [
    ('AC/DC: "Live"?', "AC_DC_ _Live__"), ("  a   b. ", "a b"), ("...", "track"), ("CON", "_CON"),
    ("Perché", "Perché"),
])
def test_safe_filename(title, expected):
    assert source.safe_filename(title) == expected


def test_missing_file(tmp_path):
    with pytest.raises(source.SourceError, match="not found"):
        source.get_track(str(tmp_path / "nope.mp3"), tmp_path)


def test_remove_end_to_end_with_fake_engine(tmp_path):
    t = np.arange(audio.RATE) / audio.RATE
    tone = np.stack([0.3 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1).astype(np.float32)
    song = tmp_path / "song.wav"
    audio.write_wav(song, tone)
    engine = FakeEngine({"voice": lambda m: m})
    result = remover.remove(str(song), resolve(["voice"]), tmp_path / "out", "flac",
                            save_removed=True, engine=engine, status=lambda s: None)
    assert result.output.name == "song (no voice).flac"
    assert np.abs(audio.read_wav(result.output)).max() < 1e-4
    assert np.abs(audio.read_wav(result.removed)).max() == pytest.approx(0.3, abs=1e-3)


def test_output_must_not_be_the_source(tmp_path):
    song = tmp_path / "song.wav"
    audio.write_wav(song, np.full((100, 2), 0.1, np.float32))
    before = song.read_bytes()
    with pytest.raises(source.SourceError, match="source file"):
        remover.remove(str(song), resolve(["voice"]), tmp_path, output=song,
                       engine=FakeEngine({}), status=lambda s: None)
    assert song.read_bytes() == before


# --- artist and title ----------------------------------------------------------------

def test_plain():
    assert source.plain("  Città   VUOTA é") == "citta vuota e"


def test_names_of_files(tmp_path):
    song = tmp_path / "some file.flac"
    tone = np.zeros((4410, 2), np.float32)
    audio.encode(tone, song, tmp_path)
    assert source.get_track(str(song), tmp_path).name == "some file"
    for kind in ("flac", "mp3", "m4a", "ogg", "wav"):
        tagged = tmp_path / f"tagged.{kind}"
        audio._ffmpeg("-i", str(song), "-metadata", "artist=Band", "-metadata", "title=Song", str(tagged))
        assert source.file_credits(tagged) == ("Band", "Song"), kind
    track = source.get_track(str(tmp_path / "tagged.flac"), tmp_path)
    assert (track.artist, track.title, track.name) == ("Band", "Song", "Band - Song")
    (tmp_path / "broken.mp3").write_bytes(b"not audio")
    assert source.file_credits(tmp_path / "broken.mp3") == ("", "broken")


def test_saved_file_carries_its_title(tmp_path):
    from tinytag import TinyTag
    out = tmp_path / "x.mp3"
    audio.encode(np.zeros((4410, 2), np.float32), out, tmp_path, title="Band - Song")
    assert TinyTag.get(out).title == "Band - Song"
