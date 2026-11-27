"""Plugins, the sets of models, and the program without any plugin."""

import numpy as np
import pytest

from zitto import cli, engine, gui, library, models, plugins, remover, settings, source, targets


class Entry:
    def __init__(self, name, made):
        self.name, self._made = name, made

    def load(self):
        if isinstance(self._made, Exception):
            raise self._made
        return lambda: self._made


def install(monkeypatch, *entries):
    monkeypatch.setattr(plugins, "entry_points", lambda group: entries)
    monkeypatch.setattr(plugins, "problems", [])


def test_plugins_are_created(monkeypatch):
    install(monkeypatch, Entry("one", "first"), Entry("two", "second"))
    assert plugins._load("zitto.links") == ["first", "second"] and plugins.problems == []


def test_a_broken_plugin_does_not_stop_the_others(monkeypatch):
    install(monkeypatch, Entry("bad", ImportError("no module named x")), Entry("good", "fine"))
    assert plugins._load("zitto.links") == ["fine"]
    assert plugins.problems == ["The plugin bad could not be loaded: no module named x"]


# --- without plugins ----------------------------------------------------------------

LINK = "https://songs.example/alpha"


def test_links_are_refused(tmp_path):
    for refused in (lambda: library.add(LINK), lambda: source.get_track(LINK, tmp_path),
                    lambda: source.read_playlist(LINK), lambda: library.add_playlist(LINK, "x", [])):
        with pytest.raises(source.SourceError, match="only files"):
            refused()
    assert source.kind(LINK) is None and library.songs() == []


def test_the_command_line_only_speaks_of_files(capsys):
    help_text = cli.build_parser().format_help()
    assert "link" not in help_text and "--playlist" not in help_text
    with pytest.raises(SystemExit):
        cli.main([LINK, "--playlist"])


def test_the_command_line_refuses_links(monkeypatch, capsys):
    monkeypatch.setattr(remover, "Engine", lambda **k: None)
    assert cli.main([LINK]) == 1
    assert "only files" in capsys.readouterr().err


@pytest.mark.usefixtures("links")
def test_the_command_line_offers_the_links_of_a_plugin():
    help_text = " ".join(cli.build_parser().format_help().split())
    assert "a link to songs.example" in help_text and "--playlist" in help_text


@pytest.mark.usefixtures("links")
def test_only_the_links_of_the_plugin_are_accepted():
    assert library.add(LINK).key == "song-alpha"
    with pytest.raises(source.SourceError, match="only files"):
        library.add("https://elsewhere.example/alpha")


def test_a_ready_song_stays_when_its_plugin_is_gone(monkeypatch, links):
    song = library.add(LINK, "Alpha")
    library.save_stems(song.key, {n: np.zeros((10, 2), np.float32) for n in models.parts()}, 1.0)
    monkeypatch.setattr(plugins, "links", lambda: ())
    assert [(s.title, s.ready) for s in library.songs()] == [("Alpha", True)]
    assert library.load_stems(song.key).names == models.parts()


# --- models --------------------------------------------------------------------------

@pytest.mark.parametrize("chosen", [models.DEFAULT, models.STATED])
def test_the_models_say_their_terms(chosen):
    for model in chosen.all():
        assert model.terms and model.page.startswith("https://")


def test_the_default_models_find_every_part():
    assert models.current() is models.DEFAULT and models.parts() == models.NAMES
    assert targets.available() == targets.TARGETS
    assert models.stems() == ("lead", "backing", "kick", "snare", "toms", "cymbals", "bass", "acoustic", "electric",
                              "piano", "wind", "other")


def test_models_with_stated_terms_can_be_asked_for(monkeypatch):
    monkeypatch.setattr(plugins, "model_set", lambda: models.DEFAULT)
    monkeypatch.setenv("ZITTO_MODELS", "Stated")  # wins over a plugin too
    assert models.current() is models.STATED
    assert not any(m.terms.startswith(("unknown", "not stated")) for m in models.STATED.all())


def test_only_the_parts_the_models_can_find_are_offered(monkeypatch):
    monkeypatch.setenv("ZITTO_MODELS", "stated")
    assert models.parts() == ("voice", "drums", "bass", "wind", "other")
    assert models.stems() == ("lead", "backing", "drums", "bass", "wind", "other")
    assert [t.key for t in targets.available()] == ["voice", "lead-voice", "drums", "bass", "wind", "other"]
    with pytest.raises(targets.UnknownTarget, match="Choose from: voice"):
        targets.resolve(["piano"])


def test_a_plugin_can_bring_other_models(monkeypatch):
    full = models.Model("Full", "full.ckpt", {"voice": "v", "drums": "d", "piano": "p"})
    other = models.ModelSet((full,), models.LEAD, models.WIND)
    monkeypatch.setattr(plugins, "model_set", lambda: other)
    assert models.parts() == ("voice", "drums", "piano", "wind", "other")
    assert models.stems() == ("lead", "backing", "drums", "piano", "wind", "other")
    assert targets.plan(targets.resolve(["voice", "piano"])) == [(full, targets.resolve(["voice", "piano"]))]


def test_the_user_can_do_without_some_models():
    settings.save(without=["lead"])
    assert models.parts() == ("voice", "drums", "bass", "guitar", "piano", "wind", "other")
    assert models.current().all() == (models.FULL, models.WIND, models.DRUMS, models.GUITAR)
    with pytest.raises(targets.UnknownTarget):
        targets.resolve(["lead-voice"])
    settings.save(without=["guitar"])
    assert "guitar" in models.stems() and "acoustic" not in models.stems() and "kick" in models.stems()
    settings.save(without=["lead", "wind", "drums", "guitar", "voice", "other"])  # not those that are needed
    assert models.parts() == models.stems() == ("voice", "drums", "bass", "guitar", "piano", "other")
    assert models.current().all() == (models.FULL,) and models.complete() is models.DEFAULT
    assert [t.key for t in targets.available()] == ["voice", "drums", "bass", "guitar", "piano", "other"]


@pytest.mark.parametrize("wrong", [None, 3, "lead", {"lead": 1}, [], ["nothing"], [["lead"]]])
def test_settings_that_make_no_sense_leave_nothing_out(wrong):
    settings.save(without=wrong)
    assert models.current() is models.DEFAULT


def test_long_songs_are_processed_in_pieces_without_seams():
    audio = np.random.default_rng(3).uniform(-1, 1, (1000, 2)).astype(np.float32)
    sizes = []

    def process(piece):
        sizes.append(len(piece))
        return np.stack([piece, piece * 0.5])

    for length in (90, 100, 101, 180, 181, 270, 1000):
        sizes.clear()
        whole, half = engine.in_pieces(audio[:length], process, 2, piece=100, fade=10)
        np.testing.assert_allclose(whole, audio[:length], atol=1e-6)
        np.testing.assert_allclose(half, audio[:length] * 0.5, atol=1e-6)
        assert max(sizes) <= 100


# --- window ----------------------------------------------------------------------------

def test_what_the_window_offers_to_open(window):
    assert not hasattr(window(), "entry")


@pytest.mark.usefixtures("links")
def test_the_window_offers_the_links_of_a_plugin(window):
    assert window().entry.winfo_exists()


def test_the_window_tells_about_a_broken_plugin(window, monkeypatch):
    monkeypatch.setattr(plugins, "problems", ["The plugin bad could not be loaded: x"])
    assert "plugin bad" in window().status.cget("text")


def test_the_window_refuses_links(window, monkeypatch):
    said = []
    app = window()
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, text: said.append(text))
    assert app.add(LINK) is False and "only files" in said[0]
