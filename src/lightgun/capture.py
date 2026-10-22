"""Threaded camera capture with manual exposure/focus control.

The single most important thing this module does is *lock exposure and focus*.
In a crushed-exposure scene the image is nearly black; autofocus will hunt and
auto-exposure will brighten the frame back up, both of which wreck detection.
OpenCV's control of these properties is backend- and camera-dependent and
often silently ignored, so we set them, read them back, and report what
actually took effect instead of assuming success.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

# OpenCV backend name -> cv2 capture API constant.
_BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
    "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY),
}


@dataclass
class Frame:
    """One captured frame plus the time it was grabbed (perf_counter seconds)."""

    image: np.ndarray
    timestamp: float
    index: int


@dataclass
class CaptureStats:
    frames: int = 0
    measured_fps: float = 0.0
    applied: dict[str, Any] = field(default_factory=dict)


class Camera:
    """Background-threaded camera. Always holds the latest frame; never queues.

    A light gun cares about the *newest* position, not a backlog, so the grab
    loop overwrites a single slot. Readers get the most recent frame or None.
    """

    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg
        self._cap: cv2.VideoCapture | None = None
        self._latest: Frame | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._index = 0
        self._fps_window: deque[float] = deque()
        # Property changes requested while the grab thread runs. VideoCapture
        # isn't thread-safe, so the grab loop applies these between reads.
        self._pending: list[tuple[int, float]] = []
        self.stats = CaptureStats()

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        c = self._cfg["camera"]
        backend = _BACKENDS.get(str(c.get("backend", "any")).lower(), cv2.CAP_ANY)
        cap = cv2.VideoCapture(int(c["index"]), backend)
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open camera index {c['index']} with backend {c.get('backend')}"
            )
        self._cap = cap
        self._configure(c)

    def _configure(self, c: dict[str, Any]) -> None:
        cap = self._cap
        assert cap is not None

        # FOURCC first: forcing MJPG unlocks the high-rate modes on UVC cams.
        if "fourcc" in c:
            fourcc = cv2.VideoWriter_fourcc(*str(c["fourcc"]))
            cap.set(cv2.CAP_PROP_FOURCC, fourcc)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(c["width"]))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(c["height"]))
        cap.set(cv2.CAP_PROP_FPS, int(c["fps"]))

        # Autofocus off, then fixed focus.
        if "autofocus" in c:
            cap.set(cv2.CAP_PROP_AUTOFOCUS, float(c["autofocus"]))
        if "focus" in c:
            cap.set(cv2.CAP_PROP_FOCUS, float(c["focus"]))

        # Manual exposure. The OpenCV convention for AUTO_EXPOSURE differs across
        # builds: some want 0.25 (manual)/0.75 (auto), some want 1/3. Try the
        # configured value; if exposure doesn't take, try the alternate mapping.
        self._apply_manual_exposure(c)

        if "gain" in c:
            cap.set(cv2.CAP_PROP_GAIN, float(c["gain"]))

        self.stats.applied = self._readback(c)

    def _apply_manual_exposure(self, c: dict[str, Any]) -> None:
        cap = self._cap
        assert cap is not None
        want_exposure = float(c.get("exposure", -7))

        # Only values that mean *manual* in some convention (0.25 old-style,
        # 1 = V4L2 manual). 0.75 and 3 mean auto and must never be tried here.
        configured = float(c.get("auto_exposure", 0.25))
        candidates = [configured] + [v for v in (0.25, 1.0) if v != configured]
        for auto_val in candidates:
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, auto_val)
            cap.set(cv2.CAP_PROP_EXPOSURE, want_exposure)
            got = cap.get(cv2.CAP_PROP_EXPOSURE)
            # Accept if the readback is close to what we asked for.
            if abs(got - want_exposure) < max(0.5, abs(want_exposure) * 0.25):
                return
        # Nothing confirmed. Re-apply the configured manual mode rather than
        # leaving the last attempt in place; _readback reports the truth.
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, configured)
        cap.set(cv2.CAP_PROP_EXPOSURE, want_exposure)

    def _readback(self, c: dict[str, Any]) -> dict[str, Any]:
        cap = self._cap
        assert cap is not None
        return {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": cap.get(cv2.CAP_PROP_FPS),
            "exposure": cap.get(cv2.CAP_PROP_EXPOSURE),
            "exposure_requested": float(c.get("exposure", -7)),
            "autofocus": cap.get(cv2.CAP_PROP_AUTOFOCUS),
            "auto_exposure": cap.get(cv2.CAP_PROP_AUTO_EXPOSURE),
            "gain": cap.get(cv2.CAP_PROP_GAIN),
        }

    def exposure_locked(self) -> bool:
        """True if the camera reported back roughly the exposure we asked for."""
        a = self.stats.applied
        if not a:
            return False
        return abs(a["exposure"] - a["exposure_requested"]) < max(
            0.5, abs(a["exposure_requested"]) * 0.25
        )

    def set_exposure(self, value: float) -> None:
        """Request a new exposure; applied by the grab thread before its next read."""
        with self._lock:
            self._pending.append((cv2.CAP_PROP_EXPOSURE, float(value)))
        self.stats.applied["exposure_requested"] = float(value)

    # -- threaded grabbing -------------------------------------------------

    def start(self) -> None:
        if self._cap is None:
            self.open()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        cap = self._cap
        assert cap is not None
        while self._running:
            with self._lock:
                pending, self._pending = self._pending, []
            for prop, value in pending:
                cap.set(prop, value)
                if prop == cv2.CAP_PROP_EXPOSURE:
                    self.stats.applied["exposure"] = cap.get(prop)
            ok, img = cap.read()
            if not ok:
                time.sleep(0.001)
                continue
            now = time.perf_counter()
            self._index += 1
            frame = Frame(image=img, timestamp=now, index=self._index)
            with self._lock:
                self._latest = frame
            self._tick_fps(now)

    def _tick_fps(self, now: float) -> None:
        self._fps_window.append(now)
        cutoff = now - 1.0
        while self._fps_window and self._fps_window[0] < cutoff:
            self._fps_window.popleft()
        self.stats.frames = self._index
        self.stats.measured_fps = len(self._fps_window)

    def read(self) -> Frame | None:
        """Return the most recent frame, or None if none captured yet."""
        with self._lock:
            return self._latest

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> Camera:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
