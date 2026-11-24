"""Temporary: where the window hangs on macOS."""
import faulthandler, sys, time, tkinter as tk
import numpy as np
faulthandler.enable()
what = sys.argv[1]
faulthandler.dump_traceback_later(25, exit=True)
from pathlib import Path
import tempfile
from zitto import gui, library, models, player, plugins, settings, theme
tmp = Path(tempfile.mkdtemp())
library.ROOT, settings.PATH = tmp / "library", tmp / "settings.json"
plugins.links, plugins.model_set = (lambda: ()), (lambda: None)
class Silent:
    latency, mixer = 0, None
    def close(self): pass
player.Output = Silent
def none(f): raise OSError("none")
player.midi.MidiInput = none
gui.jobs.Work.start = lambda self: None
f = tmp / "a.wav"; f.write_bytes(b"x")
song = library.add(str(f), "Alpha")
library.save_stems(song.key, {n: np.random.default_rng(i).uniform(-.3, .3, (44100 * 8, 2)).astype(np.float32) for i, n in enumerate(models.stems())}, 1.0)
root = tk.Tk()
print("Tk", root.tk.call("info", "patchlevel"), "screen", root.winfo_screenwidth(), root.winfo_screenheight())
if what.startswith("hidden"): root.withdraw()
app = gui.App(root)
def update(name):
    s = time.perf_counter(); root.update(); print("  %-28s %7.1f ms" % (name, 1e3 * (time.perf_counter() - s)), flush=True)
update("first")
if what == "hidden-focus":
    app.player.wave.focus_set(); update("focus, nothing open")
else:
    app.open(song.key)
    if what == "hidden-nofocus": type(app.player.wave).focus_set = lambda self: None
    end = time.time() + 10
    while app.player.key != song.key and time.time() < end:
        update("opening"); time.sleep(0.01)
    print("  opened:", app.player.key == song.key, "size", app.player.wave.winfo_width(), app.player.wave.winfo_height())
    for i in range(3): update("after")
    if what == "shown":
        w = app.player.wave
        s = time.perf_counter()
        for _ in range(10): w._draw()
        root.update(); print("  full drawing %.1f ms" % (100 * (time.perf_counter() - s)))
        s = time.perf_counter()
        for i in range(100): w.set_head(i * 2000, False); root.update_idletasks()
        print("  playhead step %.2f ms" % (10 * (time.perf_counter() - s)))
        app.player.toggle_pieces("drums"); update("drums opened")
        app.player.do("play")
        s, n = time.time(), 0
        while time.time() - s < 2: root.update(); n += 1; time.sleep(0.001)
        print("  while playing: %.0f rounds of the window in a second" % (n / 2))
app.close(); print("  closed")
