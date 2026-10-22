"""Detection accuracy against synthetic frames with known ground truth."""

from __future__ import annotations

import numpy as np

from lightgun.detect import detect_frame
from tests.tools.gen_synthetic import Dot, render_frame

CFG = {
    "detection": {
        "threshold": 220,
        "min_area": 2.0,
        "max_area": 500.0,
        "min_circularity": 0.3,
        "annulus_inner": 3.0,
        "annulus_outer": 8.0,
    },
    "guns": {
        "p1": {"color": "red", "hue_ranges": [[0, 12], [168, 180]]},
        "p2": {"color": "green", "hue_ranges": [[45, 85]]},
    },
}


def _nearest(blobs, x, y):
    return min(blobs, key=lambda b: np.hypot(b.x - x, b.y - y))


def test_single_dot_centroid_accuracy():
    frame = render_frame(dots=[Dot(640, 360, "red")], seed=0)
    blobs = detect_frame(frame, CFG)
    assert len(blobs) == 1
    b = blobs[0]
    assert np.hypot(b.x - 640, b.y - 360) < 1.0


def test_bright_background_no_false_positives():
    frame = render_frame(dots=[Dot(640, 360, "red")], bright_background=True, seed=3)
    blobs = detect_frame(frame, CFG)
    # Exactly the one real dot, nothing from the bright content.
    assert len(blobs) == 1


def test_empty_scene_no_detections():
    frame = render_frame(dots=[], bright_background=True, seed=7)
    assert detect_frame(frame, CFG) == []


def test_two_dots_color_classification():
    frame = render_frame(dots=[Dot(500, 300, "red"), Dot(820, 400, "green")], seed=5)
    blobs = detect_frame(frame, CFG)
    assert len(blobs) == 2
    red = _nearest(blobs, 500, 300)
    green = _nearest(blobs, 820, 400)
    assert red.gun == "p1"
    assert green.gun == "p2"


def test_many_frames_detection_rate():
    rng = np.random.default_rng(42)
    hits = 0
    n = 200
    for _ in range(n):
        x = float(rng.uniform(350, 900))
        y = float(rng.uniform(250, 470))
        frame = render_frame(dots=[Dot(x, y, "red")], bright_background=bool(rng.integers(2)))
        blobs = detect_frame(frame, CFG)
        if len(blobs) == 1 and np.hypot(blobs[0].x - x, blobs[0].y - y) < 1.5:
            hits += 1
    assert hits / n >= 0.99


def test_pick_blob_never_borrows_other_guns_dot():
    from lightgun.detect import Blob, pick_blob

    red = Blob(0, 0, area=10, circularity=1, gun="p1")
    green = Blob(5, 5, area=50, circularity=1, gun="p2")
    unknown = Blob(9, 9, area=20, circularity=1, gun="?")
    assert pick_blob([red, green, unknown], "p1") is red
    assert pick_blob([green, unknown], "p1") is unknown
    assert pick_blob([green], "p1") is None
    assert pick_blob([red, green], None) is green
    assert pick_blob([], None) is None
