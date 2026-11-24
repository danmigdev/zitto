"""The download of one head of a model that has many. No torch, models or network."""

import io
import os
import pickle
import sys
import zipfile
from collections import OrderedDict
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from zitto import mega

RNG = np.random.default_rng(5)
WEIGHTS = {"layers.0.weight": RNG.normal(size=(30, 40)), "layers.0.bias": RNG.normal(size=(30,))} | {
    f"mask_estimators.{head}.weight": RNG.normal(size=(300, 400)) for head in range(5)}
WEIGHTS = {name: w.astype(np.float16) for name, w in WEIGHTS.items()}
URL = "https://models.example/all.ckpt"


class Storage:
    def __init__(self, record):
        self.record = record


@pytest.fixture
def checkpoint(monkeypatch):
    """Makes a file like those of torch.save, without torch."""
    torch, utils = ModuleType("torch"), ModuleType("torch._utils")
    torch.HalfStorage = type("HalfStorage", (), {"__module__": "torch"})

    def _rebuild_tensor_v2():
        raise AssertionError("it must not run")

    _rebuild_tensor_v2.__module__, _rebuild_tensor_v2.__qualname__ = "torch._utils", "_rebuild_tensor_v2"
    utils._rebuild_tensor_v2 = _rebuild_tensor_v2
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch._utils", utils)

    class Tensor:
        def __init__(self, record, shape, stride):
            self.given = (Storage(record), 0, shape, stride, False, OrderedDict())

        def __reduce__(self):
            return _rebuild_tensor_v2, self.given

    class Pickler(pickle.Pickler):
        def persistent_id(self, thing):
            if isinstance(thing, Storage):
                return "storage", torch.HalfStorage, thing.record, "cpu", 1

    def make(*others, turned=False):
        state, records = OrderedDict(), {}
        for n, (name, w) in enumerate(WEIGHTS.items()):
            stride = tuple(s // w.itemsize for s in w.strides)
            state[name] = Tensor(str(n), w.shape, stride[::-1] if turned else stride)
            records[str(n)] = w.tobytes()
        for other in others:
            state[f"other {len(state)}"] = other
        index = io.BytesIO()
        Pickler(index).dump(state)
        made = io.BytesIO()
        with zipfile.ZipFile(made, "w") as file:
            file.writestr("model/data.pkl", index.getvalue())
            for record in sorted(records):
                file.writestr(f"model/data/{record}", records[record])
        return made.getvalue()

    return make


@pytest.fixture
def site(monkeypatch):
    """A server with one file. What was asked to it is in `asked`."""
    here = SimpleNamespace(content=b"", asked=[], whole=False, broken_after=None)

    class Session:
        def mount(self, *args): pass

        def get(self, url, headers, timeout):
            first, last = map(int, headers["Range"].removeprefix("bytes=").split("-"))
            here.asked.append((first, last + 1))
            if here.broken_after is not None and len(here.asked) > here.broken_after:
                raise OSError("no network")
            piece = here.content if here.whole else here.content[first:last + 1]
            return SimpleNamespace(content=piece, status_code=200 if here.whole else 206, url=url,
                                   headers={"Content-Range": f"bytes {first}-{last}/{len(here.content)}"},
                                   raise_for_status=lambda: None)

    class Bar:
        def __init__(self, **options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def update(self, n): pass

    requests, tqdm = ModuleType("requests"), ModuleType("tqdm")
    requests.Session, tqdm.tqdm = Session, Bar
    requests.adapters = SimpleNamespace(HTTPAdapter=lambda max_retries: None, Retry=lambda **options: None)
    monkeypatch.setitem(sys.modules, "requests", requests)
    monkeypatch.setitem(sys.modules, "tqdm", tqdm)
    monkeypatch.setattr(mega, "URL", URL)
    monkeypatch.setattr(mega, "BLOCK", 4096)
    return here


def expected(head):
    return {"layers.0.weight": WEIGHTS["layers.0.weight"], "layers.0.bias": WEIGHTS["layers.0.bias"],
            "mask_estimators.0.weight": WEIGHTS[f"mask_estimators.{head}.weight"]}


def same(found, wanted):
    return set(found) == set(wanted) and all(np.array_equal(found[n], wanted[n]) for n in wanted)


@pytest.mark.parametrize("head", [0, 1, 4])
def test_only_one_head_and_what_all_share(checkpoint, head):
    assert same(mega.read(io.BytesIO(checkpoint()), head), expected(head))


@pytest.mark.parametrize("head", [0, 1, 4])
def test_the_rest_is_not_downloaded(checkpoint, site, head):
    site.content = checkpoint()
    assert same(mega.read(mega.Remote(URL), head), expected(head))
    needed = sum(w.nbytes for w in expected(head).values())
    assert sum(end - first for first, end in site.asked) < needed + 20000 < len(site.content) / 4


def test_what_is_not_a_tensor_is_refused_and_not_run(checkpoint, tmp_path):
    class Bad:
        def __reduce__(self):
            return os.mkdir, (str(tmp_path / "made"),)

    with pytest.raises(pickle.UnpicklingError, match="mkdir"):
        mega.read(io.BytesIO(checkpoint(Bad())), 1)
    assert not (tmp_path / "made").exists()


def test_tensors_stored_in_another_order_are_refused(checkpoint):
    with pytest.raises(pickle.UnpicklingError):
        mega.read(io.BytesIO(checkpoint(turned=True)), 1)


def test_the_download_is_checked_and_done_once(checkpoint, site, tmp_path, monkeypatch):
    site.content = checkpoint()
    path = tmp_path / "models" / "one.npz"
    with pytest.raises(OSError, match="not what was expected"):
        mega.download("acoustic-guitar", path)
    assert not path.exists()
    monkeypatch.setitem(mega.HASHES, "acoustic-guitar", mega.digest(expected(1)))
    mega.download("acoustic-guitar", path)
    with np.load(path) as saved:
        assert same({name: saved[name] for name in saved.files}, expected(1))
    assert [p.name for p in path.parent.iterdir()] == ["one.npz"]
    asked = len(site.asked)
    mega.download("acoustic-guitar", path)
    assert len(site.asked) == asked


def test_a_server_that_only_gives_the_whole_file(checkpoint, site, tmp_path):
    site.content, site.whole = checkpoint(), True
    with pytest.raises(OSError, match="part of a file"):
        mega.download("acoustic-guitar", tmp_path / "one.npz")
    assert len(site.asked) == 1


def test_a_connection_that_ends_in_the_middle(checkpoint, site, tmp_path):
    site.content, site.broken_after = checkpoint(), 5
    with pytest.raises(OSError, match="no network"):
        mega.download("acoustic-guitar", tmp_path / "one.npz")
    assert list(tmp_path.iterdir()) == []
