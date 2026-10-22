"""Fullscreen calibration: shoot a 3x3 grid, compute and save the homography.

Flow: a fullscreen window shows one target at a time at known screen pixels.
Aim at it and fire. With a gun connected over serial, the trigger records the
current dot position (the player *shoots* each target). Without a gun, the
target auto-accepts once the dot holds still for `stable_frames` camera
frames, or SPACE accepts it. After all targets, we compute the homography,
report reprojection error, and write it back into the config TOML.

The homography maps camera pixels -> screen pixels of the primary monitor at
the resolution it had during calibration (captured_width/height). The camera
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
from .detect import detect_frame, pick_blob
from .mapping import compute_homography


def _grid_targets(screen_w: int, screen_h: int, cols: int, rows: int) -> list[tuple[int, int]]:
    """Target screen positions, inset from the edges so dots stay in view."""
    xs = np.linspace(screen_w * 0.1, screen_w * 0.9, cols)
    ys = np.linspace(screen_h * 0.1, screen_h * 0.9, rows)
    return [(int(x), int(y)) for y in ys for x in xs]


def _draw_target(canvas: np.ndarray, pt: tuple[int, int], live: bool) -> None:
    color = (0, 0, 255) if live else (80, 80, 80)
    cv2.circle(canvas, pt, 24, color, 2)
    cv2.circle(canvas, pt, 3, color, -1)
    cv2.line(canvas, (pt[0] - 34, pt[1]), (pt[0] - 10, pt[1]), color, 1)
    cv2.line(canvas, (pt[0] + 10, pt[1]), (pt[0] + 34, pt[1]), color, 1)
    cv2.line(canvas, (pt[0], pt[1] - 34), (pt[0], pt[1] - 10), color, 1)
    cv2.line(canvas, (pt[0], pt[1] + 10), (pt[0], pt[1] + 34), color, 1)


def run_calibration(cfg: dict[str, Any], gun_filter: str | None = None) -> dict[str, Any] | None:
    cal = cfg["calibration"]
    cols, rows = int(cal["grid_cols"]), int(cal["grid_rows"])
    need_stable = int(cal["stable_frames"])
    screen_w, screen_h = screen.primary_size()
    targets = _grid_targets(screen_w, screen_h, cols, rows)

    # Trigger presses arrive on serial threads; the UI loop consumes them.
    fired = threading.Event()

    def on_event(gid: str, msg: dict[str, Any], _t: float) -> None:
        if msg.get("e") == "trigger" and msg.get("s") == 1:
            if gun_filter is None or gid == gun_filter:
                fired.set()

    links = guns.connect(cfg, on_event)
    for link in links.values():
        link.enable(cfg, force_always=True)
    use_trigger = bool(links)
    if use_trigger:
        print("Gun connected: shoot each target (auto-accept disabled).")

    cam = Camera(cfg)
    cam.start()
    cam_w = cam.stats.applied.get("width", 0)
    cam_h = cam.stats.applied.get("height", 0)
    if not cam.exposure_locked():
        print("WARNING: exposure not locked; calibration may be noisy.")

    win = "calibration"
    cv2.namedWindow(win, cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    recorded_cam: list[tuple[float, float]] = []
    recorded_screen: list[tuple[float, float]] = []

    i = 0
    stable_count = 0
    last_pos: tuple[float, float] | None = None
    last_index = -1
    cur = None

    try:
        while i < len(targets):
            frame = cam.read()
            # Only advance detection/stability on frames we haven't seen; the UI
            # loop runs much faster than the camera.
            if frame is not None and frame.index != last_index:
                last_index = frame.index
                cur = pick_blob(detect_frame(frame.image, cfg), gun_filter)
                if cur is not None:
                    pos = (cur.x, cur.y)
                    if (last_pos and abs(pos[0] - last_pos[0]) < 2
                            and abs(pos[1] - last_pos[1]) < 2):
                        stable_count += 1
                    else:
                        stable_count = 0
                    last_pos = pos
                else:
                    stable_count = 0
                    last_pos = None

            canvas = np.zeros((screen_h, screen_w, 3), dtype=np.uint8)
            for j, t in enumerate(targets):
                _draw_target(canvas, t, live=(j == i))
            status = f"Target {i + 1}/{len(targets)}   "
            if cur is None:
                status += "dot: (not detected)"
            elif use_trigger:
                status += "dot: tracking - shoot!"
            else:
                status += f"dot: stable {stable_count}/{need_stable}"
            cv2.putText(canvas, status, (40, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (200, 200, 200), 2)
            cv2.putText(canvas, "trigger/SPACE=accept  r=redo previous  ESC=abort",
                        (40, screen_h - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (120, 120, 120), 1)
            cv2.imshow(win, canvas)

            k = cv2.waitKey(1) & 0xFF
            if k == 27:  # ESC
                print("Calibration aborted.")
                return None
            if k == ord("r"):
                if recorded_cam:
                    recorded_cam.pop()
                    recorded_screen.pop()
                    i -= 1
                    print(f"  redoing target {i + 1}")
                stable_count = 0
                last_pos = None
                fired.clear()
                continue

            auto_ready = not use_trigger and cur is not None and stable_count >= need_stable
            accept = k == ord(" ") or fired.is_set() or auto_ready
            fired.clear()
            if accept and cur is not None:
                recorded_cam.append((cur.x, cur.y))
                recorded_screen.append((float(targets[i][0]), float(targets[i][1])))
                print(f"  target {i + 1}: cam=({cur.x:.1f},{cur.y:.1f}) "
                      f"screen={targets[i]}")
                i += 1
                stable_count = 0
                last_pos = None
    finally:
        cam.stop()
        guns.close_all(links)
        cv2.destroyAllWindows()

    H, err = compute_homography(recorded_cam, recorded_screen)
    print(f"\nHomography computed. Mean reprojection error: {err:.2f} px")
    if err > 8.0:
        print("  (High error — consider redoing; aim was likely inconsistent.)")

    return {
        "homography": H.tolist(),
        "captured_width": int(screen_w),
        "captured_height": int(screen_h),
        "camera_width": int(cam_w),
        "camera_height": int(cam_h),
        "reprojection_error": round(err, 3),
    }


_WRITE_KEYS = ("homography", "captured_width", "captured_height", "camera_width", "camera_height")


def _write_back(config_path: Path, result: dict[str, Any]) -> None:
    """Patch the [calibration] values in the TOML, preserving the rest.

    Simple line-based rewrite so we don't need a TOML writer dependency. Keys
    missing from the file are appended to the end of the [calibration] table.
    """
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    in_cal = False
    cal_end = None  # index in `out` just past the last [calibration] line
    replacements = {k: f"{k} = {result[k]!r}" for k in _WRITE_KEYS}
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_cal = stripped == "[calibration]"
        if in_cal:
            key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
            if key in replacements:
                out.append(replacements[key])
                seen.add(key)
            else:
                out.append(line)
            if stripped:
                cal_end = len(out)
            continue
        out.append(line)
    missing = [replacements[k] for k in _WRITE_KEYS if k not in seen]
    if missing:
        if cal_end is None:
            out += ["", "[calibration]"]
            cal_end = len(out)
        out[cal_end:cal_end] = missing
    config_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote calibration into {config_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Light gun screen calibration.")
    ap.add_argument("--config", default=None, help="Path to config TOML.")
    ap.add_argument("--gun", default=None, help="Only use this gun's color (e.g. p1).")
    args = ap.parse_args()

    cfg_path = Path(args.config) if args.config else config.DEFAULT_CONFIG_PATH
    cfg = config.load(cfg_path)
    result = run_calibration(cfg, gun_filter=args.gun)
    if result is not None:
        _write_back(cfg_path, result)


if __name__ == "__main__":
    main()
