"""Generate synthetic camera frames for testing detection without hardware.

Renders a dim, animated 'screen' region plus 0-N laser dots (Gaussian spots
with saturated white cores and colored fringes). Ground-truth dot positions are
returned alongside each frame so tests can check detection accuracy.

Used by the pytest suite and runnable standalone to eyeball output:
    python -m tests.tools.gen_synthetic --show
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# (B, G, R) fringe color per laser, plus a white-ish core.
LASER_BGR = {
    "red": (40, 40, 255),
    "green": (40, 255, 40),
}


@dataclass
class Dot:
    x: float
    y: float
    color: str


def render_frame(
    width: int = 1280,
    height: int = 720,
    dots: list[Dot] | None = None,
    bright_background: bool = False,
    screen_rect: tuple[int, int, int, int] | None = None,
    seed: int | None = None,
) -> np.ndarray:
    """Render one BGR frame.

    bright_background simulates a white explosion frame — the stress test for
    threshold separation.
    """
    rng = np.random.default_rng(seed)
    img = np.zeros((height, width, 3), dtype=np.uint8)

    if screen_rect is None:
        screen_rect = (int(width * 0.2), int(height * 0.2), int(width * 0.6), int(height * 0.6))
    sx, sy, sw, sh = screen_rect

    # Dim game-ish content inside the screen rect. Even "bright" stays well
    # below a cranked-down exposure in the real rig; here we cap it so a correct
    # threshold still separates the dot.
    base = 90 if bright_background else 35
    content = rng.integers(0, base, size=(sh, sw, 3), dtype=np.uint8)
    img[sy:sy + sh, sx:sx + sw] = content

    for d in dots or []:
        _draw_dot(img, d)
    return img


def _draw_dot(img: np.ndarray, dot: Dot) -> None:
    h, w = img.shape[:2]
    radius = 6
    fringe = LASER_BGR[dot.color]
    x0, x1 = max(0, int(dot.x - radius)), min(w, int(dot.x + radius) + 1)
    y0, y1 = max(0, int(dot.y - radius)), min(h, int(dot.y + radius) + 1)
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            dist = np.hypot(xx - dot.x, yy - dot.y)
            if dist > radius:
                continue
            g = np.exp(-(dist ** 2) / (2 * (radius / 2.2) ** 2))
            if dist < radius * 0.4:
                # Saturated white core.
                px = np.array([255, 255, 255], dtype=np.float32)
            else:
                px = np.array(fringe, dtype=np.float32) * g + img[yy, xx] * (1 - g)
            img[yy, xx] = np.clip(px, 0, 255).astype(np.uint8)


def _demo() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    frame = render_frame(
        dots=[Dot(640, 360, "red"), Dot(800, 300, "green")],
        bright_background=True,
        seed=1,
    )
    if args.show:
        cv2.imshow("synthetic", frame)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    else:
        cv2.imwrite("synthetic_demo.png", frame)
        print("wrote synthetic_demo.png")


if __name__ == "__main__":
    _demo()
