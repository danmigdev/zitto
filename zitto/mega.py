"""MVSep Mega 53 Stems: one network with 53 heads, each of which finds an instrument.

Only the heads that zitto uses are downloaded, with the part that they share: 78 MB for one, instead of 1369.
The checkpoint is a zip file whose content is not compressed, and the server gives any piece of it.
"""

from __future__ import annotations

import hashlib
import io
import os
import pickle
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

URL = ("https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/download/v1.0.21/"
       "mvsep_mega_model_bs_roformer_53_stems_v1.ckpt")
HEADS = {"acoustic-guitar": 1}  # the position of each head that is used
HASHES = {"acoustic-guitar": "b9bf120f77cbdf066750ed1c85a94d6020ce775c7fe73b4c6ceab758da034c9e"}  # of the weights, see `digest`
HOP = 512
BLOCK = 1 << 20
CONNECTIONS = 8
HEADER = 256  # more than what precedes a record, in the file


class Remote(io.RawIOBase):
    """A file of the web. Of it, only what is read is downloaded."""

    def __init__(self, url: str):
        import requests

        self._session, self._pieces, self._at = requests.Session(), {}, 0
        again = requests.adapters.Retry(total=5, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        self._session.mount("https://", requests.adapters.HTTPAdapter(max_retries=again))
        answer = self._get(url, 0, 1)
        self._url = answer.url  # after the redirections
        self._size = int(answer.headers["Content-Range"].rpartition("/")[2])

    def _get(self, url: str, first: int, end: int):
        answer = self._session.get(url, headers={"Range": f"bytes={first}-{end - 1}"}, timeout=60)
        answer.raise_for_status()
        if answer.status_code != 206 or len(answer.content) != end - first:
            raise OSError("The server does not give a part of a file.")
        return answer

    def fetch(self, ranges: list[tuple[int, int]]) -> None:
        """Download these pieces, several at a time."""
        from tqdm import tqdm

        ranges = [(first, min(end, self._size)) for first, end in ranges]
        pool = ThreadPoolExecutor(CONNECTIONS)
        try:
            with tqdm(total=sum(end - first for first, end in ranges), unit="B", unit_scale=True) as bar:
                for (first, end), answer in zip(ranges, pool.map(lambda r: self._get(self._url, *r), ranges)):
                    self._pieces[first] = answer.content
                    bar.update(end - first)
        finally:
            pool.shutdown(cancel_futures=True)  # after an error, the others would only fail too

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._at

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._at = offset + (0 if whence == io.SEEK_SET else self._at if whence == io.SEEK_CUR else self._size)
        return self._at

    def read(self, size: int = -1) -> bytes:
        end = self._size if size is None or size < 0 else min(self._at + size, self._size)
        found = []
        while self._at < end:
            first = max((first for first in self._pieces if first <= self._at), default=0)
            piece = self._pieces.get(first, b"")[self._at - first:][:end - self._at]
            if not piece:
                stop = min(max(end, self._at + BLOCK), self._size)
                self._pieces[self._at] = self._get(self._url, self._at, stop).content
            found.append(piece)
            self._at += len(piece)
        return b"".join(found)


class Index(pickle.Unpickler):
    """Where each tensor of a checkpoint is: (record, position, shape). Nothing of it is run."""

    def find_class(self, module, name):
        known = {("collections", "OrderedDict"): OrderedDict, ("torch", "HalfStorage"): np.float16,
                 ("torch._utils", "_rebuild_tensor_v2"): self._tensor}
        if (module, name) not in known:
            raise pickle.UnpicklingError(f"Not expected in this model: {module}.{name}")
        return known[module, name]

    def persistent_load(self, pid):
        return pid[2]  # ("storage", type, record, device, length)

    @staticmethod
    def _tensor(record, position, shape, stride, *rest):
        if stride != tuple(int(np.prod(shape[n + 1:])) for n in range(len(shape))):
            raise pickle.UnpicklingError("A tensor that is not stored in order")
        return record, position, shape


def read(file, head: int) -> dict[str, np.ndarray]:
    """The weights that one head needs, from the checkpoint with all of them."""
    with zipfile.ZipFile(file) as checkpoint:
        index = next(name for name in checkpoint.namelist() if name.endswith("/data.pkl"))
        tensors = Index(io.BytesIO(checkpoint.read(index))).load()
        own, wanted = f"mask_estimators.{head}.", {}
        for name, where in tensors.items():
            if name.startswith(own):
                wanted["mask_estimators.0." + name[len(own):]] = where
            elif not name.startswith("mask_estimators."):
                wanted[name] = where
        folder = index[:-len("data.pkl")] + "data/"
        if isinstance(file, Remote):  # each with what precedes it
            records = [checkpoint.getinfo(folder + record) for record in {record for record, *_ in wanted.values()}]
            file.fetch([(r.header_offset, r.header_offset + r.file_size + HEADER) for r in records])
        return {name: np.frombuffer(checkpoint.read(folder + record), np.float16, int(np.prod(shape)), position * 2)
                .reshape(shape) for name, (record, position, shape) in wanted.items()}


def digest(weights: dict[str, np.ndarray]) -> str:
    found = hashlib.sha256()
    for name in sorted(weights):
        found.update(f"{name} {weights[name].shape}".encode())
        found.update(np.ascontiguousarray(weights[name]).data)
    return found.hexdigest()


def download(name: str, path: Path) -> None:
    """The weights of a head, in a file."""
    if path.is_file():
        return
    weights = read(Remote(URL), HEADS[name])
    if digest(weights) != HASHES[name]:
        raise OSError("What was downloaded is not what was expected.")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    try:
        with open(partial, "wb") as file:
            np.savez(file, **weights)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def network(path: Path):
    """The network with one head, on the CPU."""
    import torch
    from audio_separator.separator.uvr_lib_v5.roformer.bs_roformer import BSRoformer

    model = BSRoformer(dim=256, depth=12, stereo=True, num_stems=1, time_transformer_depth=1,
                       freq_transformer_depth=1, mlp_expansion_factor=2, stft_hop_length=HOP)
    with np.load(path, allow_pickle=False) as weights:
        model.load_state_dict({name: torch.from_numpy(weights[name]).float() for name in weights.files})
    return model.eval()
