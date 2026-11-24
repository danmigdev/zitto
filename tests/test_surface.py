"""Drawing without the help of the system, and the buttons of the Mac: both work everywhere."""

import tkinter as tk

import numpy as np
import pytest

from zitto import theme
from zitto.surface_tk import Surface


@pytest.fixture
def root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.geometry("300x200")
    yield root
    root.destroy()


def test_the_picture_is_shown(root):
    surface = Surface(root)
    surface.widget.pack(fill="both", expand=True)
    surface.show()  # nothing yet
    surface.resize(40, 20)
    surface.pixels[:] = theme.rgb("#102030")
    surface.pixels[5, 7] = theme.rgb("#ff8001")
    surface.show()
    root.update()
    assert surface._photo.get(7, 5) == (255, 128, 1) and surface._photo.get(0, 0) == (16, 32, 48)
    surface.resize(60, 30)
    assert surface.pixels.shape == (30, 60) and not surface.pixels.any()
    surface.close()


def test_the_line_goes_away(root):
    surface = Surface(root)
    surface.resize(40, 20)
    surface.line(10, 2, "#ffffff")
    assert surface.widget.itemcget(surface._line, "state") == "normal"
    assert surface.widget.coords(surface._line) == [10, 0, 12, 20]
    surface.show(10, 2)
    assert surface.widget.itemcget(surface._line, "state") == "hidden"


def test_the_waveform_works_on_it(root, monkeypatch):
    from zitto import waveform
    monkeypatch.setattr(waveform, "Surface", Surface)
    sought = []
    wave = waveform.Waveform(root, 1.0, sought.append, lambda a, b: None, lambda a, b: None)
    wave.pack(fill="both", expand=True)
    root.update()
    data = np.zeros((44100, 2), np.int16)
    data[20000:24000] = 9000
    wave.set_tracks([waveform.peaks(data)] * 2, ["#ff0000", "#00ff00"], len(data))
    wave.set_loop((10000, 30000), True)
    root.update()
    assert (wave._surface.pixels == theme.rgb("#ff0000")).any() and (wave._surface.pixels == theme.rgb("#00ff00")).any()
    wave.set_head(22050, False)
    assert wave._surface.widget.itemcget(wave._surface._line, "state") == "normal"


def test_a_button_made_of_a_label(root):
    pressed = []
    button = theme.LabelButton(root, lambda: pressed.append(True), text="Play", padx=10, pady=4)
    button.pack()
    root.update()
    click = lambda x: button._released(type("E", (), {"x": x, "y": 2}))
    click(3)
    click(500)  # released outside
    button.configure(state="disabled")
    click(3)
    button._hover("active")
    assert pressed == [True] and str(button["state"]) == "disabled"
    button.configure(state="normal")
    button._hover("active")
    assert str(button["state"]) == "active"
