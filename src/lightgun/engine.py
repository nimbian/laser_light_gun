"""The runtime engine shared by the CLI (`python -m lightgun.app`) and the GUI.

One worker thread reads new camera frames, detects dots, and, while injecting,
maps the driving gun's dot to the game monitor and moves the cursor. Trigger
events arrive over serial on other threads and are paired with the dot's
position by TriggerHandler: press -> move there + button down, release ->
button up (held at least click_hold_ms so short pulls still register). In the
default "always" laser mode that's the latest tracked position; in "on_fire"
mode it's the first dot after the pull (see [laser] in the config). A click is
never inferred from the dot appearing or disappearing.

Windows has one cursor, so only one gun drives it. Two players arrive in
Phase 4 via per-Pico HID pointers.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from . import guns, screen
from .capture import Camera, Frame
from .detect import Blob, detect_frame, pick_blob
from .inject import Injector, make_injector
from .mapping import Mapper, ScreenPoint
from .screen import Monitor


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
                print("[engine] on_fire: no dot within the window; shot dropped")
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
                    print("[engine] trigger before the dot was ever seen; ignored")
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


@dataclass
class Snapshot:
    """The newest processed frame, for previews and calibration."""

    frame: Frame
    blobs: list[Blob]
    pointer: ScreenPoint | None   # driving gun's mapped position while injecting


class Engine:
    """Camera + detection + gun links + optional pointer injection, on a worker thread.

    Lifecycle: start() opens the camera and guns and begins detecting (lasers
    forced to "always", for preview and calibration). start_injection() maps
    the driving gun's dot to the game monitor, moves the cursor and clicks on
    the trigger, with lasers in their configured mode. stop_injection() goes
    back to preview; stop() releases everything.

    The config dict is read live each frame, so detection tweaks (threshold,
    area, ...) apply immediately. Camera or gun changes need stop() + start().
    """

    def __init__(self, cfg: dict[str, Any], gun_id: str = "p1"):
        self.cfg = cfg
        self.gun_id = gun_id
        self.cam: Camera | None = None
        self.links: dict[str, guns.GunLink] = {}
        self.monitor: Monitor | None = None
        self.mode = "always"
        self.error: str | None = None
        self._listeners: list[guns.EventCallback] = []
        self._trigger: TriggerHandler | None = None
        self._mapper: Mapper | None = None
        self._injector: Injector | None = None
        self._offset = (0.0, 0.0)
        self._snapshot: Snapshot | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def injecting(self) -> bool:
        return self._trigger is not None

    def start(self, connect_guns: bool = True) -> None:
        self.error = None
        self.cam = Camera(self.cfg)
        try:
            self.cam.start()
        except Exception:
            self.cam = None
            raise
        if connect_guns:
            self.links = guns.connect(self.cfg, self._on_event)
            for link in self.links.values():
                link.enable(self.cfg, force_always=True)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_injection()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        guns.close_all(self.links)
        self.links = {}
        if self.cam is not None:
            self.cam.stop()
            self.cam = None
        self._snapshot = None

    def reconnect_guns(self) -> None:
        """Close and rediscover the guns (after plugging one in or changing ports)."""
        injecting = self.injecting
        trigger, self._trigger = self._trigger, None
        if trigger is not None:
            trigger.release()
        guns.close_all(self.links)
        self.links = guns.connect(self.cfg, self._on_event)
        for link in self.links.values():
            link.enable(self.cfg, force_always=not injecting)
        self._trigger = trigger

    def start_injection(self, dry_run: bool = False) -> None:
        """Begin driving the cursor. Raises ValueError if not calibrated / misconfigured."""
        monitor = screen.selected_monitor(self.cfg)
        mapper = Mapper.from_config(self.cfg, monitor.size)
        gun_cfg = self.cfg.get("guns", {}).get(self.gun_id)
        if gun_cfg is None:
            raise ValueError(f"Gun {self.gun_id!r} is not in config [guns].")
        mode, _, window_ms = guns.laser_settings(self.cfg, self.gun_id)
        hold_s = float(self.cfg.get("injection", {}).get("click_hold_ms", 20)) / 1000.0
        ox, oy = (float(v) for v in gun_cfg.get("offset", (0, 0)))

        self.monitor = monitor
        self._offset = (ox, oy)
        self._injector = make_injector(self.cfg, monitor, dry_run)
        self._mapper = mapper
        self.mode = mode
        for link in self.links.values():
            link.enable(self.cfg)
        self._trigger = TriggerHandler(self.gun_id, self._injector, hold_s,
                                       on_fire=mode == "on_fire", window_s=window_ms / 1000.0)
        for warning in self.calibration_warnings():
            print(f"[engine] WARNING: {warning}")

    def stop_injection(self) -> None:
        trigger, self._trigger = self._trigger, None
        if trigger is not None:
            trigger.release()
            for link in self.links.values():
                link.enable(self.cfg, force_always=True)
        self.mode = "always"

    # -- queries / controls -----------------------------------------------

    def snapshot(self) -> Snapshot | None:
        return self._snapshot

    def add_listener(self, cb: guns.EventCallback) -> None:
        """Receive every gun event (called on a serial thread)."""
        self._listeners.append(cb)

    def remove_listener(self, cb: guns.EventCallback) -> None:
        if cb in self._listeners:
            self._listeners.remove(cb)

    def set_exposure(self, value: float) -> None:
        self.cfg["camera"]["exposure"] = value
        if self.cam is not None:
            self.cam.set_exposure(value)

    def camera_size(self) -> tuple[int, int]:
        a = self.cam.stats.applied if self.cam else {}
        return int(a.get("width", 0)), int(a.get("height", 0))

    def calibration_warnings(self) -> list[str]:
        out = []
        if self.cam is not None and not self.cam.exposure_locked():
            out.append("exposure not locked; expect false dots on bright scenes")
        cal = self.cfg["calibration"]
        cal_cam = (cal.get("camera_width"), cal.get("camera_height"))
        if all(cal_cam) and self.camera_size() != cal_cam:
            out.append(f"camera is {self.camera_size()} but calibration used {cal_cam}; "
                       "recalibrate")
        return out

    # -- internals ---------------------------------------------------------

    def _on_event(self, gid: str, msg: dict[str, Any], t: float) -> None:
        trigger = self._trigger
        if trigger is not None:
            trigger.on_event(gid, msg, t)
        for cb in list(self._listeners):
            cb(gid, msg, t)

    def _loop(self) -> None:
        assert self.cam is not None
        last_index = -1
        last_xy: tuple[int, int] | None = None
        try:
            while self._running:
                frame = self.cam.read()
                if frame is None or frame.index == last_index:
                    time.sleep(0.001)
                    continue
                last_index = frame.index
                blobs = detect_frame(frame.image, self.cfg)
                pointer = None
                trigger, mapper, injector = self._trigger, self._mapper, self._injector
                if trigger is not None and mapper is not None and injector is not None:
                    blob = pick_blob(blobs, self.gun_id)
                    if blob is not None:
                        pointer = mapper.map_point(blob.x, blob.y, self._offset)
                        trigger.update(pointer, frame.timestamp)
                        xy = (round(pointer.x), round(pointer.y))
                        if pointer.on_screen and xy != last_xy:
                            injector.move(pointer.x, pointer.y)
                            last_xy = xy
                self._snapshot = Snapshot(frame, blobs, pointer)
        except Exception as e:  # surface to the UI instead of dying silently
            self.error = f"{type(e).__name__}: {e}"
            print(f"[engine] stopped: {self.error}")
            self._running = False
