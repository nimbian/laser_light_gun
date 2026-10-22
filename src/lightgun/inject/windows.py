"""Windows pointer injection via SendInput: absolute move + left button.

Absolute coordinates are normalized 0..65535 across the primary monitor, which
is why the runtime maps into primary-monitor pixels (see screen.py).

SendInput is subject to UIPI: if the game runs elevated, this process must too,
or the input is silently dropped.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR
    ]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT is the largest member, so it alone sizes the union correctly.
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def to_absolute(x: float, y: float, screen_w: int, screen_h: int) -> tuple[int, int]:
    """Screen pixels -> SendInput's 0..65535 normalized absolute coordinates."""
    ax = round(x * 65535 / max(screen_w - 1, 1))
    ay = round(y * 65535 / max(screen_h - 1, 1))
    return min(max(ax, 0), 65535), min(max(ay, 0), 65535)


class WindowsInjector:
    def __init__(self, screen_size: tuple[int, int]):
        self.screen_w, self.screen_h = screen_size
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._send = self._user32.SendInput
        self._send.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._send.restype = wintypes.UINT

    def _emit(self, flags: int, x: float | None = None, y: float | None = None) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        if x is not None and y is not None:
            inp.mi.dx, inp.mi.dy = to_absolute(x, y, self.screen_w, self.screen_h)
        inp.mi.dwFlags = flags
        if self._send(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def move(self, x: float, y: float) -> None:
        self._emit(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y)

    def button(self, down: bool) -> None:
        self._emit(MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)
