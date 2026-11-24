import gc
import tkinter as tk
from urllib.parse import parse_qs, urlsplit

import pytest

from zitto import gui, library, player, plugins, settings
from zitto.source import LIST, SONG, SONG_IN_LIST, LinkSource, Playlist


@pytest.fixture(autouse=True)
def separate_library(monkeypatch, tmp_path):
    """Tests never see or touch the user's songs and settings."""
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")


@pytest.fixture(autouse=True)
def no_plugins(monkeypatch):
    """Nor do they depend on the plugins that are installed."""
    monkeypatch.setattr(plugins, "links", lambda: ())
    monkeypatch.setattr(plugins, "model_set", lambda: None)
    monkeypatch.setattr(plugins, "problems", [])
    monkeypatch.delenv("ZITTO_MODELS", raising=False)


class Site(LinkSource):
    """A site that does not exist: songs.example/NAME, songs.example/list/NAME, songs.example/NAME?list=NAME."""

    label = "a link to songs.example"

    def accepts(self, link):
        return urlsplit(link).hostname == "songs.example"

    def clean(self, link):
        return f"https://songs.example{urlsplit(link).path}"

    def key(self, link):
        return "song-" + urlsplit(link).path.strip("/")

    def kind(self, link):
        parts = urlsplit(link)
        if parts.path.startswith("/list/"):
            return LIST
        return SONG_IN_LIST if "list" in parse_qs(parts.query) else SONG

    def read_list(self, link, status):
        return Playlist("Mine", (("Song a", "https://songs.example/a"), ("Song b", "https://songs.example/b")))


@pytest.fixture
def links(monkeypatch):
    """Installs a plugin that opens links."""
    monkeypatch.setattr(plugins, "links", lambda: (Site(),))


# --- window, not shown -------------------------------------------------------------------

class SilentOutput:
    latency = 0

    def __init__(self):
        self.mixer = None

    def close(self): pass


@pytest.fixture
def window(monkeypatch):
    """Starts the window on the library as it is at that moment."""
    monkeypatch.setattr(player, "Output", SilentOutput)
    monkeypatch.setattr(player.midi, "MidiInput", lambda f: (_ for _ in ()).throw(OSError("none here")))
    started = []
    monkeypatch.setattr(gui.jobs.Work, "start", lambda self: started.append(True))
    opened = []

    def start():
        try:
            root = tk.Tk()
        except tk.TclError:
            pytest.skip("no display")
        root.withdraw()
        app = gui.App(root)
        app.started = started
        opened.append(app)
        root.update()
        return app

    yield start
    for app in opened:
        app.close()
    # Now, and here: what is left of a window must not be collected by the thread that loads songs.
    opened.clear()
    app = None
    gc.collect()
