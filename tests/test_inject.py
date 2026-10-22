"""Pointer injection: coordinate normalization and trigger/position pairing."""

from __future__ import annotations

import ctypes
import sys
import time

import pytest

from lightgun.app import TriggerHandler
from lightgun.guns import laser_settings
from lightgun.mapping import ScreenPoint


def test_to_absolute_corners_and_clamp():
    from lightgun.inject.windows import to_absolute

    assert to_absolute(0, 0, 1920, 1080) == (0, 0)
    assert to_absolute(1919, 1079, 1920, 1080) == (65535, 65535)
    ax, ay = to_absolute(959.5, 539.5, 1920, 1080)
    assert abs(ax - 32768) <= 1 and abs(ay - 32768) <= 1
    assert to_absolute(-50, 5000, 1920, 1080) == (0, 65535)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows struct layout")
def test_input_struct_matches_win32_size():
    from lightgun.inject.windows import INPUT

    # SendInput rejects calls whose cbSize doesn't match the OS's INPUT.
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


class RecordingInjector:
    def __init__(self):
        self.calls: list[tuple] = []

    def move(self, x, y):
        self.calls.append(("move", round(x), round(y)))

    def button(self, down):
        self.calls.append(("down" if down else "up", time.perf_counter()))

    def kinds(self):
        return [c[0] for c in self.calls]


PRESS = {"e": "trigger", "s": 1}
RELEASE = {"e": "trigger", "s": 0}


def now():
    return time.perf_counter()


def wait_for_up(inj, timeout=0.5):
    deadline = now() + timeout
    while "up" not in inj.kinds() and now() < deadline:
        time.sleep(0.002)


# -- always mode ---------------------------------------------------------------


def test_trigger_clicks_at_latest_position():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.0)
    h.update(ScreenPoint(100, 200, True), now())
    h.update(ScreenPoint(300, 400, True), now())
    h.on_event("p1", PRESS, now())
    h.on_event("p1", RELEASE, now())
    assert inj.calls[0] == ("move", 300, 400)
    assert inj.kinds() == ["move", "down", "up"]


def test_trigger_enforces_minimum_hold():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.03)
    h.update(ScreenPoint(10, 10, True), now())
    h.on_event("p1", PRESS, now())
    h.on_event("p1", RELEASE, now())
    assert inj.kinds() == ["move", "down"]  # up is deferred, not slept for
    wait_for_up(inj)
    down_t, up_t = inj.calls[1][1], inj.calls[2][1]
    assert up_t - down_t >= 0.025


def test_stale_hold_timer_does_not_release_next_press():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.05)
    h.update(ScreenPoint(10, 10, True), now())
    h.on_event("p1", PRESS, now())
    h.on_event("p1", RELEASE, now())  # timer armed for press #1
    h._up()                            # simulate press #1 already lifted
    h.on_event("p1", PRESS, now())     # press #2 held down
    time.sleep(0.1)                    # press #1's timer fires meanwhile
    assert inj.kinds()[-1] == "down"


def test_trigger_ignores_other_gun_unseen_dot_and_stray_release():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.0)
    h.on_event("p1", PRESS, now())  # dot never seen
    h.update(ScreenPoint(10, 10, True), now())
    h.on_event("p2", PRESS, now())  # not our gun
    h.on_event("p1", RELEASE, now())  # release w/o press
    h.on_event("p1", {"e": "hb"}, now())
    assert inj.calls == []


def test_release_on_shutdown_lifts_held_button():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.0)
    h.update(ScreenPoint(10, 10, True), now())
    h.on_event("p1", PRESS, now())
    h.release()
    assert inj.kinds()[-1] == "up"


# -- on_fire mode --------------------------------------------------------------


def test_on_fire_clicks_at_first_dot_after_press_not_stale_one():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.0, on_fire=True, window_s=0.25)
    t0 = now()
    h.update(ScreenPoint(1, 1, True), t0 - 0.01)  # old position from before the pull
    h.on_event("p1", PRESS, t0)
    assert inj.calls == []                         # armed, not clicked yet
    h.update(ScreenPoint(2, 2, True), t0 - 0.001)  # frame grabbed before the pull
    assert inj.calls == []
    h.update(ScreenPoint(500, 600, True), t0 + 0.04)
    h.update(ScreenPoint(700, 800, True), t0 + 0.07)  # later frames don't re-fire
    assert inj.calls[0] == ("move", 500, 600)
    assert inj.kinds() == ["move", "down"]
    h.on_event("p1", RELEASE, now())
    assert inj.kinds() == ["move", "down", "up"]


def test_on_fire_quick_tap_released_before_dot_still_clicks():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.02, on_fire=True, window_s=0.25)
    t0 = now()
    h.on_event("p1", PRESS, t0)
    h.on_event("p1", RELEASE, t0 + 0.005)
    h.update(ScreenPoint(50, 60, True), t0 + 0.04)
    wait_for_up(inj)
    assert inj.kinds() == ["move", "down", "up"]
    assert inj.calls[2][1] - inj.calls[1][1] >= 0.015


def test_on_fire_drops_shot_when_dot_arrives_too_late():
    inj = RecordingInjector()
    h = TriggerHandler("p1", inj, hold_s=0.0, on_fire=True, window_s=0.1)
    t0 = now()
    h.on_event("p1", PRESS, t0)
    h.update(ScreenPoint(50, 60, True), t0 + 0.2)
    h.update(ScreenPoint(50, 60, True), t0 + 0.25)
    assert inj.calls == []


# -- config ---------------------------------------------------------------------


def test_laser_settings_default_override_and_validation():
    cfg = {
        "laser": {"mode": "always", "fire_pulse_ms": 80, "fire_window_ms": 200},
        "guns": {"p1": {}, "p2": {"laser_mode": "on_fire"}},
    }
    assert laser_settings(cfg, "p1") == ("always", 80, 200)
    assert laser_settings(cfg, "p2") == ("on_fire", 80, 200)
    assert laser_settings({}, "p1") == ("always", 100, 250)
    cfg["guns"]["p1"]["laser_mode"] = "strobe"
    with pytest.raises(ValueError):
        laser_settings(cfg, "p1")
