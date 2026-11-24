"""Waveform view: one lane per part, zoom, playhead and loop selection."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import numpy as np

from .mixer import RATE
from .surface import Surface
from .theme import TK9, font, rgb, two_fingers, wheel_steps

HOP = 64  # samples per stored peak: the closest zoom
BACK, GRID, TEXT = "#14171c", "#2a303a", "#8a93a1"
LOOP_ON, LOOP_OFF, LOOP_EDGE, HEAD = "#3a3420", "#23272e", "#f5c451", "#ffffff"
MUTED = "#39404b"
STEPS = (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600)


def clock(samples: int, decimals: int = 1) -> str:
    units = round(samples / RATE * 10 ** decimals)
    seconds, fraction = divmod(units, 10 ** decimals)
    text = f"{seconds // 60}:{seconds % 60:02d}"
    return f"{text}.{fraction:0{decimals}d}" if decimals else text


def peaks(data: np.ndarray) -> np.ndarray:
    """Loudest sample of every HOP, as int32."""
    whole = len(data) // HOP * HOP
    blocks = data[:whole].reshape(-1, HOP * data.shape[1])
    result = np.maximum(blocks.max(axis=1).astype(np.int32), -blocks.min(axis=1).astype(np.int32))
    rest = data[whole:]
    return np.append(result, np.abs(rest.astype(np.int32)).max()) if len(rest) else result


def columns(values: np.ndarray, first: float, per_pixel: float, width: int) -> np.ndarray:
    """Loudest peak under each pixel column of the view."""
    edges = ((first + np.arange(width + 1) * per_pixel) / HOP).astype(np.int64)
    starts = edges[:-1]
    inside = (starts >= 0) & (starts < len(values))
    out = np.zeros(width, np.int32)
    if inside.any():
        end = min(int(edges[-1]) + 1, len(values))
        out[inside] = np.maximum.reduceat(values[:end], starts[inside])
    return out


def draw_lane(pixels: np.ndarray, sizes: np.ndarray, color: int, background: np.ndarray) -> None:
    """Fill a lane: each column is `color` up to its size from the middle, else its background."""
    height = len(pixels)
    distance = np.abs(np.arange(height, dtype=np.int16) - height // 2)[:, None]
    pixels[:] = np.where(distance <= sizes.astype(np.int16), np.uint32(color), background)


class Waveform(tk.Frame):
    """Positions are in samples. Calls on_seek(pos), on_loop(start, end) and on_view(first, last)."""

    def __init__(self, parent, scale: float, on_seek: Callable[[int], None],
                 on_loop: Callable[[int, int], None], on_view: Callable[[float, float], None]):
        super().__init__(parent, bg=BACK)
        self.ruler = round(22 * scale)
        self._grab = round(6 * scale)
        self._line = max(1, round(scale))
        self._on_seek, self._on_loop, self._on_view = on_seek, on_loop, on_view
        self._peaks: list[np.ndarray] = []
        self._colors: list[str] = []
        self._audible: list[bool] = []
        self._sizes: list[np.ndarray] = []  # of each lane, for the current view
        self._length = 1
        self._top = 1
        self._first = 0.0
        self._per_pixel = 1.0
        self._span = 1.0  # samples in view
        self._loop: tuple[int, int] | None = None
        self._loop_on = False
        self._drag: dict | None = None
        self._head_pos = 0
        self._head_x: int | None = None  # where it is drawn
        self._head_seen = True
        self._pending: str | None = None
        self._new_view = True

        self._marks = tk.Canvas(self, bg=BACK, height=self.ruler, highlightthickness=0, bd=0)
        self._marks.pack(fill="x")
        self._surface = Surface(self)
        self._body = self._surface.widget
        self._body.pack(fill="both", expand=True)
        self._body.bind("<Configure>", lambda e: self._resized())
        if self._surface.forgets:
            self._body.bind("<Expose>", lambda e: self._show())
        self._body.bind("<Destroy>", lambda e: self._surface.close())
        for area in (self._body, self._marks):
            area.bind("<ButtonPress-1>", self._press)
            area.bind("<B1-Motion>", self._move)
            area.bind("<ButtonRelease-1>", self._release)
            area.bind("<Motion>", self._hover)
            area.bind("<MouseWheel>", lambda e: self.zoom(0.8 ** wheel_steps(e), e.x))
            area.bind("<Shift-MouseWheel>", lambda e: self.pan(-wheel_steps(e) * 0.15))
            if TK9:
                area.bind("<TouchpadScroll>", self._two_fingers)

    # --- content ----------------------------------------------------------

    def set_tracks(self, values: list[np.ndarray], colors: list[str], length: int) -> None:
        """Show a song: the `peaks` of each part and its length in samples."""
        self._peaks, self._colors, self._length = values, colors, length
        self._audible = [True] * len(values)
        self._loop, self._loop_on, self._drag = None, False, None
        self._head_pos, self._head_seen = 0, True
        self._top = max(1, max(int(p.max()) for p in values))
        self.show_all()

    def set_lanes(self, values: list[np.ndarray], colors: list[str]) -> None:
        """Other lanes for the same song: the view and the loop stay."""
        self._peaks, self._colors = values, colors
        self._audible = [True] * len(values)
        self._top = max(self._top, max(int(p.max()) for p in values))
        self._new_view = True
        self._request()

    def set_audible(self, audible: list[bool]) -> None:
        changed = [i for i, (a, b) in enumerate(zip(audible, self._audible)) if a != b]
        self._audible = list(audible)
        if changed and self._sizes and not self._pending:
            background = self._background()
            for i in changed:
                self._draw_one(i, background)
            self._show()

    def set_loop(self, loop: tuple[int, int] | None, on: bool) -> None:
        if (loop, on) != (self._loop, self._loop_on) and not self._drag:
            self._loop, self._loop_on = loop, on
            self._request()

    def set_head(self, pos: int, follow: bool) -> None:
        """Move the playhead; turn the page when it leaves the view while playing."""
        width = self._body.winfo_width()
        seen = 0 <= self._x(pos) <= width
        if follow and not seen and self._head_seen and not self._drag:
            self._set_view(pos - 0.08 * width * self._per_pixel, self._per_pixel)
            seen = True
        self._head_pos, self._head_seen = pos, seen
        if not self._pending:
            self._draw_head()

    # --- view -------------------------------------------------------------

    def show_all(self) -> None:
        self._set_view(0, self._length / max(self._body.winfo_width(), 1))

    def show_loop(self) -> None:
        if self._loop:
            start, end = self._loop
            margin = (end - start) * 0.06
            self._set_view(start - margin, (end - start + 2 * margin) / max(self._body.winfo_width(), 1))

    def zoom(self, factor: float, x: int) -> None:
        at = self._pos(x)
        per_pixel = self._clamp_zoom(self._per_pixel * factor)
        self._set_view(at - x * per_pixel, per_pixel)

    def _two_fingers(self, event) -> None:
        """Across the music it scrolls, along the lanes it zooms. Never both at once."""
        x, y = two_fingers(event)
        if abs(x) > abs(y):
            self.pan(-x / max(self._body.winfo_width(), 1))
        else:
            self.zoom(0.8 ** (y / 30), event.x)

    def pan(self, pages: float) -> None:
        self._set_view(self._first + pages * self._body.winfo_width() * self._per_pixel, self._per_pixel)

    def scroll(self, *args) -> None:
        """Command of the horizontal scrollbar."""
        if args[0] == "moveto":
            self._set_view(float(args[1]) * self._length, self._per_pixel)
        else:
            self.pan(int(args[1]) * (0.9 if args[2] == "pages" else 0.1))

    def _clamp_zoom(self, per_pixel: float) -> float:
        widest = self._length / max(self._body.winfo_width(), 1)
        return min(max(per_pixel, min(HOP, widest)), widest)

    def _set_view(self, first: float, per_pixel: float) -> None:
        width = max(self._body.winfo_width(), 1)
        self._per_pixel = self._clamp_zoom(per_pixel)
        self._span = width * self._per_pixel
        self._first = min(max(first, 0.0), max(self._length - self._span, 0.0))
        self._on_view(self._first / self._length, (self._first + self._span) / self._length)
        self._new_view = True
        self._request()

    def _resized(self) -> None:
        """The same stretch of music in the new width."""
        self._set_view(self._first, self._span / max(self._body.winfo_width(), 1))

    def _x(self, pos: float) -> float:
        return (pos - self._first) / self._per_pixel

    def _pos(self, x: float) -> int:
        return int(min(max(self._first + x * self._per_pixel, 0), self._length))

    def lane(self, index: int, n: int) -> tuple[int, int]:
        """Top and height of a lane out of n, for the controls beside it too."""
        space = max(self.winfo_height() - self.ruler, 1)
        top = self.ruler + space * index // n
        return top, self.ruler + space * (index + 1) // n - top

    # --- drawing ----------------------------------------------------------

    def _request(self) -> None:
        """Draw as soon as there is nothing more urgent: many requests cause one drawing."""
        if not self._pending:
            self._pending = self.after_idle(self._draw)

    def _draw(self) -> None:
        self._pending = None
        width, height = self._body.winfo_width(), self._body.winfo_height()
        if width < 2 or height < 2:
            return
        self._surface.resize(width, height)
        if not self._peaks:
            self._surface.pixels[:] = rgb(BACK)
            self._show()
            return
        if self._new_view:
            self._new_view = False
            self._sizes = [columns(p, self._first, self._per_pixel, width) for p in self._peaks]
            self._draw_marks(width)
        background = self._background()
        for i in range(len(self._peaks)):
            self._draw_one(i, background)
        self._show()

    def _ticks(self) -> list[tuple[int, str]]:
        """Columns and labels of the time marks in view."""
        step = next((s for s in STEPS if s * RATE / self._per_pixel >= 90), STEPS[-1])
        t = np.ceil(self._first / RATE / step) * step
        ticks = []
        while (x := self._x(t * RATE)) < self._body.winfo_width():
            ticks.append((int(x), clock(round(t * RATE), 1 if step < 1 else 0)))
            t += step
        return ticks

    def _draw_marks(self, width: int) -> None:
        self._marks.delete("all")
        for x, text in self._ticks():
            self._marks.create_text(x + 4, self.ruler / 2, anchor="w", fill=TEXT, font=font(8), text=text)

    def _background(self) -> np.ndarray:
        """The colour of each column where there is no sound: loop, time marks."""
        width = self._surface.width
        background = np.full(width, rgb(BACK), np.uint32)
        if self._loop:
            start, end = (int(min(max(self._x(p), 0), width)) for p in self._loop)
            background[start:end] = rgb(LOOP_ON if self._loop_on else LOOP_OFF)
        for x, _ in self._ticks():
            background[x] = rgb(GRID)
        return background

    def _draw_one(self, i: int, background: np.ndarray) -> None:
        top, height = self.lane(i, len(self._peaks))
        area = self._surface.pixels[top - self.ruler:top - self.ruler + height]
        if len(area) < 2 or len(self._sizes[i]) != self._surface.width:
            return
        sizes = np.maximum(self._sizes[i] * (len(area) / 2 - 3) / self._top, 0)
        draw_lane(area, sizes, rgb(self._colors[i] if self._audible[i] else MUTED), background)
        area[0] = rgb(GRID)
        if self._loop:
            edge = rgb(LOOP_EDGE if self._loop_on else MUTED)
            for p in self._loop:
                x = int(self._x(p))
                area[:, max(x - 1, 0):max(x + 1, 0)] = edge

    def _show(self) -> None:
        self._surface.show()
        self._head_x = None
        self._draw_head()

    def _draw_head(self) -> None:
        x = int(self._x(self._head_pos))
        if x == self._head_x:
            return
        if self._head_x is not None:
            self._surface.show(self._head_x, self._line)
        self._head_x = None
        if self._peaks and 0 <= x < self._surface.width:
            self._surface.line(x, self._line, HEAD)
            self._head_x = x

    # --- mouse ------------------------------------------------------------

    def _edge_at(self, x: int) -> int | None:
        if not self._loop:
            return None
        distances = [abs(self._x(p) - x) for p in self._loop]
        nearest = int(distances[1] < distances[0])
        return nearest if distances[nearest] <= self._grab else None

    def _hover(self, event) -> None:
        self.configure(cursor="sb_h_double_arrow" if self._edge_at(event.x) is not None else "")

    def _press(self, event) -> None:
        self.focus_set()  # the keyboard goes back to the player
        if not self._peaks:
            return
        edge = self._edge_at(event.x)
        fixed = self._pos(event.x) if edge is None else self._loop[1 - edge]
        self._drag = {"x": event.x, "fixed": fixed, "edge": edge, "moved": False}

    def _move(self, event) -> None:
        if not self._drag:
            return
        if abs(event.x - self._drag["x"]) > 3:
            self._drag["moved"] = True
        if self._drag["moved"]:
            x = min(max(event.x, 0), self._body.winfo_width())
            self._loop, self._loop_on = tuple(sorted((self._drag["fixed"], self._pos(x)))), True
            self._request()

    def _release(self, event) -> None:
        drag, self._drag = self._drag, None
        if not drag:
            return
        if drag["moved"]:
            self._on_loop(*self._loop)
        elif drag["edge"] is None:
            self._on_seek(self._pos(event.x))
