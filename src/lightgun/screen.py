"""Monitor geometry in physical pixels, shared by calibration and the runtime.

Both must agree: calibration records targets in the chosen monitor's pixels,
the homography maps into those pixels, and injection converts them to
virtual-desktop coordinates for SendInput. Off Windows (tests, CI) a single
1920x1080 primary monitor is reported.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

_dpi_aware = False


@dataclass(frozen=True)
class Monitor:
    name: str          # Windows device name, e.g. \\.\DISPLAY2
    x: int             # top-left in virtual-desktop pixels
    y: int
    width: int
    height: int
    primary: bool

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def label(self) -> str:
        tag = " (primary)" if self.primary else ""
        return f"{self.name}  {self.width}x{self.height} at ({self.x},{self.y}){tag}"


_FALLBACK = Monitor(name="primary", x=0, y=0, width=1920, height=1080, primary=True)


def make_dpi_aware() -> None:
    """Report physical pixels on every monitor, not DPI-scaled ones."""
    global _dpi_aware
    if _dpi_aware or sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    try:
        # PER_MONITOR_AWARE_V2; Qt asks for the same, so the two never disagree.
        if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            user32.SetProcessDPIAware()
    except AttributeError:
        user32.SetProcessDPIAware()
    _dpi_aware = True


def monitors() -> list[Monitor]:
    """All attached monitors, primary first."""
    if sys.platform != "win32":
        return [_FALLBACK]
    import ctypes
    from ctypes import wintypes

    make_dpi_aware()

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32 = ctypes.windll.user32
    found: list[Monitor] = []
    proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def on_monitor(hmon, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append(Monitor(
                name=info.szDevice, x=r.left, y=r.top,
                width=r.right - r.left, height=r.bottom - r.top,
                primary=bool(info.dwFlags & 1),  # MONITORINFOF_PRIMARY
            ))
        return True

    user32.EnumDisplayMonitors(None, None, proc_type(on_monitor), 0)
    found.sort(key=lambda m: (not m.primary, m.x, m.y))
    return found or [_FALLBACK]


def virtual_desktop() -> tuple[int, int, int, int]:
    """(x, y, width, height) of the box spanning all monitors."""
    if sys.platform != "win32":
        return 0, 0, _FALLBACK.width, _FALLBACK.height
    import ctypes

    make_dpi_aware()
    gsm = ctypes.windll.user32.GetSystemMetrics
    # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
    return gsm(76), gsm(77), gsm(78), gsm(79)


def find_monitor(choice: str, available: list[Monitor]) -> Monitor:
    """Resolve a [display] monitor setting; unknown names fall back to primary."""
    for m in available:
        if choice != "primary" and m.name == choice:
            return m
    if choice != "primary":
        print(f"[screen] monitor {choice!r} not found; using the primary display")
    return next((m for m in available if m.primary), available[0])


def selected_monitor(cfg: dict[str, Any]) -> Monitor:
    """The game monitor named by [display] monitor."""
    return find_monitor(str(cfg.get("display", {}).get("monitor", "primary")), monitors())
