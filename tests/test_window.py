"""The real window, not shown, without sound card, MIDI, GPU or network."""

import sys
import time
import tkinter as tk

import numpy as np
import pytest

from zitto import gui, library, models, player, theme
from zitto.mixer import PLAYING
from zitto.parts import target
from zitto.waveform import HOP, columns, draw_lane, peaks

N = 44100 * 4

pytestmark = pytest.mark.usefixtures("links")


def make_song(letter, name, ready=True, names=models.NAMES):
    """A song of the library, separated or not."""
    song = library.add(f"https://songs.example/{letter}", name)
    if ready:
        parts = {n: np.full((N, 2), 0.01 * (i + 1), np.float32) for i, n in enumerate(names)}
        library.save_stems(song.key, parts, 1.0)
    return song.key


def until(app, condition):
    end = time.time() + 5
    while not condition() and time.time() < end:
        app.root.update()
        time.sleep(0.01)
    assert condition()


def names(app):
    return [app.tree.item(r, "text") for r in app.keys]


def find(app, text):
    app.query.set(text)
    app._search_now()
    return names(app)


# --- list and search ---------------------------------------------------------------

def test_search_finds_every_word_anywhere_without_accents(window):
    for letter, name in (("a", "Alfa - Dreams At Night"), ("b", "Beta - Città di prova"), ("c", "Gamma - Dreams")):
        make_song(letter, name)
    app = window()
    assert len(names(app)) == 3
    assert find(app, "dreams") == ["Alfa - Dreams At Night", "Gamma - Dreams"]
    assert find(app, "DREAMS alfa") == ["Alfa - Dreams At Night"]
    assert find(app, "citta") == ["Beta - Città di prova"]
    assert find(app, "nothing like this") == [] and app.tree.get_children() == ()
    app._clear_search()
    assert len(names(app)) == 3


def test_search_by_playlist_shows_its_songs(window):
    songs = [(f"Song {c}", f"https://songs.example/{c}") for c in "xyz"]
    library.add_playlist("https://songs.example/list/one", "Road trip", songs)
    make_song("a", "Other")
    assert find(window(), "road") == ["Song x", "Song y", "Song z"]


def test_enter_in_the_search_opens_the_first_result(window):
    make_song("a", "Alpha")
    key = make_song("b", "Beta")
    app = window()
    app.query.set("bet")
    app._open_first()
    until(app, lambda: app.player.key == key)
    assert app.player.name.cget("text") == "Beta"


def test_a_closed_playlist_stays_closed(window):
    saved = library.add_playlist("https://songs.example/list/one", "Mine", [("S", "https://songs.example/a")])
    app = window()
    app.tree.item(saved.id, open=False)
    app.reload()
    assert not app.tree.item(saved.id, "open")
    find(app, "s")
    assert app.tree.item(saved.id, "open")  # results must be visible


def test_one_song_is_refreshed_without_reading_the_others(window, monkeypatch):
    ready, waiting = make_song("a", "Alpha"), make_song("b", "Beta", ready=False)
    app = window()
    assert app.left == 1 and app.started
    monkeypatch.setattr(library, "songs", lambda: pytest.fail("the whole library was read"))
    app.work.run = 7
    app._work_event({"event": "song", "key": waiting, "run": 7})
    app._work_event({"event": "progress", "percent": 40, "run": 7})
    row = app.rows[waiting][0]
    assert app.tree.set(row, "state") == "40%" and app.tree.set(gui.SINGLES, "state") == "1/2"
    app._work_event({"event": "progress", "percent": 90, "run": 6})  # from a worker stopped before
    assert app.tree.set(row, "state") == "40%"
    library.save_stems(waiting, {n: np.zeros((10, 2), np.float32) for n in models.NAMES}, 1.0)
    app._work_event({"event": "ready", "key": waiting, "run": 7})
    assert app.tree.set(row, "state") == "" and app.tree.set(gui.SINGLES, "state") == "2" and app.left == 0
    assert ready in app.songs


def test_a_long_list_is_fast(window):
    from zitto import settings
    songs = [(f"Band {n} - Song {n}", f"https://songs.example/{n}") for n in range(1500)]
    for n, (name, link) in enumerate(songs):
        settings.write(library.folder(library.key_for(link)) / "song.json",
                       {"source": link, "title": name, "order": n, "single": True})
    app = window()
    assert len(names(app)) == 1500
    start = time.perf_counter()
    found = find(app, "band 1499")
    app.root.update()
    assert found == ["Band 1499 - Song 1499"] and time.perf_counter() - start < 1
    key = app.keys[next(iter(app.keys))]
    start = time.perf_counter()
    app.update_song(key)
    assert time.perf_counter() - start < 0.1


def test_renaming(window, monkeypatch):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    app.tree.selection_set(app.rows[key][0])
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "  Band -  Song ")
    app.rename_selected()
    assert names(app) == ["Band - Song"] and app.player.name.cget("text") == "Band - Song"
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "")
    app.rename_selected()
    assert names(app) == ["Alpha"]


def test_deleting_a_playlist_under_a_search_says_how_many_songs_go(window, monkeypatch):
    songs = [(f"Song {c}", f"https://songs.example/{c}") for c in "xyz"]
    saved = library.add_playlist("https://songs.example/list/one", "Mine", songs)
    app = window()
    find(app, "song x")
    asked = []
    monkeypatch.setattr(gui.messagebox, "askokcancel", lambda title, text: asked.append(text))
    app.tree.selection_set(saved.id)
    app.delete_selected()
    assert "all 3 songs" in asked[0] and len(library.songs()) == 3


def test_a_finished_song_does_not_cut_the_one_that_is_playing(window):
    first, second = make_song("a", "Alpha"), make_song("b", "Beta", ready=False)
    app = window()
    app.open(first)
    until(app, lambda: app.player.key == first)
    app.player.do("play")
    app.open(second)
    library.save_stems(second, {n: np.zeros((N, 2), np.float32) for n in models.NAMES}, 1.0)
    app._work_event({"event": "ready", "key": second, "run": app.work.run})
    app.root.update()
    assert app.player.key == first and app.player.mixer.state == PLAYING
    assert "Ready: Beta" in app.player.message.cget("text")


def test_keys_do_nothing_while_typing(window):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    event = tk.Event()
    event.widget = app.search
    for sequence in ("<space>", "<Key-1>"):
        app.root.tk.call("event", "generate", app.search._w, sequence)
    assert app.player.mixer.state != PLAYING and all(app.player.on)


# --- parts ---------------------------------------------------------------------------

def test_mute_and_solo(window):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    p = app.player
    p.toggle_part(2)
    assert p.audible() == [n != "drums" for n in models.NAMES]
    p.toggle_solo(3)
    assert p.audible() == [n == "bass" for n in models.NAMES]
    p.toggle_solo(2)  # muted: silent also when soloed
    assert p.audible() == [n == "bass" for n in models.NAMES]
    p.toggle_part(2)
    assert p.audible() == [n in ("drums", "bass") for n in models.NAMES]
    assert p.mixer._wanted == [1.0 if n in ("drums", "bass") else 0.0 for n in models.NAMES]


def test_controls_act_where_they_are_drawn(window):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    p = app.player
    p.parts.place_rows([(22 + 60 * i, 60) for i in range(8)])
    press = lambda item, row: p.parts._press(type("E", (), dict(zip(("x", "y"), middle(p.parts, item, row)))))

    def middle(canvas, item, row):
        x0, y0, x1, y1 = canvas.coords(canvas._rows[row][item])
        return (x0 + x1) / 2, (y0 + y1) / 2

    press("solo", 1)
    press("mute", 4)
    press("name", 5)
    assert [i for i, s in enumerate(p.solo) if s] == [1]
    assert [i for i, on in enumerate(p.on) if not on] == [4, 5]
    press("track", 6)
    assert p.volume[6] == pytest.approx(0.5, abs=0.02)
    p.parts._release(None)


def test_songs_can_have_different_parts(window):
    """Those separated with other models."""
    few = ("lead", "backing", "drums", "bass", "wind", "other")
    first, second = make_song("a", "Alpha"), make_song("b", "Beta", names=few)
    app = window()
    p = app.player
    app.open(first)
    until(app, lambda: p.key == first)
    assert p.names == models.NAMES
    p.toggle_part(7)
    app.open(second)
    until(app, lambda: p.key == second)
    assert p.names == few and p.on == [True] * len(few) and len(p.parts._rows) == len(few)
    p.toggle_main(7)  # a key with no part
    p.toggle_part(1)
    assert p.audible() == [n != "backing" for n in few]
    app.open(first)
    until(app, lambda: p.key == first)
    assert p.on == [True] * 7 + [False]
    p.leave()
    assert list(library.load_state(second)["on"]) == list(few)


def test_the_drums_open_into_their_pieces(window):
    key = make_song("a", "Alpha", names=models.stems())
    other = make_song("b", "Beta")  # separated before, without pieces
    app = window()
    p = app.player
    app.open(key)
    until(app, lambda: p.key == key)
    assert len(p.parts._rows) == 8 and len(p.names) == 12 and "arrow" in p.parts._rows[2]
    p.toggle_main(2)  # the key of the drums
    kick, bass = p.names.index("kick"), p.names.index("bass")
    level = lambda stem: p.mixer._wanted[models.stems().index(stem)]
    assert [level(s) for s in ("kick", "snare", "toms", "cymbals", "bass")] == [0, 0, 0, 0, 1]
    p.toggle_main(2)
    p.toggle_main(3)  # the next part, not a piece
    assert not p.on[bass] and all(p.on[kick:kick + 4])
    p.toggle_main(3)

    p.wave._set_view(N // 4, 100)
    view = p.wave._first, p.wave._per_pixel
    p.toggle_pieces("drums")
    app.root.update()
    assert len(p.parts._rows) == len(p.wave._peaks) == 12 and (p.wave._first, p.wave._per_pixel) == view
    p.toggle_part(kick)
    p.set_volume(p.names.index("drums"), 0.5)
    p.set_volume(p.names.index("snare"), 0.5)
    assert level("kick") == 0 and level("snare") == pytest.approx(0.0625) and level("toms") == pytest.approx(0.25)
    assert p.audible()[p.names.index("drums")]

    app.open(other)
    until(app, lambda: p.key == other)
    assert p.names == models.NAMES and len(p.parts._rows) == len(p.wave._peaks) == 8 and p.mixer._wanted == [1.0] * 8
    app.open(key)
    until(app, lambda: p.key == key)
    assert p.opened == {"drums"} and len(p.parts._rows) == 12 and level("kick") == 0
    p.toggle_pieces("drums")
    assert len(p.parts._rows) == len(p.wave._peaks) == 8 and level("kick") == 0  # hidden, not switched on again


def test_the_arrow_opens_the_drums(window):
    key = make_song("a", "Alpha", names=models.stems())
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    p = app.player
    p.parts.place_rows([(22 + 60 * i, 60) for i in range(8)])
    x, y = p.parts.coords(p.parts._rows[2]["arrow"])
    p.parts._press(type("E", (), {"x": x, "y": y}))
    assert p.opened == {"drums"} and all(p.on)
    p.parts.place_rows([(22 + 34 * i, 34) for i in range(12)])  # low rows: M and S side by side
    for item in ("mute", "solo"):
        x0, y0, x1, y1 = p.parts.coords(p.parts._rows[4][item])
        p.parts._press(type("E", (), {"x": (x0 + x1) / 2, "y": (y0 + y1) / 2}))
    assert not p.on[4] and p.solo[4] and sum(p.solo) == 1


@pytest.mark.parametrize("x, y, what", [
    (10, 5, "name"), (100, 34, "name"), (180, 5, "mute"), (180, 17, "mute"), (180, 19, "solo"), (180, 35, "solo"),
    (10, 37, "volume"), (180, 59, "volume"),
])
def test_what_is_under_the_mouse(x, y, what):
    assert target(x, y, 186, 60, 32) == what


@pytest.mark.skipif(sys.platform == "darwin", reason="Tk hangs with two windows in a process")
def test_the_state_of_the_parts_comes_back(window):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    app.player.toggle_part(0)
    app.player.toggle_solo(4)
    app.player.set_volume(2, 0.3)
    app.player.mixer.set_loop(44100, 88200)
    app.player.leave()
    again = window()
    until(again, lambda: again.player.key == key)  # the last song opens by itself
    p = again.player
    assert (p.on[0], p.solo[4], p.volume[2], p.mixer.loop, p.mixer.loop_on) == (False, True, 0.3, (44100, 88200), True)


def open_wave(window):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    app.root.deiconify()
    app.root.attributes("-alpha", 0.0)
    app.root.geometry("1200x600")
    for _ in range(5):
        app.root.update()
        time.sleep(0.01)
    return app, app.player.wave


def test_a_notch_of_the_wheel_zooms_a_little(window):
    app, wave = open_wave(window)
    before = wave._per_pixel
    wave._body.event_generate("<MouseWheel>", delta=theme.NOTCH, x=100, y=10)
    app.root.update()
    assert wave._per_pixel == pytest.approx(before * 0.8, rel=0.01)
    first = wave._first
    wave._body.event_generate("<Shift-MouseWheel>", delta=-theme.NOTCH, x=100, y=10)
    app.root.update()
    assert wave._per_pixel == pytest.approx(before * 0.8, rel=0.01) and wave._first > first


@pytest.mark.skipif(not theme.TK9, reason="Tk 8 has no event of the touchpad")
def test_two_fingers_on_the_touchpad(window):
    app, wave = open_wave(window)
    before = wave._per_pixel
    wave._body.event_generate("<TouchpadScroll>", delta=30, x=100, y=10)
    app.root.update()
    assert wave._per_pixel == pytest.approx(before * 0.8, rel=0.01)
    first = wave._first
    wave._body.event_generate("<TouchpadScroll>", delta=(-20 & 0xffff) << 16 | 3, x=100, y=10)
    app.root.update()
    assert wave._per_pixel == pytest.approx(before * 0.8, rel=0.01)
    assert wave._first - first == pytest.approx(20 * wave._per_pixel, rel=0.05)


@pytest.mark.parametrize("x, y", [(0, 0), (3, -4), (-3, 4), (-1, -1), (0, 30), (-32768, 32767)])
def test_both_fingers_movements_are_in_one_number(x, y):
    packed = (x & 0xffff) << 16 | y & 0xffff
    for delta in (packed, packed - (1 << 32)):  # it can arrive with a sign or without
        event = tk.Event()
        event.delta = delta
        assert theme.two_fingers(event) == (x, y)


def test_resizing_keeps_the_same_stretch_of_music(window):
    key = make_song("a", "Alpha")
    app = window()
    app.open(key)
    until(app, lambda: app.player.key == key)
    app.root.deiconify()
    app.root.attributes("-alpha", 0.0)
    wave = app.player.wave

    def view(size):
        app.root.geometry(size)
        for _ in range(5):
            app.root.update()
            time.sleep(0.01)
        return wave._first, wave._first + wave._body.winfo_width() * wave._per_pixel

    assert view("1200x600") == pytest.approx((0, N), abs=2)
    assert view("1800x700") == pytest.approx((0, N), abs=2)
    wave._set_view(N // 4, N / 2 / wave._body.winfo_width())
    before = view("1800x701")
    assert view("1150x600") == pytest.approx(before, rel=0.01)
    assert (wave._surface.width, wave._surface.height) == (wave._body.winfo_width(), wave._body.winfo_height())


# --- drawing -----------------------------------------------------------------------------

def test_columns_show_the_loudest_sample_of_each():
    data = np.zeros((HOP * 100, 2), np.int16)
    data[HOP * 42 + 5, 1] = -1000
    values = peaks(data)
    assert values[42] == 1000 and values.sum() == 1000
    assert columns(values, 0, HOP * 10, 10).tolist() == [0, 0, 0, 0, 1000, 0, 0, 0, 0, 0]
    assert columns(values, HOP * 40, HOP, 5).tolist() == [0, 0, 1000, 0, 0]
    assert columns(values, HOP * 95, HOP, 10).tolist() == [0] * 10  # past the end


def test_loudest_value_does_not_overflow():
    assert peaks(np.full((HOP, 2), -32768, np.int16)).tolist() == [32768]


def test_a_lane_is_drawn_from_its_middle():
    pixels = np.zeros((9, 3), np.uint32)
    draw_lane(pixels, np.array([0, 2, 4]), 7, np.array([1, 2, 3], np.uint32))
    assert pixels[:, 0].tolist() == [1, 1, 1, 1, 7, 1, 1, 1, 1]
    assert pixels[:, 1].tolist() == [2, 2, 7, 7, 7, 7, 7, 2, 2]
    assert pixels[:, 2].tolist() == [7] * 9
