"""Player logic: mixing, loop, MIDI. No sound card, MIDI device, GPU or network."""

import json
import sys
import types

import numpy as np
import pytest

from zitto import midi
from zitto.library import StemSet
from zitto.mixer import FADE, MIN_LOOP, PAUSED, PLAYING, STOPPED, Mixer, mixdown
from zitto.player import all_peaks, controls, heard, mix_name, pitch_name

N = 40000


def ramp_stem():
    """Left channel: the sample number, so the output tells exactly what was played."""
    data = np.zeros((N, 2), np.int16)
    data[:, 0] = np.arange(N) // 2
    return data


def tone(freq, level=8000, n=N):
    x = (level * np.sin(2 * np.pi * freq * np.arange(n) / 44100)).astype(np.int16)
    return np.stack([x, x], axis=1)


def render(mixer, total, block=512):
    blocks = []
    while total > 0:
        blocks.append(mixer.render(min(block, total)))
        total -= len(blocks[-1])
    return np.concatenate(blocks)


def where(out):
    """Sample positions from the ramp stem."""
    return np.round(out[:, 0] * 32768).astype(int) * 2


def biggest_step(out):
    return np.abs(np.diff(out, axis=0)).max()


# --- mixing --------------------------------------------------------------------

def test_all_stems_together_are_the_exact_sum():
    parts = [tone(200), tone(555), tone(900)]
    mixer = Mixer(parts, gain=1.25)
    mixer.play()
    out = render(mixer, 20000)
    expected = sum(p.astype(np.float64) for p in parts)[:20000] * 1.25 / 32768
    np.testing.assert_allclose(out[FADE:], expected[FADE:], atol=1e-6)


def test_silent_until_play():
    mixer = Mixer([tone(200)])
    assert not render(mixer, 5000).any() and mixer.position == 0 and mixer.state == STOPPED


@pytest.mark.parametrize("block", [64, 333, 512, 4096])
def test_result_does_not_depend_on_the_block_size(block):
    def play(size):
        mixer = Mixer([tone(200)])
        mixer.set_loop(1000, 9000)
        mixer.play()
        return render(mixer, 30000, size)
    np.testing.assert_allclose(play(block), play(1), atol=1e-6)


def test_switching_a_stem_is_immediate_and_has_no_click():
    mixer = Mixer([tone(200), tone(300)])
    mixer.play()
    render(mixer, 5120)
    mixer.set_level(1, 0)
    out = render(mixer, 5120, 512)
    solo = tone(200).astype(np.float64)[5120:10240] / 32768
    np.testing.assert_allclose(out[512:], solo[512:], atol=1e-6)  # gone within one block
    assert biggest_step(out) < 0.03  # the natural slope of these tones


def test_loud_mix_is_clipped_not_wrapped():
    mixer = Mixer([tone(200, 30000), tone(200, 30000)])
    mixer.play()
    assert np.abs(render(mixer, 8000)).max() == 1.0


# --- loop ----------------------------------------------------------------------

def test_loop_repeats_with_exact_length():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(8000, 18000)
    assert mixer.position == 8000
    mixer.play()
    pos = where(render(mixer, 35000))
    for turn in range(3):
        start = turn * 10000
        np.testing.assert_allclose(pos[start + FADE:start + 10000 - FADE],
                                   np.arange(8000 + FADE, 18000 - FADE), atol=2)
    assert pos.max() < 18000 and mixer.state == PLAYING


def test_loop_seam_has_no_click():
    mixer = Mixer([tone(440)])
    mixer.set_loop(5000, 5000 + 10037)  # ends at an arbitrary phase
    mixer.play()
    out = render(mixer, 40000)
    natural = biggest_step(tone(440) / 32768)
    assert biggest_step(out) < natural * 1.6


def test_loop_from_the_very_beginning_to_the_very_end():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(0, N)
    mixer.play()
    pos = where(render(mixer, N * 2))
    np.testing.assert_allclose(pos[N + FADE:2 * N - FADE], np.arange(FADE, N - FADE), atol=2)


def test_playing_into_a_loop_then_repeating():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(8000, 18000)
    mixer.seek(2000)
    mixer.play()
    pos = where(render(mixer, 20000))
    np.testing.assert_allclose(pos[FADE:15000], np.arange(2000 + FADE, 17000), atol=2)
    assert pos[16000 + 500] == pytest.approx(8500, abs=2)


def test_past_the_end_of_the_loop_goes_on():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(8000, 18000)
    mixer.seek(25000)
    mixer.play()
    assert where(render(mixer, 10000))[-1] == pytest.approx(34999, abs=2)


def test_seek_close_to_the_end_of_the_loop_still_repeats():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(8000, 18000)
    mixer.seek(17990)
    mixer.play()
    assert where(render(mixer, 3000)).max() < 18000


def test_new_loop_moves_the_playhead_only_when_outside():
    mixer = Mixer([ramp_stem()])
    mixer.seek(10000)
    mixer.set_loop(8000, 18000)
    assert mixer.position == 10000
    mixer.set_loop(20000, 30000)
    assert mixer.position == 20000


def test_loop_is_ordered_bounded_and_not_too_short():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(18000, 8000)
    assert mixer.loop == (8000, 18000)
    mixer.set_loop(100, 101)
    assert mixer.loop == (100, 100 + MIN_LOOP)
    mixer.set_loop(N - 5, N + 999)
    assert mixer.loop == (N - MIN_LOOP, N)


def test_loop_can_be_switched_off_and_on():
    mixer = Mixer([ramp_stem()])
    mixer.enable_loop(True)
    assert not mixer.loop_on  # there is none
    mixer.set_loop(8000, 18000)
    mixer.enable_loop(False)
    mixer.play()
    assert where(render(mixer, 15000)).max() > 20000 and mixer.loop == (8000, 18000)
    mixer.enable_loop(True)
    assert mixer.position == 8000
    mixer.clear_loop()
    assert mixer.loop is None and not mixer.loop_on


# --- transport -----------------------------------------------------------------

def test_pause_resumes_from_the_same_point():
    mixer = Mixer([ramp_stem()])
    mixer.play()
    render(mixer, 10240)
    mixer.pause()
    out = render(mixer, 2048)
    held = mixer.position
    assert mixer.state == PAUSED and not out[FADE:].any() and held == pytest.approx(10240, abs=FADE)
    mixer.pause()
    assert mixer.state == PLAYING
    assert where(render(mixer, 512))[-1] == pytest.approx(held + 511, abs=2)


def test_pause_does_nothing_when_stopped():
    mixer = Mixer([ramp_stem()])
    mixer.pause()
    assert mixer.state == STOPPED


@pytest.mark.parametrize("loop, home", [(None, 0), ((8000, 18000), 8000)])
def test_stop_and_rewind_go_to_the_start_of_the_loop(loop, home):
    mixer = Mixer([ramp_stem()])
    if loop:
        mixer.set_loop(*loop)
    mixer.play()
    render(mixer, 4096)
    mixer.rewind()
    assert mixer.state == PLAYING
    assert where(render(mixer, 1024))[-1] == pytest.approx(home + 1023, abs=2)
    mixer.stop()
    assert mixer.state == STOPPED and mixer.position == home
    out = render(mixer, 1024)
    assert not out[FADE:].any() and mixer.position == home
    mixer.play()
    assert where(render(mixer, 1024))[-1] == pytest.approx(home + 1023, abs=2)


def test_stop_then_play_before_the_fade_has_ended():
    mixer = Mixer([ramp_stem()])
    mixer.set_loop(8000, 18000)
    mixer.play()
    render(mixer, 4096)
    mixer.stop()
    mixer.play()
    assert where(render(mixer, 1024))[-1] == pytest.approx(9023, abs=2)


def test_transport_has_no_clicks():
    mixer = Mixer([tone(440)])
    natural = biggest_step(tone(440) / 32768)
    mixer.play()
    out = [render(mixer, 3001)]
    for action in (mixer.pause, mixer.pause, mixer.rewind, mixer.stop, mixer.play,
                   lambda: mixer.seek(20000), lambda: mixer.set_loop(2000, 9000), mixer.stop):
        action()
        out.append(render(mixer, 3001))
    assert biggest_step(np.concatenate(out)) < natural * 1.6


def test_end_of_the_track_stops_and_returns_to_the_start():
    mixer = Mixer([ramp_stem()])
    mixer.seek(N - 1000)
    mixer.play()
    out = render(mixer, 4096)
    assert mixer.state == STOPPED and mixer.position == 0 and not out[1000:].any()


# --- MIDI ----------------------------------------------------------------------

@pytest.mark.parametrize("message, expected", [
    ([0x90, 60, 100], ("note", 0, 60)),
    ([0x93, 60, 1], ("note", 3, 60)),
    ([0x90, 60, 0], None),  # note on with no velocity is a release
    ([0x80, 60, 64], None),
    ([0xB0, 20, 127], ("cc", 0, 20)),
    ([0xB0, 20, 64], ("cc", 0, 20)),
    ([0xB0, 20, 0], None),
    ([0xC2, 5], ("program", 2, 5)),
    ([0xF0, 0x7F, 0x7F, 0x06, 0x02, 0xF7], ("mmc", 2)),
    ([0xF0, 0x7E, 0x7F, 0x06, 0x02, 0xF7], None),  # identity reply, not transport
    ([0xF0, 0x43], None),
    ([0xFA], ("realtime", 0xFA)),
    ([0xF8], None),
    ([0xE0, 0, 64], None),
    ([0x90], None),
    ([], None),
])
def test_midi_trigger(message, expected):
    assert midi.trigger(message) == expected


def test_standard_transport_works_without_setup():
    b = midi.Bindings()
    for command, actions in [(1, ("stop",)), (2, ("play",)), (5, ("rewind",)), (9, ("pause",)), (6, ())]:
        assert b.actions(("mmc", command)) == actions
    assert b.actions(("realtime", 0xFA)) == ("rewind", "play")
    assert b.actions(("note", 0, 60)) == ()


def test_learning_replaces_and_never_duplicates():
    b = midi.Bindings()
    b.learn("play", ("note", 0, 60))
    b.learn("stop", ("note", 0, 60))
    assert b.get("play") is None and b.actions(("note", 0, 60)) == ("stop",)
    b.learn("stop", ("cc", 0, 20))
    assert b.actions(("note", 0, 60)) == ()
    b.learn("rewind", ("mmc", 2))  # a learned button wins over the standard meaning
    assert b.actions(("mmc", 2)) == ("rewind",)
    b.clear("rewind")
    assert b.actions(("mmc", 2)) == ("play",)


def test_bindings_survive_saving_and_ignore_rubbish():
    b = midi.Bindings()
    b.learn("play", ("note", 9, 36))
    b.learn("pause", ("mmc", 9))
    saved = json.loads(json.dumps(b.to_json()))
    assert midi.Bindings(saved).to_json() == b.to_json()
    bad = {"play": ["note", 1], "stop": "x", "fly": ["note", 0, 1], "pause": [], "rewind": ["note", 0, "a"]}
    assert midi.Bindings(bad).to_json() == {}


@pytest.mark.parametrize("t, text", [
    (("note", 0, 60), "Note 60, channel 1"), (("cc", 15, 7), "CC 7, channel 16"),
    (("mmc", 2), "MMC Play"), (("realtime", 0xFC), "MIDI Stop"),
])
def test_describe(t, text):
    assert midi.describe(t) == text


class FakeMidiIn:
    ports = ["Pad 0", "Keys 1"]
    opened = []
    busy = False

    def get_ports(self): return list(self.ports)
    def ignore_types(self, sysex, timing, active_sense): self.ignored = (sysex, timing, active_sense)
    def set_callback(self, f): self.callback = f
    def close_port(self): pass
    def delete(self): pass

    def open_port(self, index):
        if self.busy:
            raise RuntimeError("busy")
        FakeMidiIn.opened.append((self, index))


@pytest.fixture
def fake_rtmidi(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", types.SimpleNamespace(MidiIn=FakeMidiIn))
    monkeypatch.setattr(midi, "_need_core_midi", lambda: None)
    FakeMidiIn.opened, FakeMidiIn.busy = [], False


def test_midi_messages_drive_the_mixer(fake_rtmidi):
    mixer, bindings = Mixer([ramp_stem()]), midi.Bindings()
    mixer.set_loop(8000, 18000)
    bindings.learn("play", ("note", 0, 36))
    bindings.learn("stop", ("cc", 0, 21))

    def on_message(message):
        for action in bindings.actions(midi.trigger(message)):
            getattr(mixer, action)()

    port = midi.MidiInput(on_message)
    port.open("Keys 1")
    device, index = FakeMidiIn.opened[0]
    assert index == 1 and device.ignored[0] is False  # sysex must get through for MMC
    send = lambda *m: device.callback((list(m), 0.0))
    send(0x90, 36, 100)
    assert mixer.state == PLAYING
    render(mixer, 3000)
    send(0x90, 36, 0)  # release: nothing
    assert mixer.state == PLAYING
    send(0xF0, 0x7F, 0x7F, 0x06, 0x09, 0xF7)  # MMC pause
    assert mixer.state == PAUSED
    render(mixer, 512)
    send(0xF0, 0x7F, 0x7F, 0x06, 0x05, 0xF7)  # MMC rewind
    assert mixer.state == PAUSED and mixer.position == 8000
    send(0xFB)
    assert mixer.state == PLAYING
    send(0xB0, 21, 127)
    assert mixer.state == STOPPED and mixer.position == 8000


@pytest.mark.parametrize("failures", [0, 2, 99])
def test_the_midi_service_of_the_mac(monkeypatch, failures):
    # When it does not start, rtmidi ends the whole program: it must not be reached.
    class Function:
        def __init__(self, name):
            self.name = name

        def __call__(self, *args):
            if self.name == "MIDIClientCreate":
                asked.append(args)
                return -304 if len(asked) <= failures else 0
            return 1

    class Library:
        def __init__(self, path): pass
        def __getattr__(self, name): return Function(name)

    asked = []
    monkeypatch.setitem(sys.modules, "rtmidi", types.SimpleNamespace(MidiIn=FakeMidiIn))
    monkeypatch.setattr(midi.sys, "platform", "darwin")
    monkeypatch.setattr(midi.ctypes, "CDLL", Library)
    monkeypatch.setattr(midi.time, "sleep", lambda s: None)
    if failures > 2:
        with pytest.raises(OSError, match="-304"):
            midi.MidiInput(lambda m: None)
    else:
        midi.MidiInput(lambda m: None)
    assert len(asked) == min(failures + 1, 3)


@pytest.mark.skipif(sys.platform != "darwin", reason="only the Mac has it")
def test_the_real_midi_service_of_the_mac():
    try:
        midi._need_core_midi()
    except OSError as e:
        assert "did not start" in str(e)


def test_midi_port_errors_and_renumbering(fake_rtmidi):
    port = midi.MidiInput(lambda m: None)
    assert port.find("Keys 7") == "Keys 1" and port.find("Pad 0") == "Pad 0" and port.find("Organ 0") is None
    with pytest.raises(OSError, match="not found"):
        port.open("Organ 0")
    FakeMidiIn.busy = True
    with pytest.raises(OSError, match="Another program"):
        port.open("Pad 0")
    assert port.name is None


# --- saving --------------------------------------------------------------------

def test_mixdown_is_what_is_heard():
    parts = [tone(200), tone(555), tone(900)]
    levels = [1.0, 0.0, 0.25]
    mixer = Mixer(parts, gain=1.2)
    for i, level in enumerate(levels):
        mixer.set_level(i, level)
    render(mixer, 512)
    mixer.play()
    heard = render(mixer, 20000)
    np.testing.assert_allclose(mixdown(parts, 1.2, levels)[FADE:20000], heard[FADE:], atol=1e-6)


ALL = ("lead", "backing", "drums", "bass", "guitar", "piano", "wind", "other")


@pytest.mark.parametrize("off, name", [
    ((), ""),
    (("drums",), " (no drums)"),
    (("lead",), " (no lead voice)"),
    (("lead", "backing"), " (no voice)"),
    (("lead", "backing", "guitar"), " (no voice, guitar)"),
    (tuple(n for n in ALL if n not in ("drums", "bass")), " (only drums, bass)"),
    (tuple(n for n in ALL if n not in ("lead", "backing")), " (only voice)"),
])
def test_mix_name(off, name):
    assert mix_name({n: n not in off for n in ALL}) == name


@pytest.mark.parametrize("off, name", [
    (("kick", "snare", "toms", "cymbals"), " (no drums)"),
    (("kick",), " (no kick)"),
    (("lead", "backing", "kick", "snare", "toms", "cymbals"), " (no voice, drums)"),
    (("lead", "backing", "guitar", "other"), " (only drums, bass)"),  # two parts against four
])
def test_all_the_pieces_are_the_drums(off, name):
    stems = ("lead", "backing", "kick", "snare", "toms", "cymbals", "bass", "guitar", "other")
    assert mix_name({s: s not in off for s in stems}) == name


@pytest.mark.parametrize("cents, name", [
    (0, ""), (100, " (+1 semitone)"), (-100, " (-1 semitone)"), (300, " (+3 semitones)"), (-1200, " (-12 semitones)"),
    (215, " (+2.15 semitones)"), (-150, " (-1.5 semitones)"), (15, " (+15 cents)"), (-99, " (-99 cents)"),
])
def test_pitch_name(cents, name):
    assert pitch_name(cents) == name


# --- parts and pieces ------------------------------------------------------------------

PIECED = ("piano", "kick", "snare", "toms", "cymbals", "bass")


def test_the_pieces_follow_their_part():
    assert controls(PIECED) == [("piano", None), ("drums", None), ("kick", 1), ("snare", 1), ("toms", 1),
                                ("cymbals", 1), ("bass", None)]
    assert controls(("piano", "drums", "bass")) == [("piano", None), ("drums", None), ("bass", None)]
    assert controls(("lead", "backing", "drums")) == [("voice", None), ("lead", 0), ("backing", 0), ("drums", None)]


def sounding(off=(), solo=()):
    names, wholes = zip(*controls(PIECED))
    result = heard([n not in off for n in names], [n in solo for n in names], list(wholes))
    return {n for n, r in zip(names, result) if r}


def test_muting_and_soloing_parts_and_pieces():
    everything = {"piano", "drums", "kick", "snare", "toms", "cymbals", "bass"}
    assert sounding() == everything
    assert sounding(off=("kick",)) == everything - {"kick"}
    assert sounding(off=("drums",)) == {"piano", "bass"}
    assert sounding(off=("kick", "snare", "toms", "cymbals")) == {"piano", "bass"}  # the part too is silent
    assert sounding(solo=("drums",)) == everything - {"piano", "bass"}
    assert sounding(solo=("kick", "bass")) == {"drums", "kick", "bass"}
    assert sounding(solo=("drums", "kick")) == {"drums", "kick"}  # the piece wins
    assert sounding(solo=("kick",), off=("drums",)) == set()


def test_the_drums_are_drawn_from_their_pieces():
    data = [np.full((640, 2), v, np.int16) for v in (100, 1000, 20000, 20000, 5, 7)]
    found = all_peaks(StemSet(PIECED, data, 1.0))
    assert found["drums"].max() == 41005 and found["kick"].max() == 1000 and set(found) == {*PIECED, "drums"}
