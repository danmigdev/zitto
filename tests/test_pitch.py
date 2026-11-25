"""The change of pitch: how exact it is, and that it stays in time with the rest. No sound card."""

import numpy as np
import pytest

from zitto.mixer import FADE, PLAYING, STOPPED, Mixer, mixdown
from zitto.pitch import Reader, Shift, Stem

RATE = 44100


def stem(*channels):
    return np.round(np.stack(channels if len(channels) == 2 else channels * 2, axis=1)).astype(np.int16)


def tone(freq, seconds=2.0, level=8000):
    return stem(level * np.sin(2 * np.pi * freq * np.arange(int(seconds * RATE)) / RATE))


def bursts(every=0.4, seconds=3.0, level=9000):
    """Notes with a sharp attack: where they begin, and the sound."""
    n, rng = int(seconds * RATE), np.random.default_rng(1)
    sound, begin = np.zeros(n), np.arange(0.2, seconds - 0.2, every)
    for at in begin:
        t = np.arange(n) / RATE - at
        sound += np.where(t >= 0, np.exp(-np.clip(t, 0, None) / 0.06), 0) * np.sin(2 * np.pi * rng.uniform(300, 900) * t)
    return (begin * RATE).astype(int), stem(level * sound)


def every(cents, n=1, natural=False):
    return Shift(cents, (True,) * n, (natural,) * n)


def read(stems, shift, block=4096, gains=None, start=0, total=None):
    gains = gains or [1 / 32768] * len(stems)
    reader = Reader([Stem(s) for s in stems], start, shift, gains)
    left, blocks = total or len(stems[0]), []
    while left > 0:
        blocks.append(reader.read(min(block, left), gains))
        left -= len(blocks[-1])
    return np.concatenate(blocks)


def render(mixer, total, block=441):
    blocks = []
    while total > 0:
        blocks.append(mixer.render(min(block, total)))
        total -= len(blocks[-1])
    return np.concatenate(blocks)


def frequency(sound):
    sound = sound[:, 0] * np.hanning(len(sound))
    spectrum = np.abs(np.fft.rfft(sound, 16 * len(sound)))
    return spectrum.argmax() * RATE / (16 * len(sound))


def decibels(a, b):
    return 10 * np.log10(np.mean(np.square(a, dtype=np.float64)) / np.mean(np.square(b, dtype=np.float64)))


def impurity(sound, freq):
    """How much there is besides a steady tone, in dB."""
    t = np.arange(len(sound)) / RATE
    waves = np.stack([np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)], axis=1)
    rest = sound[:, 0] - waves @ np.linalg.lstsq(waves, sound[:, 0], rcond=None)[0]
    return decibels(rest, sound[:, 0])


def biggest_step(out):
    return np.abs(np.diff(out, axis=0)).max()


# --- the sound -----------------------------------------------------------------------

@pytest.mark.parametrize("cents", [-1300, -1200, -500, -100, -7, 3, 50, 200, 700, 1200, 1300])
def test_the_pitch_is_exact_and_the_level_is_the_same(cents):
    out = read([tone(440)], every(cents))
    wanted = 440 * 2 ** (cents / 1200)
    inside = out[10000:-10000]
    assert len(out) == 2 * RATE
    assert 1200 * np.log2(frequency(inside) / wanted) == pytest.approx(0, abs=1)
    assert decibels(inside, tone(440)[10000:-10000] / 32768) == pytest.approx(0, abs=0.1)
    assert impurity(inside, wanted) < -60


@pytest.mark.parametrize("block", [1, 64, 333, 1024, 20000])
def test_the_sound_does_not_depend_on_the_block_size(block):
    parts = [bursts()[1][:60000], tone(220)[:60000]]
    shift = Shift(300, (True, True), (False, True))
    np.testing.assert_allclose(read(parts, shift, block, total=20000), read(parts, shift, 4096, total=20000), atol=1e-5)


@pytest.mark.parametrize("cents", [-500, 300, 900])
def test_notes_begin_when_they_did(cents):
    begin, sound = bursts()
    out = np.abs(read([sound], every(cents))[:, 0])
    for at in begin:
        loud = out[at:at + 2000].max()
        assert out[at - 1500:at - 130].max() < 0.1 * loud  # nothing is heard 3 ms before
        assert out[at:at + 400].max() > 0.5 * loud  # and there it is, within 9 ms


def test_left_and_right_stay_apart():
    out = read([stem(8000 * np.sin(np.arange(60000) / 20), np.zeros(60000))], every(400))
    assert not out[:, 1].any() and out[:, 0].any()


def test_what_would_fold_over_is_not_there():
    """18 kHz an octave up would be 36, and come back as 8."""
    out = read([tone(18000)], every(1200))
    assert decibels(out[10000:-10000], tone(18000)[10000:-10000] / 32768) < -60


def test_stems_that_have_a_sound_in_common_still_add_up():
    whole = bursts()[1]
    share = np.clip(0.5 + 0.4 * np.sin(np.arange(len(whole)) / 9000), 0, 1)[:, None]
    one = np.round(whole * share).astype(np.int16)
    np.testing.assert_allclose(read([one, whole - one], every(400, 2)), read([whole], every(400)), atol=1e-5)


def test_a_silent_stem_costs_no_sound_and_no_trouble():
    out = read([np.zeros((30000, 2), np.int16)], every(300, natural=True))
    assert not out.any()


@pytest.mark.parametrize("start", [-3000, 29000, 50000])
def test_there_is_silence_around_the_song(start):
    out = read([tone(440)[:30000]], every(300), start=start, total=6000)
    assert np.isfinite(out).all()
    assert out.any() == (start < 30000)
    if start == 50000:
        assert not out.any()


def formant(sound):
    """Where the loudest band is, in Hz."""
    power = np.abs(np.fft.rfft(sound[20000:-20000, 0] * np.hanning(len(sound) - 40000))) ** 2
    power = np.convolve(power, np.ones(600), "same")
    return power.argmax() * RATE / (len(sound) - 40000)


def test_a_voice_keeps_its_timbre():
    t = np.arange(3 * RATE) / RATE
    voice = stem(sum(4000 / (1 + ((110 * h - 1000) / 150) ** 2) * np.sin(2 * np.pi * 110 * h * t + h * h)
                     for h in range(1, 40)))
    assert formant(voice) == pytest.approx(1000, rel=0.06)
    assert formant(read([voice], every(700))) == pytest.approx(1500, rel=0.06)
    natural = read([voice], every(700, natural=True))
    assert formant(natural) == pytest.approx(1000, rel=0.12)
    assert decibels(natural[20000:-20000], voice[20000:-20000] / 32768) == pytest.approx(0, abs=1)


# --- in the player -------------------------------------------------------------------

def test_without_a_change_it_is_the_original():
    parts = [tone(200), tone(555)]
    plain, zero = Mixer(parts), Mixer(parts)
    zero.set_shift(every(0, 2))
    none_of_them = Mixer(parts)
    none_of_them.set_shift(Shift(300, (False, False), (False, False)))
    for mixer in (plain, zero, none_of_them):
        mixer.play()
    expected = render(plain, 20000)
    assert (render(zero, 20000) == expected).all() and (render(none_of_them, 20000) == expected).all()


def test_stems_that_do_not_change_stay_exact_and_in_time():
    drums, other = bursts()[1], tone(440)[:len(bursts()[1])]
    other = np.resize(other, drums.shape)
    mixer, only = Mixer([drums, other]), Mixer([other])
    mixer.set_shift(Shift(300, (False, True), (False, False)))
    only.set_shift(every(300))
    mixer.play(), only.play()
    difference = render(mixer, 60000) - render(only, 60000)
    np.testing.assert_allclose(difference[FADE:], drums[FADE:60000] / 32768, atol=1e-5)


def test_what_is_saved_is_what_is_heard():
    parts = [bursts()[1], np.resize(tone(555), bursts()[1].shape), np.resize(tone(90), bursts()[1].shape)]
    levels, shift = [1.0, 0.0, 0.25], Shift(-250, (True, True, False), (False, True, False))
    mixer = Mixer(parts, gain=1.2)
    for i, level in enumerate(levels):
        mixer.set_level(i, level)
    render(mixer, 512)
    mixer.set_shift(shift)
    mixer.play()
    heard = render(mixer, 100000)
    saved = mixdown(parts, 1.2, levels, shift)
    assert saved.shape == parts[0].shape and saved.dtype == np.float32
    np.testing.assert_allclose(saved[FADE:100000], heard[FADE:], atol=1e-5)


def test_switching_a_stem_is_still_immediate():
    mixer = Mixer([tone(300), tone(700)])
    mixer.set_shift(every(400, 2))
    mixer.play()
    render(mixer, 20000, 512)
    mixer.set_level(1, 0)
    out = render(mixer, 20000, 512)
    natural = 8000 / 32768 * 2 * np.pi * (300 + 700) * 2 ** (4 / 12) / RATE
    assert biggest_step(out) < natural * 1.2
    assert impurity(out[512:], 300 * 2 ** (4 / 12)) < -40  # gone within one block, and the other goes on as it was
    mixer.set_level(1, 1)
    out = render(mixer, 8192, 512)
    assert biggest_step(out) < natural * 1.2
    assert decibels(out[512:4608], 2 * tone(300)[:4096] / 32768) > -4  # and back


def test_the_pitch_changes_while_it_plays_without_clicks():
    mixer = Mixer([tone(440, 4)])
    mixer.play()
    out = [render(mixer, 10000)]
    for cents in (200, 205, 210, 1200, 100, 0, -50, -1200, 300):
        mixer.set_shift(every(cents))
        out.append(render(mixer, 12000))
        if cents % 100 == 0:
            measured = 1200 * np.log2(frequency(out[-1][4000:]) / 440)
            assert measured == pytest.approx(cents, abs=6)
    natural = 8000 / 32768 * 2 * np.pi * 880 / RATE
    assert biggest_step(np.concatenate(out)) < natural * 1.3
    assert mixer.state == PLAYING and mixer.position == 10000 + 9 * 12000


def test_the_transport_has_no_clicks_with_another_pitch():
    mixer = Mixer([tone(440, 4), tone(440, 4, 2000)])
    mixer.set_shift(Shift(300, (True, False), (True, False)))
    natural = (8000 * 2 ** (3 / 12) + 2000) / 32768 * 2 * np.pi * 440 / RATE
    mixer.play()
    out = [render(mixer, 3001)]
    for action in (mixer.pause, mixer.pause, mixer.rewind, mixer.stop, mixer.play, lambda: mixer.seek(20000),
                   lambda: mixer.set_loop(2000, 9000), lambda: mixer.set_shift(every(300, 2)),
                   lambda: (mixer.seek(5000), mixer.set_shift(None)), lambda: mixer.set_shift(every(-200, 2)),
                   mixer.stop):
        action()
        out.append(render(mixer, 9001))
    assert biggest_step(np.concatenate(out)) < natural * 1.6
    assert mixer.state == STOPPED


def test_a_loop_repeats_in_time():
    begin, sound = bursts(every=0.25)
    start, end = begin[2] - 1000, begin[6] - 1000  # of one second
    mixer = Mixer([sound])
    mixer.set_shift(every(-300))
    mixer.set_loop(start, end)
    mixer.play()
    out = np.abs(render(mixer, 3 * (end - start))[:, 0])
    for turn in range(3):
        for at in begin[2:6] - start + turn * (end - start):
            loud = out[at:at + 2000].max()
            assert out[at - 900:at - 130].max() < 0.2 * loud and out[at:at + 400].max() > 0.5 * loud
    assert mixer.state == PLAYING and start <= mixer.position < end


def test_the_song_ends_and_starts_again_with_another_pitch():
    mixer = Mixer([tone(440, 0.5)])
    mixer.set_shift(every(500))
    for _ in range(2):
        mixer.play()
        out = render(mixer, 30000)
        assert mixer.state == STOPPED and mixer.position == 0
        assert decibels(out[3000:19000], tone(440)[3000:19000] / 32768) == pytest.approx(0, abs=0.3)
        assert not out[22100:].any()


def test_a_loud_song_is_clipped_not_wrapped():
    mixer = Mixer([tone(200, level=30000), tone(205, level=30000)])
    mixer.set_shift(every(300, 2))
    mixer.play()
    assert np.abs(render(mixer, 30000)).max() == 1.0
