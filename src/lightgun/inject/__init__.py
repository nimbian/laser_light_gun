"""Pointer injection backends. Phase 2: Windows SendInput (one shared cursor).

Phase 4 moves pointer reporting into each Pico as a USB HID absolute device so
two players get separate pointers; that backend will live alongside this one.
"""

from __future__ import annotations

from typing import Any, Protocol


class Injector(Protocol):
    def move(self, x: float, y: float) -> None: ...
    def button(self, down: bool) -> None: ...


class DryRunInjector:
    """Logs instead of injecting, for testing the pipeline without moving the cursor."""

    def __init__(self) -> None:
        self._last: tuple[float, float] | None = None

    def move(self, x: float, y: float) -> None:
        self._last = (x, y)

    def button(self, down: bool) -> None:
        pos = f"({self._last[0]:.0f},{self._last[1]:.0f})" if self._last else "(no position)"
        print(f"[inject] {'DOWN' if down else 'UP  '} at {pos}")


def make_injector(cfg: dict[str, Any], screen_size: tuple[int, int], dry_run: bool) -> Injector:
    if dry_run:
        return DryRunInjector()
    backend = str(cfg.get("injection", {}).get("backend", "windows")).lower()
    if backend == "windows":
        from .windows import WindowsInjector

        return WindowsInjector(screen_size)
    raise ValueError(f"Unknown injection backend {backend!r}")
