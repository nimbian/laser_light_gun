"""Homography round-trip and mapping behavior."""

from __future__ import annotations

from lightgun.mapping import Mapper, compute_homography


def test_homography_roundtrip_identity_scale():
    # Camera points inside a region; screen points are a known affine of them.
    cam = [(100, 100), (500, 100), (500, 400), (100, 400)]
    screen = [(0, 0), (1920, 0), (1920, 1080), (0, 1080)]
    H, err = compute_homography(cam, screen)
    assert err < 1.0

    m = Mapper(H, captured_size=(1920, 1080), screen_size=(1920, 1080))
    # A camera point at the center maps near screen center.
    p = m.map_point(300, 250)
    assert abs(p.x - 960) < 30
    assert abs(p.y - 540) < 30
    assert p.on_screen


def test_offscreen_flag_and_clamp():
    cam = [(100, 100), (500, 100), (500, 400), (100, 400)]
    screen = [(0, 0), (1920, 0), (1920, 1080), (0, 1080)]
    H, _ = compute_homography(cam, screen)
    m = Mapper(H, captured_size=(1920, 1080), screen_size=(1920, 1080))
    # Well outside the calibrated quad -> off screen, clamped into bounds.
    p = m.map_point(0, 0)
    assert not p.on_screen
    assert 0 <= p.x < 1920
    assert 0 <= p.y < 1080


def test_resolution_rescale():
    cam = [(0, 0), (100, 0), (100, 100), (0, 100)]
    screen = [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]
    H, _ = compute_homography(cam, screen)
    # Calibrated at 1000x1000 but live screen is 2000x2000 -> doubled.
    m = Mapper(H, captured_size=(1000, 1000), screen_size=(2000, 2000))
    p = m.map_point(50, 50)
    assert abs(p.x - 1000) < 5
    assert abs(p.y - 1000) < 5


def test_requires_four_points():
    try:
        compute_homography([(0, 0), (1, 1)], [(0, 0), (1, 1)])
    except ValueError:
        return
    raise AssertionError("expected ValueError for <4 points")
