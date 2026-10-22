"""Calibration session logic and saving the result into the config."""

from __future__ import annotations

from lightgun import config
from lightgun.calibrate import CalibrationSession, _grid_targets, save_result
from lightgun.detect import Blob

RESULT = {
    "homography": [[1.5, 0.0, -10.25], [0.0, 1.5, 3.0], [1e-05, 0.0, 1.0]],
    "captured_width": 1920,
    "captured_height": 1080,
    "camera_width": 1280,
    "camera_height": 720,
    "reprojection_error": 0.5,
}


def dot(x, y):
    return Blob(x, y, area=20, circularity=1.0, gun="p1")


def test_save_result_roundtrips_and_keeps_comments(isolated_user_config):
    p = config.ensure_user_config()
    save_result(p, RESULT)
    cfg = config.load(p)
    for k in ("homography", "captured_width", "captured_height", "camera_width",
              "camera_height"):
        assert cfg["calibration"][k] == RESULT[k]
    assert cfg["calibration"]["grid_cols"] == 3
    assert cfg["guns"]["p1"]["color"] == "red"
    assert "# Grayscale brightness threshold" in p.read_text(encoding="utf-8")


def test_grid_targets_inset_and_ordered():
    t = _grid_targets(1000, 500, 3, 3)
    assert t[0] == (100, 50) and t[-1] == (900, 450)
    assert len(t) == 9


def test_session_auto_accepts_after_stable_frames_only():
    s = CalibrationSession((1000, 500), stable_frames=3, auto_accept=True)
    assert not s.feed(dot(10, 10))   # first sighting
    assert not s.feed(dot(10.5, 10))
    assert not s.feed(dot(10, 10.5))
    assert s.feed(dot(10, 10))       # 3rd stable frame accepts
    assert s.index == 1
    assert s.cam_points == [(10, 10)]
    assert s.screen_points == [(100.0, 50.0)]


def test_session_jitter_resets_stability_and_no_dot_cannot_fire():
    s = CalibrationSession((1000, 500), stable_frames=2, auto_accept=True)
    s.feed(dot(10, 10))
    s.feed(dot(10, 10))
    s.feed(dot(40, 40))              # jumped: restart the count
    assert s.stable_count == 0 and s.index == 0
    s.feed(None)
    assert not s.fire()


def test_session_trigger_mode_never_auto_accepts_and_redo_steps_back():
    s = CalibrationSession((1000, 500), stable_frames=1, auto_accept=False)
    for _ in range(5):
        assert not s.feed(dot(10, 10))
    assert s.fire() and s.index == 1
    s.feed(dot(20, 20))
    assert s.fire() and s.index == 2
    s.redo()
    assert s.index == 1 and s.current_target == s.targets[1]


def test_session_full_run_produces_homography():
    s = CalibrationSession((1920, 1080), stable_frames=1, auto_accept=False)
    while not s.done:
        tx, ty = s.current_target
        s.feed(dot(tx / 2 + 100, ty / 2 + 50))  # camera sees a half-scale, offset screen
        s.fire()
    r = s.result((1280, 720))
    assert r["captured_width"] == 1920 and r["camera_width"] == 1280
    assert r["reprojection_error"] < 0.5
