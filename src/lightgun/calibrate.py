"""Calibration: shoot a 3x3 grid on the game monitor, compute and save the homography.

The logic lives in CalibrationSession, a small state machine that both front
ends drive: this module's OpenCV fullscreen CLI and the Qt calibration screen
in the GUI. Feed it each new frame's dot; it records a target when the
trigger fires (or SPACE), or, when no gun is connected, once the dot holds
still for `stable_frames` camera frames.

The homography maps camera pixels -> pixels of the chosen monitor at the
resolution it had during calibration (captured_width/height). The camera
resolution is recorded too (camera_width/height) because the homography is
only valid for that camera mode.

Run:  python -m lightgun.calibrate [--gun p1]
Keys: SPACE accept current target (stand-in trigger)
      r    go back and redo the previous target
      ESC  abort
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import config, guns, screen
from .capture import Camera
from .detect import Blob, detect_frame, pick_blob
from .mapping import compute_homography


def _grid_targets(screen_w: int, screen_h: int, cols: int, rows: int) -> list[tuple[int, int]]:
    """Target screen positions, inset from the edges so dots stay in view."""
    xs = np.linspace(screen_w * 0.1, screen_w * 0.9, cols)
    ys = np.linspace(screen_h * 0.1, screen_h * 0.9, rows)
    return [(int(x), int(y)) for y in ys for x in xs]


class CalibrationSession:
    """Target sequence, dot stability and accept/redo, independent of any UI."""

    def __init__(
        self,
        screen_size: tuple[int, int],
        cols: int = 3,
        rows: int = 3,
        stable_frames: int = 8,
        auto_accept: bool = True,
    ):
        self.screen_w, self.screen_h = screen_size
        self.targets = _grid_targets(self.screen_w, self.screen_h, cols, rows)
        self.need_stable = stable_frames
        self.auto_accept = auto_accept
        self.cam_points: list[tuple[float, float]] = []
        self.screen_points: list[tuple[float, float]] = []
        self.dot: Blob | None = None
        self.stable_count = 0
        self._last_pos: tuple[float, float] | None = None

    @classmethod
    def from_config(
        cls, cfg: dict[str, Any], screen_size: tuple[int, int], auto_accept: bool
    ) -> CalibrationSession:
        cal = cfg["calibration"]
        return cls(screen_size, int(cal["grid_cols"]), int(cal["grid_rows"]),
                   int(cal["stable_frames"]), auto_accept)

    @property
    def index(self) -> int:
        return len(self.cam_points)

    @property
    def done(self) -> bool:
        return self.index >= len(self.targets)

    @property
    def current_target(self) -> tuple[int, int] | None:
        return None if self.done else self.targets[self.index]

    def feed(self, dot: Blob | None) -> bool:
        """Give the session the dot from one *new* camera frame. Returns True if
        that auto-accepted the current target."""
        self.dot = dot
        if dot is None:
            self.stable_count = 0
            self._last_pos = None
            return False
        pos = (dot.x, dot.y)
        last = self._last_pos
        if last and abs(pos[0] - last[0]) < 2 and abs(pos[1] - last[1]) < 2:
            self.stable_count += 1
        else:
            self.stable_count = 0
        self._last_pos = pos
        if self.auto_accept and self.stable_count >= self.need_stable:
            return self.fire()
        return False

    def fire(self) -> bool:
        """Record the current dot against the current target (trigger / SPACE)."""
        if self.dot is None or self.done:
            return False
        tx, ty = self.targets[self.index]
        self.cam_points.append((self.dot.x, self.dot.y))
        self.screen_points.append((float(tx), float(ty)))
        print(f"  target {self.index}: cam=({self.dot.x:.1f},{self.dot.y:.1f}) "
              f"screen=({tx},{ty})")
        self._reset_stability()
        return True

    def redo(self) -> None:
        """Step back to the previous target."""
        if self.cam_points:
            self.cam_points.pop()
            self.screen_points.pop()
        self._reset_stability()

    def _reset_stability(self) -> None:
        self.stable_count = 0
        self._last_pos = None

    def status(self) -> str:
        s = f"Target {min(self.index + 1, len(self.targets))}/{len(self.targets)}   "
        if self.dot is None:
            return s + "dot: (not detected)"
        if not self.auto_accept:
            return s + "dot: tracking - shoot!"
        return s + f"dot: stable {self.stable_count}/{self.need_stable}"

    def result(self, camera_size: tuple[int, int]) -> dict[str, Any]:
        H, err = compute_homography(self.cam_points, self.screen_points)
        return {
            "homography": H.tolist(),
            "captured_width": int(self.screen_w),
            "captured_height": int(self.screen_h),
            "camera_width": int(camera_size[0]),
            "camera_height": int(camera_size[1]),
            "reprojection_error": round(err, 3),
        }


_SAVE_KEYS = ("homography", "captured_width", "captured_height", "camera_width", "camera_height")


def save_result(path: str | Path | None, result: dict[str, Any]) -> Path:
    """Write a calibration result into [calibration], keeping the file's comments."""
    p = config.save_values(path, {"calibration": {k: result[k] for k in _SAVE_KEYS}})
    print(f"Wrote calibration into {p}")
    return p


def apply_result(cfg: dict[str, Any], result: dict[str, Any]) -> None:
    """Update an in-memory config with a calibration result."""
    cfg["calibration"].update({k: result[k] for k in _SAVE_KEYS})


def draw_target(canvas: np.ndarray, pt: tuple[int, int], live: bool) -> None:
    color = (0, 0, 255) if live else (80, 80, 80)
    cv2.circle(canvas, pt, 24, color, 2)
    cv2.circle(canvas, pt, 3, color, -1)
    cv2.line(canvas, (pt[0] - 34, pt[1]), (pt[0] - 10, pt[1]), color, 1)
    cv2.line(canvas, (pt[0] + 10, pt[1]), (pt[0] + 34, pt[1]), color, 1)
    cv2.line(canvas, (pt[0], pt[1] - 34), (pt[0], pt[1] - 10), color, 1)
    cv2.line(canvas, (pt[0], pt[1] + 10), (pt[0], pt[1] + 34), color, 1)


def run_calibration(cfg: dict[str, Any], gun_filter: str | None = None) -> dict[str, Any] | None:
    """OpenCV fullscreen calibration on the configured monitor (CLI front end)."""
    monitor = screen.selected_monitor(cfg)

    # Trigger presses arrive on serial threads; the UI loop consumes them.
    fired = threading.Event()

    def on_event(gid: str, msg: dict[str, Any], _t: float) -> None:
        if msg.get("e") == "trigger" and msg.get("s") == 1:
            if gun_filter is None or gid == gun_filter:
                fired.set()

    links = guns.connect(cfg, on_event)
    for link in links.values():
        link.enable(cfg, force_always=True)
    if links:
        print("Gun connected: shoot each target (auto-accept disabled).")
    session = CalibrationSession.from_config(cfg, monitor.size, auto_accept=not links)

    cam = Camera(cfg)
    cam.start()
    cam_size = (cam.stats.applied.get("width", 0), cam.stats.applied.get("height", 0))
    if not cam.exposure_locked():
        print("WARNING: exposure not locked; calibration may be noisy.")

    win = "calibration"
    cv2.namedWindow(win, cv2.WND_PROP_FULLSCREEN)
    cv2.moveWindow(win, monitor.x, monitor.y)  # fullscreen lands on this monitor
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    last_index = -1

    try:
        while not session.done:
            frame = cam.read()
            # Only feed new frames; the UI loop runs much faster than the camera.
            if frame is not None and frame.index != last_index:
                last_index = frame.index
                session.feed(pick_blob(detect_frame(frame.image, cfg), gun_filter))

            canvas = np.zeros((monitor.height, monitor.width, 3), dtype=np.uint8)
            for j, t in enumerate(session.targets):
                draw_target(canvas, t, live=(j == session.index))
            cv2.putText(canvas, session.status(), (40, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (200, 200, 200), 2)
            cv2.putText(canvas, "trigger/SPACE=accept  r=redo previous  ESC=abort",
                        (40, monitor.height - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (120, 120, 120), 1)
            cv2.imshow(win, canvas)

            k = cv2.waitKey(1) & 0xFF
            if k == 27:  # ESC
                print("Calibration aborted.")
                return None
            if k == ord("r"):
                session.redo()
                fired.clear()
                continue
            if k == ord(" ") or fired.is_set():
                session.fire()
            fired.clear()
    finally:
        cam.stop()
        guns.close_all(links)
        cv2.destroyAllWindows()

    result = session.result(cam_size)
    err = result["reprojection_error"]
    print(f"\nHomography computed. Mean reprojection error: {err:.2f} px")
    if err > 8.0:
        print("  (High error — consider redoing; aim was likely inconsistent.)")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Light gun screen calibration.")
    ap.add_argument("--config", default=None, help="Config TOML (default: the user config).")
    ap.add_argument("--gun", default=None, help="Only use this gun's color (e.g. p1).")
    args = ap.parse_args()

    cfg = config.load(args.config)
    result = run_calibration(cfg, gun_filter=args.gun)
    if result is not None:
        save_result(args.config, result)


if __name__ == "__main__":
    main()
