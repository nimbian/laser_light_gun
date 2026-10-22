"""Primary-display size, queried the same way by calibration and the runtime.

Both must agree: calibration records targets in these pixels, and SendInput's
absolute coordinates are normalized over the primary monitor.
"""

from __future__ import annotations

_dpi_aware = False


def primary_size() -> tuple[int, int]:
    """Physical pixel size of the primary monitor; 1920x1080 off Windows."""
    global _dpi_aware
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not _dpi_aware:
            # Without this, Windows reports scaled (e.g. 150%) sizes and the
            # homography would target the wrong pixels.
            user32.SetProcessDPIAware()
            _dpi_aware = True
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return 1920, 1080
