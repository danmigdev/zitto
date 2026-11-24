"""A picture in memory that numpy fills and Tk shows, on any system.

Showing the whole picture is slower than where the system can copy pixels, but it is rare: while
playing only the line of the playhead moves, and that is a shape of Tk over the picture.
"""

from __future__ import annotations

import tkinter as tk

import numpy as np

from .theme import BG


def keep_content_on_resize(root: tk.Tk, *widgets: tk.Widget) -> None:
    """Only Windows needs it."""


class Surface:
    forgets = False

    def __init__(self, parent: tk.Widget):
        self.widget = tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0, takefocus=0)
        self._photo: tk.PhotoImage | None = None
        self._image = self.widget.create_image(0, 0, anchor="nw")
        self._line = self.widget.create_rectangle(0, 0, 0, 0, width=0, state="hidden")
        self.width = self.height = 0
        self.pixels = np.zeros((0, 0), np.uint32)  # (height, width), 0x00rrggbb

    def resize(self, width: int, height: int) -> None:
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self.pixels = np.zeros((height, width), np.uint32)
        self._photo = tk.PhotoImage(master=self.widget, width=width, height=height)
        self.widget.itemconfigure(self._image, image=self._photo)

    def show(self, x: int = 0, width: int | None = None) -> None:
        """Show the picture. Some of its columns: only take the line away from them."""
        self.widget.itemconfigure(self._line, state="hidden")
        if width is None and self._photo:
            colors = np.empty((self.height, self.width, 3), np.uint8)
            for i, shift in enumerate((16, 8, 0)):
                colors[..., i] = self.pixels >> shift & 0xFF
            self._photo.configure(data=b"P6 %d %d 255\n" % (self.width, self.height) + colors.tobytes())

    def line(self, x: int, width: int, color: str) -> None:
        """A vertical line over the picture: `show` on the same columns takes it away."""
        self.widget.coords(self._line, x, 0, x + width, self.height)
        self.widget.itemconfigure(self._line, fill=color, state="normal")

    def close(self) -> None:
        self._photo = None
