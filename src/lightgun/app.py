"""Phase 2 runtime: track one gun's dot, drive the Windows cursor, click on trigger.

The camera loop maps each new frame's dot to screen pixels and moves the
cursor. Trigger events arrive over serial on another thread and are paired
with the dot's position: press -> move there + button down, release -> button
up (held at least click_hold_ms so short pulls still register). In the default
"always" laser mode that's the latest tracked position; in "on_fire" mode it's
the first dot after the pull (see [laser] in the config). A click is never
inferred from the dot appearing or disappearing.

Windows has one cursor, so only one gun drives it (--gun, default p1).
Two players arrive in Phase 4 via per-Pico HID pointers.

Run:  python -m lightgun.app [--gun p1] [--dry-run]
      (or the `lightgun` console script). Ctrl+C to quit.
"""

from __future__ import annotations

import argparse
import threading
import time
from typing import Any

from . import config, guns, screen
from .capture import Camera
from .detect import detect_frame, pick_blob
from .inject import Injector, make_injector
from .mapping import Mapper, ScreenPoint


class TriggerHandler:
    """Turns one gun's trigger events into button presses at the dot's position.

    always mode: press -> click at the latest tracked position immediately.
    on_fire mode: the gun lights its laser on the press, so the press only arms
    a shot; the click lands at the first dot from a frame grabbed after the
    press (within window_s), else the shot is dropped. Either way the click is
    caused by the trigger event, never by the dot alone.

    Releases are held at least hold_s after the button-down (via a timer, so
    neither the serial nor the camera thread sleeps).
    """

    def __init__(
        self,
        gun_id: str,
        injector: Injector,
        hold_s: float,
        on_fire: bool = False,
        window_s: float = 0.25,
    ):
        self.gun_id = gun_id
        self.injector = injector
        self.hold_s = hold_s
        self.on_fire = on_fire
        self.window_s = window_s
        self._lock = threading.Lock()
        self._pos: ScreenPoint | None = None
        self._down_at: float | None = None
        self._shot_at: float | None = None  # on_fire: press awaiting its dot
        self._release_pending = False       # on_fire: released before the dot arrived
        self._press_id = 0                  # stops a stale hold timer releasing a newer press

    def update(self, pos: ScreenPoint, t: float) -> None:
        """Called by the camera loop with the newest mapped position and its frame time."""
        with self._lock:
            self._pos = pos
            if self._shot_at is None or t < self._shot_at:
                return  # nothing armed, or this frame was grabbed before the pull
            if t - self._shot_at > self.window_s:
                print("[app] on_fire: no dot within the window; shot dropped")
            else:
                self._press(pos)
                if self._release_pending:
                    self._schedule_up()
            self._shot_at = None
            self._release_pending = False

    def on_event(self, gid: str, msg: dict[str, Any], t: float) -> None:
        if gid != self.gun_id or msg.get("e") != "trigger":
            return
        with self._lock:
            if msg.get("s") == 1:
                if self.on_fire:
                    self._shot_at = t
                    self._release_pending = False
                elif self._pos is None:
                    print("[app] trigger before the dot was ever seen; ignored")
                else:
                    self._press(self._pos)
            elif self._shot_at is not None:
                self._release_pending = True
            elif self._down_at is not None:
                self._schedule_up()

    def _press(self, pos: ScreenPoint) -> None:
        self.injector.move(pos.x, pos.y)
        self.injector.button(True)
        self._down_at = time.perf_counter()
        self._press_id += 1

    def _schedule_up(self) -> None:
        assert self._down_at is not None
        remaining = self.hold_s - (time.perf_counter() - self._down_at)
        if remaining <= 0:
            self._up()
            return
        timer = threading.Timer(remaining, self._timed_up, args=(self._press_id,))
        timer.daemon = True
        timer.start()

    def _timed_up(self, press_id: int) -> None:
        with self._lock:
            if press_id == self._press_id and self._down_at is not None:
                self._up()

    def _up(self) -> None:
        self.injector.button(False)
        self._down_at = None

    def release(self) -> None:
        """Make sure the button isn't left held down on shutdown."""
        with self._lock:
            self._shot_at = None
            if self._down_at is not None:
                self._up()


def run(cfg: dict[str, Any], gun_id: str, dry_run: bool) -> None:
    screen_size = screen.primary_size()
    try:
        mapper = Mapper.from_config(cfg, screen_size)
    except ValueError as e:
        raise SystemExit(f"{e}  (python -m lightgun.calibrate)") from None
    gun_cfg = cfg.get("guns", {}).get(gun_id)
    if gun_cfg is None:
        raise SystemExit(f"Gun {gun_id!r} is not in config [guns].")
    offset = tuple(float(v) for v in gun_cfg.get("offset", (0, 0)))

    injector = make_injector(cfg, screen_size, dry_run)
    hold_s = float(cfg.get("injection", {}).get("click_hold_ms", 20)) / 1000.0
    try:
        mode, _, window_ms = guns.laser_settings(cfg, gun_id)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    trigger = TriggerHandler(gun_id, injector, hold_s,
                             on_fire=mode == "on_fire", window_s=window_ms / 1000.0)

    links = guns.connect(cfg, trigger.on_event)
    if gun_id not in links:
        print(f"[app] {gun_id} not connected: cursor will track, but there is no trigger.")
    for link in links.values():
        link.enable(cfg)

    cam = Camera(cfg)
    cam.start()
    if not cam.exposure_locked():
        print("[app] WARNING: exposure not locked; expect false dots on bright scenes.")
    cal = cfg["calibration"]
    cam_size = (cam.stats.applied.get("width"), cam.stats.applied.get("height"))
    cal_cam = (cal.get("camera_width"), cal.get("camera_height"))
    if all(cal_cam) and cam_size != cal_cam:
        print(f"[app] WARNING: camera is {cam_size} but calibration used {cal_cam}; recalibrate.")

    print(f"[app] {gun_id} driving the cursor on a {screen_size[0]}x{screen_size[1]} screen, "
          f"laser mode {mode}{' (dry run)' if dry_run else ''}. Ctrl+C to quit.")
    last_index = -1
    last_xy: tuple[float, float] | None = None
    try:
        while True:
            frame = cam.read()
            if frame is None or frame.index == last_index:
                time.sleep(0.001)
                continue
            last_index = frame.index
            blob = pick_blob(detect_frame(frame.image, cfg), gun_id)
            if blob is None:
                continue
            pos = mapper.map_point(blob.x, blob.y, offset)
            trigger.update(pos, frame.timestamp)
            xy = (round(pos.x), round(pos.y))
            if pos.on_screen and xy != last_xy:
                injector.move(pos.x, pos.y)
                last_xy = xy
    except KeyboardInterrupt:
        pass
    finally:
        trigger.release()
        guns.close_all(links)
        cam.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Light gun runtime (Phase 2: single player).")
    ap.add_argument("--config", default=None, help="Path to config TOML.")
    ap.add_argument("--gun", default="p1", help="Gun that drives the cursor (default p1).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Log clicks instead of injecting input.")
    args = ap.parse_args()
    run(config.load(args.config), args.gun, args.dry_run)


if __name__ == "__main__":
    main()
