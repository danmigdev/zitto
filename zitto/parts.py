"""The controls beside the waveform: name, mute, solo and volume of each part.

One canvas instead of some forty widgets, which Windows moves and paints one by one: too slow
while the window is being resized.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .theme import ACCENT, BAD, BG, BUTTON, DIM, PANEL, TEXT, font

BUTTONS = 0.6  # share of the height of a row for name and buttons; the rest is the volume


def side_by_side(height: float, side: float) -> bool:
    """Whether M and S are beside each other: in a low row, one over the other they could not be read."""
    return height * BUTTONS < side * 0.85


def target(x: float, y: float, width: float, height: float, side: float, arrow: float = 0) -> str:
    """What is at a point of a row: "open", "name", "mute", "solo" or "volume"."""
    if y >= height * BUTTONS:
        return "volume"
    if x < width - side:
        return "open" if x < arrow else "name"
    if side_by_side(height, side):
        return "mute" if x < width - side / 2 else "solo"
    return "mute" if y < height * BUTTONS / 2 else "solo"


class PartControls(tk.Canvas):
    """Calls on_mute(i), on_solo(i), on_volume(i, 0..1) and on_open(i).

    `pieces`: the rows that are pieces of another one. `opened`: the rows that have pieces, and
    whether these are shown.
    """

    def __init__(self, parent, scale: float, labels: list[str], colors: list[str],
                 on_mute: Callable[[int], None], on_solo: Callable[[int], None],
                 on_volume: Callable[[int, float], None], on_open: Callable[[int], None] | None = None,
                 pieces: set[int] = frozenset(), opened: dict[int, bool] | None = None):
        super().__init__(parent, bg=BG, width=round(190 * scale), highlightthickness=0, bd=0, cursor="hand2",
                         takefocus=0)
        self._k = scale
        self._colors = colors
        self._on_mute, self._on_solo, self._on_volume, self._on_open = on_mute, on_solo, on_volume, on_open
        self._pieces, self._opened = pieces, opened or {}
        self._places: list[tuple[int, int]] = []  # top and height of each row
        self._volumes = [1.0] * len(labels)
        self._dragging: int | None = None
        self._rows = [self._build_row(i, label) for i, label in enumerate(labels)]
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)

    def _build_row(self, i: int, label: str) -> dict[str, int]:
        def box(color):
            return self.create_rectangle(0, 0, 0, 0, width=0, fill=color)

        row = {"back": box(PANEL), "name": box(BUTTON), "mute": box(BUTTON), "solo": box(BUTTON),
               "track": box(BUTTON), "level": box(DIM), "knob": box(TEXT)}
        row["label"] = self.create_text(0, 0, text=label, anchor="w", font=font(10, bold=True))
        if i in self._opened:
            row["arrow"] = self.create_text(0, 0, text="▾" if self._opened[i] else "▸",
                                            font=font(11, symbols=True))
        for name in ("mute", "solo"):
            row[f"{name} text"] = self.create_text(0, 0, text=name[0].upper(), font=font(8, bold=True))
        return row

    @property
    def _side(self) -> int:
        return round(32 * self._k)

    def _left(self, i: int) -> int:
        return round(16 * self._k) if i in self._pieces else 0

    def _arrow(self, i: int) -> int:
        return round(26 * self._k) if i in self._opened else 0

    def place_rows(self, places: list[tuple[int, int]]) -> None:
        """Align with the lanes: top and height of each."""
        if places == self._places:
            return
        self._places = places
        width, side = int(self["width"]) - 4, self._side
        for i, (row, (top, height)) in enumerate(zip(self._rows, places)):
            bottom = top + height - 2
            buttons = top + round(height * BUTTONS)
            middle = top + round(height * BUTTONS / 2)
            left, right = self._left(i), width - side + 1
            self.coords(row["back"], left, top, width, bottom)
            self.coords(row["name"], left, top, width - side, buttons)
            self.coords(row["label"], left + (self._arrow(i) or 10 * self._k), middle)
            if "arrow" in row:
                self.coords(row["arrow"], left + 13 * self._k, middle)
            if side_by_side(height, side):
                centre = width - side // 2
                mute, solo = (right, top, centre, buttons), (centre + 1, top, width, buttons)
            else:
                mute, solo = (right, top, width, middle - 1), (right, middle, width, buttons)
            for name, (x0, y0, x1, y1) in (("mute", mute), ("solo", solo)):
                self.coords(row[name], x0, y0, x1, y1)
                self.coords(row[f"{name} text"], (x0 + x1) / 2, (y0 + y1) / 2)
        for i in range(len(self._rows)):
            self._place_volume(i)

    def _volume_span(self, i: int) -> tuple[float, float]:
        return self._left(i) + 10 * self._k, int(self["width"]) - 4 - 10 * self._k

    def _place_volume(self, i: int) -> None:
        if i >= len(self._places):
            return
        top, height = self._places[i]
        first, last = self._volume_span(i)
        y = top + height * (1 + BUTTONS) / 2 - 1
        x = first + (last - first) * self._volumes[i]
        half, knob = 2 * self._k, min(5 * self._k, height * (1 - BUTTONS) / 2 - 1)
        row = self._rows[i]
        self.coords(row["track"], first, y - half, last, y + half)
        self.coords(row["level"], first, y - half, x, y + half)
        self.coords(row["knob"], x - half, y - knob, x + half, y + knob)

    def show(self, i: int, heard: bool, muted: bool, solo: bool, volume: float) -> None:
        row, color = self._rows[i], self._colors[i]
        self.itemconfigure(row["name"], fill=color if heard else BUTTON)
        for name in ("label", "arrow"):
            if name in row:
                self.itemconfigure(row[name], fill=BG if heard else DIM)
        self.itemconfigure(row["mute"], fill=BAD if muted else BUTTON)
        self.itemconfigure(row["mute text"], fill=BG if muted else DIM)
        self.itemconfigure(row["solo"], fill=ACCENT if solo else BUTTON)
        self.itemconfigure(row["solo text"], fill=BG if solo else DIM)
        self.itemconfigure(row["level"], fill=color if heard else DIM)
        if volume != self._volumes[i]:
            self._volumes[i] = volume
            self._place_volume(i)

    def _press(self, event) -> None:
        for i, (top, height) in enumerate(self._places):
            if top <= event.y < top + height:
                left = self._left(i)
                what = target(event.x - left, event.y - top, int(self["width"]) - 4 - left, height, self._side,
                              self._arrow(i))
                if what == "volume":
                    self._dragging = i
                    self._move(event)
                elif what == "solo":
                    self._on_solo(i)
                elif what == "open":
                    self._on_open(i)
                else:
                    self._on_mute(i)
                return

    def _move(self, event) -> None:
        if self._dragging is not None:
            first, last = self._volume_span(self._dragging)
            self._on_volume(self._dragging, min(max((event.x - first) / (last - first), 0.0), 1.0))

    def _release(self, event) -> None:
        self._dragging = None
