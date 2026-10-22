"""Windows pointer injection via SendInput: absolute move + left button.

Positions arrive in the game monitor's own pixels (what the homography maps
to). They're offset by the monitor's place on the virtual desktop and
normalized 0..65535 across the whole virtual desktop (MOUSEEVENTF_VIRTUALDESK),
so any monitor works, not just the primary.

SendInput is subject to UIPI: if the game runs elevated, this process must too,
or the input is silently dropped.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from ..screen import Monitor

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_VIRTUALDESK = 0x4000
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


def to_absolute(
    x: float, y: float, monitor: Monitor, desktop: tuple[int, int, int, int]
) -> tuple[int, int]:
    """Monitor pixels -> SendInput's 0..65535 virtual-desktop coordinates."""
    vx, vy, vw, vh = desktop
    px = monitor.x + min(max(x, 0.0), monitor.width - 1)
    py = monitor.y + min(max(y, 0.0), monitor.height - 1)
    ax = round((px - vx) * 65535 / max(vw - 1, 1))
    ay = round((py - vy) * 65535 / max(vh - 1, 1))
    return min(max(ax, 0), 65535), min(max(ay, 0), 65535)


class WindowsInjector:
    def __init__(self, monitor: Monitor, desktop: tuple[int, int, int, int]):
        self.monitor = monitor
        self.desktop = desktop
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._send = self._user32.SendInput
        self._send.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._send.restype = wintypes.UINT

    def _emit(self, flags: int, x: float | None = None, y: float | None = None) -> None:
        inp = INPUT(type=INPUT_MOUSE)
        if x is not None and y is not None:
            inp.mi.dx, inp.mi.dy = to_absolute(x, y, self.monitor, self.desktop)
        inp.mi.dwFlags = flags
        if self._send(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def move(self, x: float, y: float) -> None:
        self._emit(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, x, y)

    def button(self, down: bool) -> None:
        self._emit(MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)
