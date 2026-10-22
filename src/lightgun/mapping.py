"""Map camera-space dot positions to screen pixels via a homography.

The calibration step produces a 3x3 homography H that maps camera (x,y) to
screen (x,y) at the resolution calibration was captured at. At runtime we apply
H, rescale to the current screen resolution, apply a per-gun offset, and flag
anything that lands off-screen (needed later for reload-by-shooting-offscreen).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class ScreenPoint:
    x: float
    y: float
    on_screen: bool


class Mapper:
    def __init__(
        self,
        homography: list[list[float]] | np.ndarray,
        captured_size: tuple[int, int],
        screen_size: tuple[int, int],
    ):
        self.H = np.asarray(homography, dtype=np.float64).reshape(3, 3)
        self.cap_w, self.cap_h = captured_size
        self.screen_w, self.screen_h = screen_size

    @classmethod
    def from_config(cls, cfg: dict[str, Any], screen_size: tuple[int, int]) -> Mapper:
        cal = cfg["calibration"]
        if not cal.get("homography"):
            raise ValueError("No homography in config; run calibration first.")
        return cls(
            homography=cal["homography"],
            captured_size=(int(cal["captured_width"]), int(cal["captured_height"])),
            screen_size=screen_size,
        )

    def map_point(
        self, cx: float, cy: float, offset: tuple[float, float] = (0.0, 0.0)
    ) -> ScreenPoint:
        src = np.array([[[cx, cy]]], dtype=np.float64)
        import cv2

        dst = cv2.perspectiveTransform(src, self.H)[0][0]
        sx, sy = float(dst[0]), float(dst[1])

        # Homography maps into the captured screen resolution; rescale if the
        # live screen differs.
        if self.cap_w and self.cap_h:
            sx *= self.screen_w / self.cap_w
            sy *= self.screen_h / self.cap_h

        sx += offset[0]
        sy += offset[1]

        on = 0 <= sx < self.screen_w and 0 <= sy < self.screen_h
        sx = min(max(sx, 0.0), self.screen_w - 1)
        sy = min(max(sy, 0.0), self.screen_h - 1)
        return ScreenPoint(x=sx, y=sy, on_screen=on)


def compute_homography(
    camera_points: list[tuple[float, float]],
    screen_points: list[tuple[float, float]],
) -> tuple[np.ndarray, float]:
    """Compute H mapping camera points to screen points.

    Returns (H, mean_reprojection_error_px). Needs >= 4 correspondences.
    """
    import cv2

    if len(camera_points) < 4:
        raise ValueError("Need at least 4 point correspondences.")
    src = np.array(camera_points, dtype=np.float64).reshape(-1, 1, 2)
    dst = np.array(screen_points, dtype=np.float64).reshape(-1, 1, 2)
    H, _ = cv2.findHomography(src, dst, cv2.RANSAC, ransacReprojThreshold=5.0)
    if H is None:
        raise RuntimeError("findHomography failed (degenerate points?).")

    proj = cv2.perspectiveTransform(src, H).reshape(-1, 2)
    err = float(np.mean(np.linalg.norm(proj - np.array(screen_points), axis=1)))
    return H, err
