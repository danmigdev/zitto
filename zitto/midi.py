"""MIDI control of the transport: message decoding, bindings, input port."""

from __future__ import annotations

import ctypes
import re
import sys
import time
from typing import Callable, Sequence

ACTIONS = ("play", "pause", "stop", "rewind")

# What identifies a button: ("note", channel, number), ("cc", channel, number),
# ("program", channel, number), ("mmc", command) or ("realtime", status).
Trigger = tuple

# Standard transport messages, understood without any setup.
STANDARD: dict[Trigger, tuple[str, ...]] = {
    ("mmc", 0x01): ("stop",),
    ("mmc", 0x02): ("play",),
    ("mmc", 0x03): ("play",),  # deferred play
    ("mmc", 0x05): ("rewind",),
    ("mmc", 0x09): ("pause",),
    ("realtime", 0xFA): ("rewind", "play"),  # start: from the beginning
    ("realtime", 0xFB): ("play",),  # continue
    ("realtime", 0xFC): ("stop",),
}
_MMC_NAMES = {1: "Stop", 2: "Play", 3: "Deferred Play", 4: "Fast Forward", 5: "Rewind", 6: "Record", 9: "Pause"}
_REALTIME_NAMES = {0xFA: "Start", 0xFB: "Continue", 0xFC: "Stop"}


def trigger(message: Sequence[int]) -> Trigger | None:
    """The button press in a raw message, or None (releases, clock, pitch bend...)."""
    if not message:
        return None
    status = message[0]
    if status in _REALTIME_NAMES:
        return ("realtime", status)
    if status == 0xF0:  # F0 7F <device> 06 <command> F7
        if len(message) >= 6 and message[1] == 0x7F and message[3] == 0x06:
            return ("mmc", message[4])
        return None
    kind, channel = status & 0xF0, status & 0x0F
    if kind == 0x90 and len(message) >= 3 and message[2] > 0:
        return ("note", channel, message[1])
    if kind == 0xB0 and len(message) >= 3 and message[2] >= 64:
        return ("cc", channel, message[1])
    if kind == 0xC0 and len(message) >= 2:
        return ("program", channel, message[1])
    return None


def describe(t: Trigger) -> str:
    kind, *rest = t
    if kind == "mmc":
        return f"MMC {_MMC_NAMES.get(rest[0], rest[0])}"
    if kind == "realtime":
        return f"MIDI {_REALTIME_NAMES.get(rest[0], rest[0])}"
    channel, number = rest
    return f"{ {'note': 'Note', 'cc': 'CC', 'program': 'Program'}[kind] } {number}, channel {channel + 1}"


class Bindings:
    """One learned trigger per action, on top of the standard messages."""

    def __init__(self, saved: dict | None = None):
        self._learned: dict[str, Trigger] = {}
        for action, value in (saved or {}).items():
            if action in ACTIONS and _valid(value):
                self._learned[action] = tuple(value)

    def get(self, action: str) -> Trigger | None:
        return self._learned.get(action)

    def learn(self, action: str, t: Trigger) -> None:
        """Bind a trigger; a trigger drives one action only."""
        self._learned = {a: old for a, old in self._learned.items() if old != t and a != action}
        self._learned[action] = t

    def clear(self, action: str) -> None:
        self._learned = {a: t for a, t in self._learned.items() if a != action}

    def actions(self, t: Trigger) -> tuple[str, ...]:
        for action, learned in self._learned.items():
            if learned == t:
                return (action,)
        return STANDARD.get(t, ())

    def to_json(self) -> dict:
        return {action: list(t) for action, t in self._learned.items()}


def _valid(value) -> bool:
    if not isinstance(value, (list, tuple)) or not value:
        return False
    sizes = {"note": 3, "cc": 3, "program": 3, "mmc": 2, "realtime": 2}
    return len(value) == sizes.get(value[0]) and all(isinstance(v, int) for v in value[1:])


class MidiInput:
    """One input port. `on_message` is called on the MIDI thread with the raw message."""

    def __init__(self, on_message: Callable[[Sequence[int]], None]):
        import rtmidi

        if sys.platform == "darwin":
            _need_core_midi()
        self._rtmidi = rtmidi
        self._on_message = on_message
        self._probe = rtmidi.MidiIn()
        self._port = None
        self.name: str | None = None

    def ports(self) -> list[str]:
        return list(self._probe.get_ports())

    def find(self, name: str) -> str | None:
        """The port called `name`, also when its trailing number has changed."""
        ports = self.ports()
        if name in ports:
            return name
        return next((p for p in ports if _base(p) == _base(name)), None)

    def open(self, name: str) -> None:
        """Open a port. Raises OSError when it is gone or used by another program."""
        self.close()
        ports = self.ports()
        if name not in ports:
            raise OSError(f"MIDI port not found: {name}")
        port = self._rtmidi.MidiIn()
        try:
            port.ignore_types(sysex=False, timing=True, active_sense=True)  # MMC is a sysex
            port.set_callback(lambda event, data=None: self._on_message(event[0]))
            port.open_port(ports.index(name))
        except Exception as e:
            port.delete()
            raise OSError(f"Cannot open {name}. Another program may be using it.") from e
        self._port, self.name = port, name

    def close(self) -> None:
        if self._port is not None:
            self._port.close_port()
            self._port.delete()
        self._port, self.name = None, None


def _need_core_midi() -> None:
    """Raise OSError when the MIDI service of the Mac does not start: rtmidi would end the whole program.

    Once it has started for this process, it stays.
    """
    frameworks = "/System/Library/Frameworks"
    text = ctypes.CDLL(f"{frameworks}/CoreFoundation.framework/CoreFoundation").CFStringCreateWithCString
    text.restype, text.argtypes = ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    create = ctypes.CDLL(f"{frameworks}/CoreMIDI.framework/CoreMIDI").MIDIClientCreate
    create.restype, create.argtypes = ctypes.c_int32, [ctypes.c_void_p] * 3 + [ctypes.POINTER(ctypes.c_uint32)]
    name, client = text(None, b"zitto", 0x0600), ctypes.c_uint32()  # 0x0600: ASCII
    for wait in (0, 0.3, 0.7):  # it can be closing, after the program that was using it
        time.sleep(wait)
        status = create(name, None, None, ctypes.byref(client))
        if status == 0:
            return
    raise OSError(f"The MIDI service of macOS did not start ({status}).")


def _base(name: str) -> str:
    return re.sub(r" \d+$", "", name)
