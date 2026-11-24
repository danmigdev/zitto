# Writing a plugin

zitto opens files. A plugin is a Python package, installed next to it, that can add:

- **links**: another place to open songs from, for example your own server
- **models**: another set of separation models

The plugin is a separate project: zitto does not have to be changed. Whoever writes or installs
a plugin has to make sure that it respects the law, the terms of the site it opens, and the
rights on the recordings and on the models.

## The package

    my-plugin/
        pyproject.toml
        my_plugin.py

zitto finds plugins through entry points. Each names something to call without arguments: a
class, or a function. In `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "my-plugin"
version = "0.1.0"

[project.entry-points."zitto.links"]
my_server = "my_plugin:MyServer"

[tool.setuptools]
py-modules = ["my_plugin"]
```

Install it in the environment of zitto, with the libraries it needs, and start zitto again:

    .venv\Scripts\python.exe -m pip install -e C:\path\to\my-plugin

With at least one plugin for links, the window shows a field to paste them, and the command
line accepts them. If a plugin cannot be loaded, the window says why and goes on without it.

## Links

A class derived from `zitto.source.LinkSource`. This one is complete: it opens the audio files
of one server.

```python
"""Opens the recordings on my own server."""

import shutil
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from zitto.source import LinkSource, SourceError, Track

SERVER = "recordings.example.org"


class MyServer(LinkSource):
    label = "a link to my server"

    def accepts(self, link):
        return urlsplit(link).hostname == SERVER

    def clean(self, link):
        return urlsplit(link.strip())._replace(query="", fragment="").geturl()

    def get_track(self, link, workdir, status):
        name = PurePosixPath(unquote(urlsplit(link).path))
        status(f"Downloading: {name.stem}")
        path = Path(workdir) / f"download{name.suffix}"
        try:
            with urllib.request.urlopen(self.clean(link), timeout=30) as answer, open(path, "wb") as file:
                shutil.copyfileobj(answer, file)
        except (urllib.error.URLError, OSError) as e:
            raise SourceError(f"Download failed: {e}") from e
        return Track(path, title=name.stem, artist="")
```

| Method | What it does | If not written |
| --- | --- | --- |
| `label` | what can be pasted, as zitto says it: "Add *a link to my server*, or files" | "a link" |
| `accepts(link)` | whether this plugin opens the link. The first plugin that accepts it is used | required |
| `get_track(link, workdir, status)` | puts the audio in `workdir` and returns a `Track(path, title, artist)` | required |
| `clean(link)` | one spelling for all the links to the same song | the link |
| `key(link)` | identifies the song, and names its folder: only letters, digits, `-` and `_` | a digest of `clean(link)` |
| `kind(link)` | `SONG`, `LIST`, or `SONG_IN_LIST` when it could mean either: then zitto asks | `SONG` |
| `list_id(link)` | identifies a list, so that adding it again updates it | `clean(link)` |
| `read_list(link, status)` | the songs of a list: `Playlist(title, ((name, link), ...))` | refuses |

`SONG`, `LIST`, `SONG_IN_LIST`, `Playlist`, `Track` and the errors are in `zitto.source`.

Rules:

- `accepts`, `clean`, `key`, `kind` and `list_id` run in the window, for every link: they must
  only look at the text. No network, no slow imports
- `get_track` and `read_list` run in a background process, that can be stopped at any time.
  Import what they need inside them
- `get_track` can return any file that ffmpeg reads. zitto copies it into its library and does
  not ask for it again, so leave it in `workdir`
- The `key` of a song must never change: it is how zitto knows that it has it already
- Raise `SourceError` with a sentence for the user when a song cannot be had. It is shown in
  red, and the next one goes on
- Raise `SourceBlocked` when the site is refusing everything, for example because of too many
  requests. zitto stops, and tries again at the next start
- Call `status(text)` at each step that takes time. A progress bar of tqdm on stderr becomes
  the percentage in the window

## Models

The entry point is in the group `zitto.models`, and gives a `zitto.models.ModelSet`:

```python
from zitto.models import LEAD, WIND, Model, ModelSet

FULL = Model("My model", "my_model.ckpt",
             {"voice": "vocals", "drums": "drums", "bass": "bass", "piano": "piano"},
             terms="...", page="https://...")


def my_models():
    return ModelSet(main=(FULL,), lead=LEAD, wind=WIND)
```

- `drums`, if it is given, finds `kick`, `snare` and `toms` inside the drums. The cymbals are the rest
- `main` runs in order on the song, each model on what the others left. Together they must find
  at least `voice`
- `lead` finds the lead voice inside the voice, `wind` the wind instruments in what is left. Both can
  be left out: without `lead`, the voice is one part
- The dictionary says what each part is called inside the model. The parts are `voice`, `lead`,
  `drums`, `bass`, `guitar`, `piano`, `wind`, `other`; the window shows those that are found
- The file is one that [audio-separator](https://github.com/nomadkaraoke/python-audio-separator)
  can run. Read the terms of its weights: the licence of the code often does not cover them

Songs already separated keep the parts they have.

## Testing

Replace what is installed, so that the test does not depend on it:

```python
from zitto import library, plugins
from my_plugin import MyServer


def test_the_same_song(monkeypatch, tmp_path):
    monkeypatch.setattr(library, "ROOT", tmp_path)
    monkeypatch.setattr(plugins, "links", lambda: (MyServer(),))
    link = "https://recordings.example.org/take%203.flac"
    assert library.add(link).key == library.add(link + "?download=1").key
```
