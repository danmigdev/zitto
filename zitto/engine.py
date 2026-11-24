"""Runs the models and returns what they find as arrays. Most run through audio-separator."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .audio import RATE, read_wav, write_wav
from .models import Model
from .settings import HOME

MODELS_DIR = HOME / "models"
# What torch cannot do on the GPU of a Mac is done on the CPU, instead of failing. Read when it is imported.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
OPEN_UNMIX_TARGETS = ("vocals", "drums", "bass", "other")
PIECE, FADE = 60 * RATE, 2 * RATE


class EngineError(RuntimeError):
    pass


class Engine:
    def __init__(self, models_dir: Path = MODELS_DIR, verbose: bool = False):
        self._models_dir = models_dir
        self._level = logging.INFO if verbose else logging.ERROR
        self._loaded: dict[tuple[Model, Path], Any] = {}

    def is_downloaded(self, model: Model) -> bool:
        if model.backend == "openunmix":
            place = self._models_dir / "checkpoints"
            return all(any(place.glob(f"{target}-*.pth")) for target in OPEN_UNMIX_TARGETS)
        return (self._models_dir / model.file).is_file()

    def download(self, model: Model) -> None:
        """Download a model now, instead of the first time it is needed. Does nothing if it is there."""
        if model.backend == "openunmix":
            self._open_unmix_network(model)
        else:
            from audio_separator.separator import Separator

            Separator(log_level=self._level, model_file_dir=str(self._models_dir)).download_model_files(model.file)

    def remove(self, model: Model) -> None:
        """Delete a model from the disk. It is downloaded again when it is needed."""
        (self._models_dir / model.file).unlink(missing_ok=True)

    def _load(self, model: Model, workdir: Path):
        """A model on the GPU. It stays in memory as long as the same folder is used."""
        import torch
        from audio_separator.separator import Separator

        if (model, workdir) in self._loaded:
            separator = self._loaded[model, workdir]
            separator.model_instance.model_run.to(separator.torch_device)
            return separator
        self._loaded = {k: v for k, v in self._loaded.items() if k[1] == workdir}
        # Float WAV in and soundfile out keeps stems in 32-bit float. With the
        # threshold at 1.0 and the headroom left by the caller, the engine never
        # rescales a stem, which would make it unusable for subtraction.
        separator = Separator(
            log_level=self._level,
            model_file_dir=str(self._models_dir),
            output_dir=str(workdir),
            output_format="WAV",
            use_soundfile=True,
            normalization_threshold=1.0,
            # Reduced precision: nearly twice as fast on an NVIDIA GPU, the difference is below -66 dB.
            # Not on the CPU, where it is a format that many cannot compute, and takes far longer.
            use_autocast=torch.cuda.is_available(),
            mdxc_params={"segment_size": 256, "override_model_segment_size": False, "batch_size": None,
                         "overlap": model.overlap, "pitch_shift": 0},
            # Not the 256 of these models: with their own size they would run on onnxruntime, on the CPU.
            mdx_params={"hop_length": 1024, "segment_size": 512, "overlap": 0.25, "batch_size": 1,
                        "enable_denoise": False},
        )
        separator.load_model(model.file)
        self._loaded[model, workdir] = separator
        return separator

    def _park(self, model: Model, workdir: Path) -> None:
        """Move a model to RAM. With several of them on the GPU Windows starts moving graphics
        memory around, and everything becomes three times slower."""
        import torch

        loaded = self._loaded.get((model, workdir))
        if model.backend == "openunmix":
            network = loaded
        else:
            network = getattr(getattr(loaded, "model_instance", None), "model_run", None)
        if isinstance(network, torch.nn.Module):
            network.to("cpu")
        else:
            self._loaded.pop((model, workdir), None)
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif torch.backends.mps.is_available():
            torch.mps.empty_cache()

    def stems(self, audio: np.ndarray, model: Model, parts: list[str], workdir: Path) -> dict[str, np.ndarray]:
        """Separate `audio` and return the parts wanted, at the same scale as the input."""
        try:
            if model.backend == "openunmix":
                return self._open_unmix(audio, model, parts, workdir)
            return self._separator(audio, model, parts, workdir)
        finally:
            self._park(model, workdir)

    def _separator(self, audio: np.ndarray, model: Model, parts: list[str], workdir: Path) -> dict[str, np.ndarray]:
        mix = workdir / "mix.wav"
        write_wav(mix, audio)
        try:
            separator = self._load(model, workdir)
            separator.separate(str(mix), {model.parts[part]: f"stem_{part}" for part in parts})
        except SystemExit as e:  # the library exits the process on a bad model file
            raise EngineError(
                f"Could not load {model.file}. Delete it from {self._models_dir} and try again."
            ) from e

        result = {}
        for part in parts:
            path = workdir / f"stem_{part}.wav"
            if not path.is_file():
                raise EngineError(f"{model.name} did not produce the stem '{part}'")
            stem = read_wav(path)
            path.unlink()  # a later model must not pick up this file
            if np.abs(stem).max(initial=0.0) >= 0.9999:
                raise EngineError(f"Stem '{part}' was rescaled by the engine; cannot subtract it safely")
            result[part] = _fit(stem, len(audio))
        return result

    def _open_unmix_network(self, model: Model):
        try:
            import openunmix
            import torch

            torch.hub.set_dir(str(self._models_dir))  # where the weights are downloaded
            # All its targets, also when fewer are wanted: they are estimated together.
            return getattr(openunmix, model.file)(targets=list(OPEN_UNMIX_TARGETS), device="cpu")
        except Exception as e:
            raise EngineError(f"Could not load {model.name}: {e}") from e

    def _open_unmix(self, audio: np.ndarray, model: Model, parts: list[str], workdir: Path) -> dict[str, np.ndarray]:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        network = self._loaded.get((model, workdir))
        if network is None:
            network = self._loaded[model, workdir] = self._open_unmix_network(model)
        network.to(device)
        rows = [list(network.target_models).index(model.parts[part]) for part in parts]

        def process(piece: np.ndarray) -> np.ndarray:
            given = torch.from_numpy(np.ascontiguousarray(piece.T[None], dtype=np.float32)).to(device)
            with torch.inference_mode():
                found = network(given)[0, rows].cpu().numpy()
            return np.stack([_fit(one.T, len(piece)) for one in found])

        return dict(zip(parts, in_pieces(audio, process, len(parts))))


def in_pieces(audio: np.ndarray, process: Callable[[np.ndarray], np.ndarray], n: int,
              piece: int = PIECE, fade: int = FADE) -> np.ndarray:
    """Process a song of any length in the same memory: pieces, each faded into the next.

    `process` returns `n` signals as long as the piece it is given.
    """
    result = np.zeros((n, *audio.shape), np.float32)
    ramp = ((np.arange(fade, dtype=np.float32) + 0.5) / fade)[:, None]
    start = 0
    while True:
        part = audio[start:start + piece]
        last = start + piece >= len(audio)
        gain = np.ones((len(part), 1), np.float32)
        if start:
            gain[:fade] = ramp  # longer than a fade: else the piece before would have been the last
        if not last:
            gain[-fade:] = ramp[::-1]
        result[:, start:start + len(part)] += process(part) * gain
        if last:
            return result
        start += piece - fade


def _fit(stem: np.ndarray, length: int) -> np.ndarray:
    if len(stem) >= length:
        return stem[:length]
    return np.pad(stem, ((0, length - len(stem)), (0, 0)))
