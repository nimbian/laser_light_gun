"""Laser dot detection and color classification.

Pipeline per frame:
  1. Grayscale + threshold -> binary mask of bright pixels.
  2. Connected components -> candidate blobs.
  3. Filter by area and circularity (rejects noise and glare).
  4. Subpixel centroid via image moments.
  5. Classify color by sampling the *annulus* around the centroid, because a
     saturated dot core clips to white and loses hue; the fringe keeps it.

Phase 0 only needs steps 1-4 (find the dot, print coordinates). Step 5 (color)
is here too so two-gun work in later phases has nothing new to wire in.

Run directly to preview detection on the live camera:
    python -m lightgun.detect            (--laser turns Pico-driven lasers on)
Keys: q quit, [ / ] lower/raise threshold, - / = darker/brighter exposure.
On quit it prints the final threshold/exposure to copy into the config.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from . import config, guns
from .capture import Camera


@dataclass
class Blob:
    x: float               # subpixel centroid, camera pixels
    y: float
    area: float
    circularity: float
    hue: float | None = None       # OpenCV HSV hue 0-180, or None if unread
    gun: str | None = None         # assigned gun id, or None/"?" if unresolved


def _circularity(area: float, perimeter: float) -> float:
    if perimeter <= 0:
        return 0.0
    return float(min(1.0, 4.0 * np.pi * area / (perimeter * perimeter)))


def find_blobs(gray: np.ndarray, det: dict[str, Any]) -> list[Blob]:
    """Threshold a grayscale frame and return accepted blobs."""
    thr = int(det["threshold"])
    _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = float(det["min_area"])
    max_area = float(det["max_area"])
    min_circ = float(det["min_circularity"])

    blobs: list[Blob] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue
        perim = cv2.arcLength(c, True)
        circ = _circularity(area, perim)
        if circ < min_circ:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx = m["m10"] / m["m00"]
        cy = m["m01"] / m["m00"]
        blobs.append(Blob(x=cx, y=cy, area=area, circularity=circ))
    return blobs


def read_hue(hsv: np.ndarray, blob: Blob, det: dict[str, Any]) -> float | None:
    """Mean hue sampled from the annulus around the centroid.

    Samples a ring (inner..outer radius) rather than the core, which may be
    clipped to white. Returns circular-mean hue in 0-180, or None if the ring
    has no usable pixels.
    """
    h, w = hsv.shape[:2]
    r_out = float(det["annulus_outer"])
    r_in = float(det["annulus_inner"])
    cx, cy = blob.x, blob.y

    x0 = max(0, int(cx - r_out))
    x1 = min(w, int(cx + r_out) + 1)
    y0 = max(0, int(cy - r_out))
    y1 = min(h, int(cy + r_out) + 1)
    if x0 >= x1 or y0 >= y1:
        return None

    patch = hsv[y0:y1, x0:x1]
    ys, xs = np.mgrid[y0:y1, x0:x1]
    dist = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
    ring = (dist >= r_in) & (dist <= r_out)
    # Require some saturation and value so we don't average dark background.
    sat = patch[:, :, 1] > 60
    val = patch[:, :, 2] > 60
    sel = ring & sat & val
    if not np.any(sel):
        return None

    hues = patch[:, :, 0][sel].astype(np.float32)
    # Circular mean: hue is 0-180 representing 0-360 degrees.
    ang = hues * (2.0 * np.pi / 180.0)
    mean_ang = np.arctan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))
    if mean_ang < 0:
        mean_ang += 2.0 * np.pi
    return float(mean_ang * (180.0 / (2.0 * np.pi)))


def _hue_in_ranges(hue: float, ranges: list[list[int]]) -> bool:
    return any(lo <= hue <= hi for lo, hi in ranges)


def classify(blobs: list[Blob], hsv: np.ndarray, cfg: dict[str, Any]) -> list[Blob]:
    """Assign each blob to a gun by hue. Ambiguous blobs get gun='?'."""
    det = cfg["detection"]
    guns = {k: v for k, v in cfg.get("guns", {}).items()}
    for b in blobs:
        b.hue = read_hue(hsv, b, det)
        if b.hue is None:
            b.gun = "?"
            continue
        matches = [
            gid for gid, g in guns.items()
            if _hue_in_ranges(b.hue, [list(r) for r in g["hue_ranges"]])
        ]
        b.gun = matches[0] if len(matches) == 1 else "?"
    return blobs


def pick_blob(blobs: list[Blob], gun: str | None) -> Blob | None:
    """The dot to use for one gun: largest blob classified as that gun.

    With gun=None, the largest blob of any color. With a gun named, fall back
    only to unclassified ("?") blobs, never to another gun's dot.
    """
    if gun is None:
        pool = blobs
    else:
        pool = [b for b in blobs if b.gun == gun] or [b for b in blobs if b.gun == "?"]
    return max(pool, key=lambda b: b.area) if pool else None


def detect_frame(image: np.ndarray, cfg: dict[str, Any]) -> list[Blob]:
    """Full single-frame detection + classification. Returns blobs."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blobs = find_blobs(gray, cfg["detection"])
    if blobs:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        classify(blobs, hsv, cfg)
    return blobs


# -- live preview ----------------------------------------------------------

_GUN_BGR = {"p1": (0, 0, 255), "p2": (0, 255, 0), "?": (0, 255, 255), None: (255, 255, 255)}


def _preview(cfg: dict[str, Any], laser: bool = False) -> None:
    links = guns.connect(cfg, lambda *_: None) if laser else {}
    for link in links.values():
        link.enable(cfg, force_always=True)

    cam = Camera(cfg)
    cam.start()
    applied = cam.stats.applied
    print("Camera applied settings:", applied)
    if not cam.exposure_locked():
        print(
            "WARNING: exposure did not lock to the requested value. "
            "The scene may be too bright for clean detection. "
            "Try the Logitech Camera Settings app, or switch backend dshow<->msmf."
        )

    win = "lightgun detect (q=quit  [ ]=threshold)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    thr = int(cfg["detection"]["threshold"])
    exposure = float(cfg["camera"].get("exposure", -7))
    last_index = -1

    try:
        while True:
            frame = cam.read()
            if frame is None or frame.index == last_index:
                if cv2.waitKey(5) & 0xFF == ord("q"):
                    break
                continue
            last_index = frame.index

            cfg["detection"]["threshold"] = thr
            blobs = detect_frame(frame.image, cfg)
            view = frame.image.copy()
            for b in blobs:
                color = _GUN_BGR.get(b.gun, (255, 255, 255))
                cv2.circle(view, (int(b.x), int(b.y)), 10, color, 1)
                label = f"{b.gun or '-'} a{b.area:.0f} c{b.circularity:.2f}"
                if b.hue is not None:
                    label += f" h{b.hue:.0f}"
                cv2.putText(view, label, (int(b.x) + 12, int(b.y)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

            hud = (f"fps {cam.stats.measured_fps:.0f}  thr {thr}  exp {exposure:g}  "
                   f"blobs {len(blobs)}")
            cv2.putText(view, hud, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2)
            cv2.imshow(win, view)

            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            elif k == ord("["):
                thr = max(0, thr - 2)
            elif k == ord("]"):
                thr = min(255, thr + 2)
            elif k in (ord("-"), ord("=")):
                exposure += -1 if k == ord("-") else 1
                cam.set_exposure(exposure)
                time.sleep(0.05)
                print(f"exposure requested {exposure:g}, camera reports "
                      f"{cam.stats.applied.get('exposure')}")
    finally:
        cam.stop()
        guns.close_all(links)
        cv2.destroyAllWindows()
        print(f"\nFinal values for config/default.toml:  threshold = {thr}   "
              f"exposure = {exposure:g}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Live laser-dot detection preview.")
    ap.add_argument("--config", default=None, help="Path to config TOML.")
    ap.add_argument("--laser", action="store_true",
                    help="Find the guns over serial and turn their lasers on.")
    args = ap.parse_args()
    _preview(config.load(args.config), laser=args.laser)


if __name__ == "__main__":
    main()
