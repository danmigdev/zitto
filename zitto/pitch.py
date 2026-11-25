"""Changes the pitch of stems while they play, and not how long they last.

A phase vocoder: the sound is stretched in time, then read faster or slower by as much. The whole
song is in memory, so it reads ahead and adds no delay. What keeps it close to the original:

- how much each partial turns from one window to the next is measured between two windows that
  are exactly that far apart, so it does not have to be guessed
- the bins around a peak turn with it, left and right together (identity phase locking)
- all the stems turn together, as what is heard of them asks: they have sounds in common, which
  would no longer add up if each turned on its own
- a note begins as it is in the original: around its attack nothing is stretched, and the time is
  made up for before and after it

There is no audio device here: see mixer.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

SIZE = 4096  # of a window, in samples
OVERLAP = 4  # windows over each moment
TAPS, PHASES = 32, 512  # of the filter that reads between the samples
ENVELOPE = 64  # detail of the timbre of a voice, in samples: less than a period of the highest notes


@dataclass(frozen=True)
class Shift:
    """How the pitch of a song is changed."""
    cents: float
    stems: tuple[bool, ...]  # whether each is changed
    natural: tuple[bool, ...]  # whether each keeps its timbre: what makes a voice that of somebody

    @property
    def ratio(self) -> float:
        """Of the frequencies."""
        return 2.0 ** (self.cents / 1200)

    def gains(self, gains: Sequence[float]) -> list[float]:
        """Without those of the stems that are not changed."""
        return [gain if changed else 0.0 for gain, changed in zip(gains, self.stems)]


def _kernel() -> np.ndarray:
    """A low-pass filter, moved by each fraction of a sample."""
    half = TAPS // 2
    t = np.arange(1 - half, half + 1) - np.arange(PHASES + 2)[:, None] / PHASES
    kernel = np.sinc(0.94 * t) * np.i0(8 * np.sqrt(np.clip(1 - (t / half) ** 2, 0, 1)))
    return (kernel / kernel.sum(axis=1, keepdims=True)).astype(np.float32)


KERNEL = _kernel()


class Stem:
    """A stem, and where its notes begin: found a stretch at a time, when it is first asked."""

    GRAIN = 64  # samples
    AFTER, BEFORE = 8, 24  # grains: a note begins where the energy of these is RISE times that of those
    APART = 23  # grains: at least, between two notes
    STRETCH = 256  # grains found together
    RISE = 3.0
    FLOOR = 512.0  # energy of a grain of noise too low to be heard

    def __init__(self, data: np.ndarray):
        """`data`: int16, (samples, 2)."""
        self.data = data
        self._found: dict[int, tuple[list[int], np.ndarray]] = {}

    def add_to(self, out: np.ndarray, start: int, gain: float) -> None:
        """Add the samples from `start` on to `out` (2, samples)."""
        lo, hi = max(start, 0), min(start + out.shape[1], len(self.data))
        if lo < hi:
            out[:, lo - start:hi - start] += self.data[lo:hi].T * np.float32(gain)

    def notes(self, n: int) -> tuple[list[int], np.ndarray]:
        """Of the n-th stretch: the grains where notes begin, and the energy that follows each grain."""
        found = self._found.get(n)
        if found is None:
            found = self._found[n] = self._find(n)
        return found

    def _find(self, n: int) -> tuple[list[int], np.ndarray]:
        before = self.BEFORE + self.APART
        grains = self.STRETCH + 2 * self.APART + self.BEFORE + self.AFTER - 1
        x = np.zeros((2, grains * self.GRAIN + 1), np.float32)
        self.add_to(x, (n * self.STRETCH - before) * self.GRAIN - 1, 1)
        steps = np.diff(x)  # more weight to the high frequencies, where attacks are
        energy = np.square(steps, out=steps).reshape(2, grains, self.GRAIN).sum(axis=(0, 2), dtype=np.float64)
        total = np.concatenate(([0.0], np.cumsum(energy)))
        i = np.arange(self.BEFORE, grains - self.AFTER + 1)
        after = (total[i + self.AFTER] - total[i]) / self.AFTER
        rise = after / ((total[i] - total[i - self.BEFORE]) / self.BEFORE + self.FLOOR)
        highest = np.lib.stride_tricks.sliding_window_view(rise, 2 * self.APART + 1).max(axis=1)
        inside = slice(self.APART, -self.APART)
        begin = np.flatnonzero((rise[inside] == highest) & (highest >= self.RISE))
        return [n * self.STRETCH + int(g) for g in begin], after[inside]


@dataclass
class Window:
    start: int  # in the stretched sound
    centre: int  # in the song
    turn: np.ndarray  # of each bin


class Reader:
    """The stems that change pitch, from a position on. What is read is always what comes next."""

    SHARE = 0.03  # of the energy of all the stems: notes that have less are not looked after
    TOGETHER = 4  # grains: notes of different stems that begin together

    def __init__(self, stems: Sequence[Stem], pos: int, shift: Shift, gains: Sequence[float], size: int = SIZE):
        """`gains`: of all the stems."""
        self.at = pos  # what comes next
        self._stems, self._shift, self._gains = stems, shift, shift.gains(gains)
        self._size, self._step, self._bins = size, size // OVERLAP, size // 2 + 1
        self._shape = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(size) / size)).astype(np.float32)
        self._unshape = self._shape * np.float32(1 / (0.375 * OVERLAP))  # so that they add up to 1
        self._ratio = shift.ratio
        # Positions in the stretched sound: at self._origin[0] of the song it is at self._origin[1].
        self._origin = (pos, 0.0)
        self._next = 1 - TAPS // 2 - size + self._step  # where the next window starts
        self._base = self._next  # where self._made starts
        self._made = np.zeros((2, 8 * size), np.float32)
        self._windows: list[Window] = []  # those that are still heard
        self._last = np.zeros((2, self._bins), np.complex64)  # the spectrum of the last one, turned
        self._begun: int | None = None  # the last note that began
        self._notes: dict[int, list[tuple[int, list[int]]]] = {}

    @property
    def shift(self) -> Shift:
        """It can be another while it is being read, but of the same stems."""
        return self._shift

    @shift.setter
    def shift(self, shift: Shift) -> None:
        if shift.ratio != self._ratio:
            self._origin = (self.at, self._stretched(self.at))
            self._ratio = shift.ratio
        self._shift = shift

    def _stretched(self, pos: float) -> float:
        return self._origin[1] + self._ratio * (pos - self._origin[0])

    # --- reading ----------------------------------------------------------

    def read(self, n: int, gains: Sequence[float], before: Sequence[float] | None = None,
             ramp: np.ndarray | None = None) -> np.ndarray:
        """The next `n` samples, float32 (n, 2).

        `gains`: of all the stems. With a `ramp` (n, 1) they go from those of `before`, at 0, to them, at 1.
        """
        gains = self._shift.gains(gains)
        before = gains if ramp is None else self._shift.gains(before)
        self._use(before)
        sound = self._sound(n)
        if before != gains:
            self._use(gains)
            sound += (self._sound(n) - sound) * ramp
        self.at += n
        return sound

    def _sound(self, n: int) -> np.ndarray:
        pos = self._stretched(self.at) + self._ratio * np.arange(n)
        whole = np.floor(pos).astype(np.int64)
        first = int(whole[0]) + 1 - TAPS // 2
        self._make(first, int(whole[-1]) + 1 + TAPS // 2)
        phase = (pos - whole) * PHASES
        k = phase.astype(np.int64)
        kernel = KERNEL[k] + (KERNEL[k + 1] - KERNEL[k]) * (phase - k)[:, None].astype(np.float32)
        samples = self._made[:, (whole + 1 - TAPS // 2 - self._base)[:, None] + np.arange(TAPS)]
        return np.einsum("cnk,nk->nc", samples, kernel)

    def _make(self, first: int, end: int) -> None:
        """Stretched sound up to `end`. What comes before `first` will not be needed again."""
        if end + self._size - self._base > self._made.shape[1]:
            base = min(first, self._next)
            kept = self._made[:, base - self._base:]
            made = np.zeros((2, max(8 * self._size, end + self._size - base, kept.shape[1])), np.float32)
            made[:, :kept.shape[1]] = kept
            self._made, self._base = made, base
        while self._next < end:
            self._add_window()
        self._windows = [w for w in self._windows if w.start + self._size > first]

    def _use(self, gains: list[float]) -> None:
        """Make again what is still heard, with the same turns: only the levels change."""
        if gains == self._gains:
            return
        self._gains, self._notes = gains, {}
        self._made[:] = 0
        self._last[:] = 0
        for window in self._windows:
            x, voice = self._mix(window.centre - self._size // 2, self._size)
            self._last = self._put(window, self._spectrum(x), voice)

    # --- windows ----------------------------------------------------------

    def _mix(self, start: int, count: int, only: Sequence[int] | None = None) -> tuple[np.ndarray, np.ndarray | None]:
        """What is heard of the stems, and of those that keep their timbre if there are any."""
        x = np.zeros((2, count), np.float32)
        voice = None
        for i, (stem, gain) in enumerate(zip(self._stems, self._gains)):
            if not gain or (only is not None and i not in only):
                continue
            if self._shift.natural[i] and only is None:
                voice = np.zeros((2, count), np.float32) if voice is None else voice
                stem.add_to(voice, start, gain)
            else:
                stem.add_to(x, start, gain)
        if voice is not None:
            x += voice
        return x, voice

    def _spectrum(self, x: np.ndarray) -> np.ndarray:
        return np.fft.rfft(x * self._shape)

    def _add_window(self) -> None:
        centre, beginning = self._aim()
        x, voice = self._mix(centre - self._size // 2 - self._step, self._size + self._step)
        now = self._spectrum(x[:, self._step:])
        before = self._spectrum(x[:, :self._size])
        powers = power(now)
        near = nearest_peak(powers)
        turn = (self._last * before.conj()).sum(axis=0)[near]
        length = np.abs(turn)
        turn = np.divide(turn, length, out=np.ones_like(turn), where=length > 0)
        if beginning:  # what begins starts from its own phases, what is held goes on
            own, _ = self._mix(centre - self._size // 2, self._size, beginning)
            turn[2 * power(self._spectrum(own))[near] > powers[near]] = 1
        window = Window(self._next, centre, turn)
        self._windows.append(window)
        self._last = self._put(window, now, None if voice is None else voice[:, self._step:])
        self._next += self._step

    def _put(self, window: Window, spectrum: np.ndarray, voice: np.ndarray | None) -> np.ndarray:
        """Add a window to the stretched sound. Returns its spectrum, turned."""
        turned = spectrum * window.turn
        shaped = turned
        if voice is not None:
            part = self._spectrum(voice)
            shaped = turned + part * (window.turn * (self._timbre(power(part)) - 1))
        if self._ratio > 1:  # what would fold over
            edge = int(self._bins / self._ratio)
            shaped = shaped.copy()
            shaped[:, edge:] = 0
            shaped[:, edge - 16:edge] *= np.linspace(1, 0, 16, dtype=np.float32)
        at = window.start - self._base
        skip = max(-at, 0)  # what will not be needed again
        self._made[:, at + skip:at + self._size] += (np.fft.irfft(shaped, self._size) * self._unshape)[:, skip:]
        return turned

    def _timbre(self, powers: np.ndarray) -> np.ndarray:
        """What makes the envelope of a spectrum, once it is read at another speed, what it was: as loud as before."""
        log = np.log(powers + 1e-6 * powers.max() + 1e-12) / 2
        detail = np.fft.irfft(log)
        detail[ENVELOPE:1 - ENVELOPE] = 0
        envelope = np.fft.rfft(detail).real
        bins = np.arange(self._bins)
        there = np.interp(bins * self._ratio, bins, envelope)
        gains = np.exp(np.clip(there - envelope, -3, 3))
        loud = np.sum(powers * gains ** 2, dtype=np.float64)
        return (gains * np.sqrt(np.sum(powers, dtype=np.float64) / loud) if loud else gains).astype(np.float32)

    # --- notes ------------------------------------------------------------

    def _aim(self) -> tuple[int, list[int]]:
        """Where in the song the next window is taken, and the stems of the notes that begin there."""
        centre = self._next + self._size // 2
        pos = self._origin[0] + (centre - self._origin[1]) / self._ratio
        note, stems = self._note_near(pos, self._size / 2 / self._ratio)
        if note is None:
            return round(pos), []
        past = centre - self._stretched(note)
        if past < -self._size / 4 or note == self._begun:
            return round(note + past), []
        self._begun = note
        return round(note + past), stems

    def _note_near(self, pos: float, reach: float) -> tuple[int | None, list[int]]:
        stretch = Stem.GRAIN * Stem.STRETCH
        found = [note for n in range(int((pos - reach) // stretch), int((pos + reach) // stretch) + 1)
                 for note in self._heard_notes(n)]
        return min((note for note in found if abs(note[0] - pos) <= reach), key=lambda note: abs(note[0] - pos),
                   default=(None, []))

    def _heard_notes(self, n: int) -> list[tuple[int, list[int]]]:
        """Of a stretch: where the notes that are heard begin, and in which stems."""
        if n not in self._notes:
            self._notes[n] = self._choose_notes(n)
        return self._notes[n]

    def _choose_notes(self, n: int) -> list[tuple[int, list[int]]]:
        heard = [(i, gain ** 2, [self._stems[i].notes(m) for m in (n - 1, n, n + 1)])
                 for i, gain in enumerate(self._gains) if gain]
        if not heard:
            return []
        first = (n - 1) * Stem.STRETCH
        total = sum(g2 * np.concatenate([after for _, after in found]) for _, g2, found in heard)
        notes = []  # energy, grain, stem
        for i, g2, found in heard:
            for m, (grains, after) in enumerate(found):
                for grain in grains:
                    energy = g2 * after[grain - first - m * Stem.STRETCH]
                    if energy >= self.SHARE * total[grain - first]:
                        notes.append((energy, grain, i))
        chosen: list[int] = []
        for _, grain, _ in sorted(notes, reverse=True):
            if all(abs(grain - other) > Stem.APART for other in chosen):
                chosen.append(grain)
        return [(grain * Stem.GRAIN, sorted({i for _, g, i in notes if abs(g - grain) <= self.TOGETHER}))
                for grain in sorted(chosen) if n * Stem.STRETCH <= grain < (n + 1) * Stem.STRETCH]


def power(spectrum: np.ndarray) -> np.ndarray:
    """Of left and right together."""
    return (spectrum.real ** 2 + spectrum.imag ** 2).sum(axis=0)


def nearest_peak(powers: np.ndarray) -> np.ndarray:
    """For each bin, that of the nearest peak."""
    k = np.arange(len(powers))
    here = powers[2:-2]
    peaks = 2 + np.flatnonzero((here > powers[1:-3]) & (here >= powers[3:-1]) & (here > powers[:-4])
                               & (here >= powers[4:]))
    if not len(peaks):
        return k
    return peaks[np.searchsorted((peaks[1:] + peaks[:-1]) / 2, k)]
