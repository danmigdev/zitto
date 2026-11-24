"""A picture in memory that numpy fills and Windows copies to a window.

Tk draws every shape under a moving line again, which on a large display takes a quarter of a
second. Copying pixels takes well under a millisecond.
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes

import numpy as np

from .theme import rgb

_user, _gdi = ctypes.windll.user32, ctypes.windll.gdi32
_SRCCOPY = 0x00CC0020


class _Header(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                ("image_size", wintypes.DWORD), ("x_ppm", wintypes.LONG), ("y_ppm", wintypes.LONG),
                ("used", wintypes.DWORD), ("important", wintypes.DWORD)]


def _declare(function, result, *arguments):
    function.restype, function.argtypes = result, arguments
    return function


_GetDC = _declare(_user.GetDC, wintypes.HDC, wintypes.HWND)
_ReleaseDC = _declare(_user.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_FillRect = _declare(_user.FillRect, ctypes.c_int, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH)
_CreateCompatibleDC = _declare(_gdi.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_DeleteDC = _declare(_gdi.DeleteDC, wintypes.BOOL, wintypes.HDC)
_CreateDIBSection = _declare(_gdi.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(_Header),
                             wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
_SelectObject = _declare(_gdi.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_DeleteObject = _declare(_gdi.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_CreateSolidBrush = _declare(_gdi.CreateSolidBrush, wintypes.HBRUSH, wintypes.COLORREF)
_BitBlt = _declare(_gdi.BitBlt, wintypes.BOOL, wintypes.HDC, *[ctypes.c_int] * 4, wintypes.HDC,
                   *[ctypes.c_int] * 2, wintypes.DWORD)
_GdiFlush = _declare(_gdi.GdiFlush, wintypes.BOOL)


_GetClassLong = _declare(_user.GetClassLongPtrW, ctypes.c_size_t, wintypes.HWND, ctypes.c_int)
_SetClassLong = _declare(_user.SetClassLongPtrW, ctypes.c_size_t, wintypes.HWND, ctypes.c_int, ctypes.c_size_t)
_GCL_STYLE, _REDRAW_ON_RESIZE = -26, 0x0003


def keep_content_on_resize(root: tk.Tk, *widgets: tk.Widget) -> None:
    """Stop Windows from repainting whole windows at each change of size, for the window classes
    of these. Tk draws again what needs it by itself: without this it happens twice."""
    for window in (int(root.frame(), 16), *(w.winfo_id() for w in widgets)):
        _SetClassLong(window, _GCL_STYLE, _GetClassLong(window, _GCL_STYLE) & ~_REDRAW_ON_RESIZE)


class Surface:
    forgets = True  # what is shown has to be shown again after something has covered it

    def __init__(self, parent: tk.Widget):
        self.widget = tk.Frame(parent, bg="", takefocus=0)  # drawn from here, not by Tk
        self._window = self.widget.winfo_id()
        self._dc = _CreateCompatibleDC(None)
        self._bitmap = self._previous = None
        self.width = self.height = 0
        self.pixels = np.zeros((0, 0), np.uint32)  # (height, width), 0x00rrggbb

    def resize(self, width: int, height: int) -> None:
        if (width, height) == (self.width, self.height):
            return
        self._free()
        header = _Header(size=ctypes.sizeof(_Header), width=width, height=-height, planes=1, bits=32)
        bits = ctypes.c_void_p()
        bitmap = _CreateDIBSection(self._dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            raise MemoryError(f"No picture of {width}x{height}")
        self._bitmap, self._previous = bitmap, _SelectObject(self._dc, bitmap)
        self.width, self.height = width, height
        memory = (ctypes.c_uint32 * (width * height)).from_address(bits.value)
        self.pixels = np.frombuffer(memory, np.uint32).reshape(height, width)

    def show(self, x: int = 0, width: int | None = None) -> None:
        """Copy the picture, or some of its columns, to the window."""
        x, right = max(x, 0), self.width if width is None else min(x + width, self.width)
        if right <= x:
            return
        _GdiFlush()
        dc = _GetDC(self._window)
        _BitBlt(dc, x, 0, right - x, self.height, self._dc, x, 0, _SRCCOPY)
        _ReleaseDC(self._window, dc)

    def line(self, x: int, width: int, color: str) -> None:
        """A vertical line on the window only: `show` on the same columns takes it away."""
        value = rgb(color)
        brush = _CreateSolidBrush((value & 0xFF) << 16 | value & 0xFF00 | value >> 16)
        dc = _GetDC(self._window)
        _FillRect(dc, ctypes.byref(wintypes.RECT(x, 0, x + width, self.height)), brush)
        _ReleaseDC(self._window, dc)
        _DeleteObject(brush)

    def _free(self) -> None:
        self.pixels = np.zeros((0, 0), np.uint32)  # its memory goes away with the bitmap
        if self._bitmap:
            _SelectObject(self._dc, self._previous)
            _DeleteObject(self._bitmap)
            self._bitmap = None
        self.width = self.height = 0

    def close(self) -> None:
        self._free()
        if self._dc:
            _DeleteDC(self._dc)
            self._dc = None
