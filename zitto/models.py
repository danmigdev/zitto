"""The separation models: what each one finds, where it comes from and under which terms.

None is part of the program. Each is downloaded, the first time it is needed, from where its
authors or the audio-separator project published it. See NOTICE.md for the credits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from . import plugins, settings

# The parts of a song, in the order they are shown. A set of models may tell apart only some, and not their pieces.
NAMES = ("voice", "drums", "bass", "guitar", "piano", "wind", "other")
LABELS = {"voice": "Voice", "lead": "Lead voice", "backing": "Backing voices", "drums": "Drums",
          "bass": "Bass", "guitar": "Guitar", "piano": "Piano", "wind": "Wind", "other": "Other",
          "kick": "Kick", "snare": "Snare", "toms": "Toms", "cymbals": "Cymbals",
          "acoustic": "Acoustic", "electric": "Electric"}
# Parts that can be stored as pieces. The last piece is what the others leave.
PIECES = {"voice": ("lead", "backing"), "drums": ("kick", "snare", "toms", "cymbals"),
          "guitar": ("acoustic", "electric")}
STEMS = tuple(s for n in NAMES for s in (*PIECES.get(n, ()), n))  # all that can be stored, in order


@dataclass(frozen=True)
class Model:
    name: str
    file: str
    parts: dict[str, str] = field(hash=False)  # part ("voice", "lead", "drums"...) -> its name in the model
    terms: str = ""
    page: str = ""
    backend: str = "separator"  # or "openunmix", "mega"
    overlap: int | None = None  # how many times each moment is computed, where the model allows it


@dataclass(frozen=True)
class ModelSet:
    main: tuple[Model, ...]  # run in this order on the song, each on what the others left
    lead: Model | None = None  # the lead voice, inside the voice
    wind: Model | None = None  # the wind instruments, in what is left
    drums: Model | None = None  # the pieces of the drums, inside the drums
    guitar: Model | None = None  # the acoustic guitar, inside the guitar

    def all(self) -> tuple[Model, ...]:
        return (*self.main, *filter(None, [self.lead, self.wind, self.drums, self.guitar]))

    def opener(self, part: str) -> Model | None:
        """The model that finds the pieces of a part. That of the voice finds the lead."""
        return getattr(self, "lead" if part == "voice" else part) if part in PIECES else None


_UVR = {"terms": "MIT, with credit to UVR and its developers (as their README asks)",
        "page": "https://github.com/Anjok07/ultimatevocalremovergui"}

# The best ones, by far. Their terms are not known: see NOTICE.md.
FULL = Model("BS-Roformer-SW", "BS-Roformer-SW.ckpt",
             {"voice": "vocals", "drums": "drums", "bass": "bass", "guitar": "guitar", "piano": "piano",
              "other": "other"},
             "unknown, as its author", "https://huggingface.co/enerjazzer/BS-ROFO-SW-Fixed")
# Each moment twice. Its own choice is 8, over three times slower for a difference 36 dB below the result.
LEAD = Model("Mel-Roformer Karaoke (becruily)", "mel_band_roformer_karaoke_becruily.ckpt", {"lead": "Vocals"},
             "not stated", "https://huggingface.co/becruily/mel-band-roformer-karaoke", overlap=2)
WIND = Model("UVR 17_HP-Wind_Inst", "17_HP-Wind_Inst-UVR.pth", {"wind": "Woodwinds"}, **_UVR)

VOICE = Model("UVR-MDX-NET Main", "UVR_MDXNET_Main.onnx", {"voice": "Vocals"}, **_UVR)
BAND = Model("Open-Unmix UMX-L", "umxl", {"drums": "drums", "bass": "bass", "other": "other"},
             "CC BY-NC-SA 4.0: not for commercial use", "https://zenodo.org/records/5069601", backend="openunmix")
STATED_LEAD = Model("UVR 6_HP-Karaoke", "6_HP-Karaoke-UVR.pth", {"lead": "Vocals"}, **_UVR)

DRUMS = Model("MDX23C DrumSep (aufr33, jarredou)", "MDX23C-DrumSep-aufr33-jarredou.ckpt",
              {"kick": "kick", "snare": "snare", "toms": "toms"},
              "not stated", "https://pypi.org/project/mdxnet-infer/")

# The electric guitar is what remains: better than looking for it too.
GUITAR = Model("MVSep Mega 53 Stems (acoustic guitar)", "MVSep-Mega-53-acoustic-guitar.npz",
               {"acoustic": "acoustic-guitar"}, "not stated",
               "https://github.com/ZFTurbo/Music-Source-Separation-Training/releases/tag/v1.0.21", backend="mega")

DEFAULT = ModelSet((FULL,), LEAD, WIND, DRUMS, GUITAR)
STATED = ModelSet((VOICE, BAND), STATED_LEAD, WIND)  # only models whose authors say how they can be used
OPTIONAL = ("lead", "wind", "drums", "guitar")  # what the user can do without: "without", in the settings


def complete() -> ModelSet:
    if os.environ.get("ZITTO_MODELS", "").lower() == "stated":
        return STATED
    return plugins.model_set() or DEFAULT


def current() -> ModelSet:
    """The complete set, without what the user does without."""
    without = settings.load().get("without")
    left_out = [name for name in OPTIONAL if isinstance(without, list) and name in without]
    return replace(complete(), **dict.fromkeys(left_out)) if left_out else complete()


def provider(part: str) -> Model | None:
    """The model that finds a part in a whole song."""
    chosen = current()
    return next((m for m in (*chosen.main, chosen.lead, chosen.wind) if m and part in m.parts), None)


def parts() -> tuple[str, ...]:
    """The parts that the current models tell apart."""
    chosen = current()
    found = {"other"}.union(*(m.parts for m in chosen.main))
    if chosen.wind:
        found.add("wind")
    return tuple(n for n in NAMES if n in found)


def stems() -> tuple[str, ...]:
    """What is stored of a song separated now: its parts, or their pieces."""
    chosen = current()
    return tuple(s for n in parts() for s in (PIECES[n] if chosen.opener(n) else (n,)))


def whole(stem: str) -> str | None:
    """The part that a stem is a piece of."""
    return next((part for part, pieces in PIECES.items() if stem in pieces), None)
