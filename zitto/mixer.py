"""Transport, loop and live mixing of the stems of one track.

There is no audio device here: `render` is called by the sound card (see player.py)
and by the tests. Every public method can be called from any thread.
"""

from __future__ import annotations

import threading

import numpy as np

from .pitch import Reader, Shift, Stem

RATE = 44100
FADE = 128  # samples (3 ms): long enough to hide a jump, short enough not to be heard
MIN_LOOP = RATE // 20
BLOCK = 4096  # samples shifted together when they are not heard

PLAYING, PAUSED, STOPPED = "playing", "paused", "stopped"


def mixdown(stems: list[np.ndarray], gain: float, levels: list[float], shift: Shift | None = None) -> np.ndarray:
    """The whole song with the given level for each part, float32, not clipped. `shift`: as in `set_shift`."""
    out = np.zeros(stems[0].shape, np.float32)
    gains = [level * gain / 32768 for level in levels]
    for i, stem in enumerate(stems):
        if gains[i] and not (shift and shift.stems[i]):
            out += stem * np.float32(gains[i])
    if shift:
        reader = Reader([Stem(s) for s in stems], 0, shift, gains)
        for start in range(0, len(out), BLOCK):
            part = out[start:start + BLOCK]
            part += reader.read(len(part), gains)
    return out


class Mixer:
    def __init__(self, stems: list[np.ndarray], gain: float = 1.0):
        """`stems`: int16 arrays of the same shape (samples, 2). `gain`: their overall level."""
        self.length = len(stems[0])
        self._stems = stems
        self._shifting = [Stem(s) for s in stems]
        self._unit = gain / 32768
        self._lock = threading.Lock()
        self._wanted = [1.0] * len(stems)
        self._level = [1.0] * len(stems)  # follows _wanted, one block at a time
        self._pos = 0
        self._state = STOPPED
        self._gate = 0  # 0 silent ... FADE fully open
        self._park: int | None = None  # where to go once the gate has closed
        self._fade_from = 0
        self._fade_left = 0
        self._shift: Shift | None = None
        self._reader: Reader | None = None  # what shifts the stems from self._pos on
        self._old_reader: Reader | None = None  # the same for self._fade_from
        self._loop: tuple[int, int] | None = None
        self._loop_on = False

    # --- state ------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def loop(self) -> tuple[int, int] | None:
        return self._loop

    @property
    def loop_on(self) -> bool:
        return self._loop_on

    @property
    def position(self) -> int:
        return min(max(self._pos if self._park is None else self._park, 0), self.length)

    # --- transport --------------------------------------------------------

    def play(self) -> None:
        with self._lock:
            if self._park is not None:
                self._move(self._park)
            self._state = PLAYING

    def pause(self) -> None:
        """Pause, or resume when already paused."""
        with self._lock:
            if self._state == PLAYING:
                self._state = PAUSED
            elif self._state == PAUSED:
                if self._park is not None:
                    self._move(self._park)
                self._state = PLAYING

    def toggle(self) -> None:
        self.pause() if self._state == PLAYING else self.play()

    def stop(self) -> None:
        """Stop and go back to the start of the loop."""
        with self._lock:
            self._state = STOPPED
            self._place(self._home())

    def rewind(self) -> None:
        """Back to the start of the loop, without changing whether it plays."""
        with self._lock:
            self._place(self._home())

    def seek(self, pos: int) -> None:
        with self._lock:
            pos = min(max(pos, 0), self.length)
            if self._loop_on and self._seam() <= pos < self._loop[1]:
                pos = self._seam() - 1  # too close to the end to still repeat
            self._place(pos)

    def set_loop(self, start: int, end: int) -> None:
        with self._lock:
            start, end = sorted((min(max(start, 0), self.length), min(max(end, 0), self.length)))
            if end - start < MIN_LOOP:
                end = min(start + MIN_LOOP, self.length)
                start = max(end - MIN_LOOP, 0)
            self._loop = (start, end)
            self._loop_on = True
            self._enter_loop()

    def clear_loop(self) -> None:
        with self._lock:
            self._loop, self._loop_on = None, False

    def enable_loop(self, on: bool) -> None:
        with self._lock:
            self._loop_on = on and self._loop is not None
            if self._loop_on:
                self._enter_loop()

    def set_level(self, index: int, level: float) -> None:
        with self._lock:
            self._wanted[index] = float(level)

    def set_shift(self, shift: Shift | None) -> None:
        """Change the pitch of some stems, also while they play. None: of none."""
        if shift and not (shift.cents and any(shift.stems)):
            shift = None
        with self._lock:
            before, self._shift = self._shift, shift
            if (before and before.stems) != (shift and shift.stems):
                self._leave()

    # --- helpers, called with the lock held -------------------------------

    def _home(self) -> int:
        return self._loop[0] if self._loop_on else 0

    def _seam(self) -> int:
        """Where the end of the loop starts blending into what precedes its beginning."""
        return self._loop[1] - FADE

    def _enter_loop(self) -> None:
        pos = self._pos if self._park is None else self._park
        if not self._loop[0] <= pos < self._seam():
            self._place(self._loop[0])

    def _place(self, pos: int) -> None:
        """Change position as soon as it can be done without a click."""
        if self._gate and self._state != PLAYING:
            self._park = pos  # still fading out
        else:
            self._move(pos)

    def _move(self, pos: int) -> None:
        self._leave()
        self._pos, self._park = pos, None

    def _leave(self) -> None:
        """Fade out what is being heard, while something else starts."""
        if self._gate and self._fade_left < FADE:  # otherwise what replaced it has not been heard yet
            self._fade_from, self._fade_left, self._old_reader = self._pos, FADE, self._reader
        self._reader = None

    # --- audio ------------------------------------------------------------

    def render(self, frames: int) -> np.ndarray:
        """The next `frames` samples, float32 (frames, 2)."""
        out = np.zeros((frames, 2), np.float32)
        with self._lock:
            before, gains = ([level * self._unit for level in levels] for levels in (self._level, self._wanted))
            self._level = list(self._wanted)
            ramp = np.linspace(0, 1, frames + 1, dtype=np.float32)[1:, None]  # from some to the others
            done = 0
            while done < frames:
                n = self._advance_limit(frames - done)
                if n == 0:
                    break
                out[done:done + n] = self._chunk(n, before, gains, ramp[done:done + n])
                done += n
        return np.clip(out, -1.0, 1.0, out=out)

    def _advance_limit(self, n: int) -> int:
        """Handle what happens at the current position; return how far the audio is uniform."""
        if self._state != PLAYING and self._gate == 0:
            if self._park is not None:
                self._pos, self._park = self._park, None
            return 0
        if self._loop_on and self._pos == self._seam():
            self._move(self._loop[0] - FADE)  # blends over FADE samples: lands exactly on the start
        if self._pos >= self.length:
            self._state, self._gate, self._fade_left = STOPPED, 0, 0
            self._pos, self._park = self._home(), None
            return 0
        limits = [n, self.length - self._pos]
        if self._loop_on and self._pos < self._seam():
            limits.append(self._seam() - self._pos)
        if self._fade_left:
            limits.append(self._fade_left)
        if self._state != PLAYING:
            limits.append(self._gate)
        elif self._gate < FADE:
            limits.append(FADE - self._gate)
        return min(limits)

    def _chunk(self, n: int, *levels) -> np.ndarray:
        """`levels`: those before this block, those wanted, and how far from the ones to the others."""
        if self._shift is None:
            self._reader = None
        elif self._reader is None or self._reader.at != self._pos:
            self._reader = Reader(self._shifting, self._pos, self._shift, levels[0])
        else:
            self._reader.shift = self._shift
        audio = self._mix(self._pos, n, self._reader, *levels)
        if self._fade_left:
            w = (np.arange(n, dtype=np.float32) + (FADE - self._fade_left + 1))[:, None] / FADE
            audio = audio * w + self._mix(self._fade_from, n, self._old_reader, *levels) * (1 - w)
            self._fade_from += n
            self._fade_left -= n
        if self._state != PLAYING:
            audio *= np.arange(self._gate - 1, self._gate - 1 - n, -1, dtype=np.float32)[:, None] / FADE
            self._gate -= n
        elif self._gate < FADE:
            audio *= np.arange(self._gate + 1, self._gate + 1 + n, dtype=np.float32)[:, None] / FADE
            self._gate += n
        self._pos += n
        return audio

    def _mix(self, pos: int, n: int, reader: Reader | None, before: list[float], gains: list[float],
             ramp: np.ndarray) -> np.ndarray:
        """`n` samples from `pos` on. `reader`: what reads the stems that change pitch, if any does."""
        out = np.zeros((n, 2), np.float32)
        lo, hi = max(pos, 0), min(pos + n, self.length)
        if lo < hi:
            part, rising = out[lo - pos:hi - pos], ramp[lo - pos:hi - pos]
            for i, (stem, old, gain) in enumerate(zip(self._stems, before, gains)):
                if reader and reader.shift.stems[i]:
                    continue
                if old != gain:
                    part += stem[lo:hi] * (np.float32(old) + np.float32(gain - old) * rising)
                elif gain:
                    part += stem[lo:hi] * np.float32(gain)
        if reader:
            out += reader.read(n, gains, before, ramp)
        return out
