"""A real separation, with the models used by default. Not one of the tests: it downloads them.

    python tests/separation.py models       download the models
    python tests/separation.py cpu | mps    separate a generated song, on that device
    python tests/separation.py gpu          what the GPU can do by itself
"""

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

RATE, SECONDS = 44100, 12
START = time.time()


def say(*what):
    print("%6.1f s " % (time.time() - START), *what, flush=True)


def song() -> np.ndarray:
    t, rng = np.arange(RATE * SECONDS) / RATE, np.random.default_rng(1)
    x = 0.2 * np.sin(2 * np.pi * 110 * t) + 0.15 * np.sin(2 * np.pi * 330 * t) * np.sin(np.pi * t) ** 2
    x += rng.normal(0, 0.2, len(t)) * np.exp(-((t % 0.5) / 0.06))
    return np.repeat(x[:, None] * 0.6, 2, 1).astype("float32")


def download():
    from audio_separator.separator import Separator

    from zitto import engine, models
    separator = Separator(model_file_dir=str(engine.MODELS_DIR))
    default = models.current()
    for model in (*default.main, default.lead, default.wind, default.drums):
        separator.download_model_files(model.file)
        say(model.name, "%.0f MB" % ((engine.MODELS_DIR / model.file).stat().st_size / 1e6))


def gpu():
    say("recommended memory %.1f GB" % (torch.mps.recommended_max_memory() / 1e9))
    x = torch.randn(1000, 1000, device="mps")
    say("a product", float((x @ x).abs().mean()))
    with torch.autocast("mps", torch.float16):
        say("the same in 16 bits", float((x @ x).float().abs().mean()))
    s = torch.stft(torch.randn(2, RATE, device="mps"), 2048, 512, window=torch.hann_window(2048, device="mps"),
                   return_complex=True)
    say("a spectrum", tuple(s.shape), float(s.abs().mean()))
    y = torch.nn.functional.scaled_dot_product_attention(*(torch.randn(8, 8, 1000, 64, device="mps"),) * 3)
    say("attention", float(y.abs().mean()))


def separate(device: str):
    if device == "cpu":
        torch.backends.mps.is_available = lambda: False
    from zitto import audio, engine, library, models, settings, worker

    library.ROOT, settings.PATH = Path("ci_library_" + device), Path("ci_settings.json")
    work = Path("ci_work").resolve()
    work.mkdir(exist_ok=True)
    mix = song()
    sf.write("song_%s.wav" % device, mix, RATE)
    added = library.add("song_%s.wav" % device)
    eng = engine.Engine()
    worker.prepare(added, eng, work, say)
    stems = library.load_stems(added.key)
    total = sum(d.astype(np.float64) for d in stems.data) / 32768 * stems.gain
    error = float(np.abs(total - mix).max())
    say("separated on", device, "parts as expected:", stems.names == models.stems(), "error of the sum %.5f" % error)
    for name, data in zip(stems.names, stems.data):
        say("   %-8s %.4f" % (name, float(np.sqrt((data.astype(np.float64) / 32768 * stems.gain) ** 2).mean())))
    assert stems.names == models.stems() and error < 1e-3
    lead = eng.stems(mix, models.current().lead, ["lead"], work)["lead"]
    say("lead", lead.shape, "finite:", bool(np.isfinite(lead).all()))
    assert np.isfinite(lead).all() and lead.shape == mix.shape


if __name__ == "__main__":
    what = sys.argv[1]
    {"models": download, "gpu": gpu}.get(what, lambda: separate(what))()
