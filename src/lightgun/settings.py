"""Describes every user-facing config option, for the GUI's settings forms.

Kept free of Qt so it can be tested: tests check that every key in the bundled
default config is either described here or listed in MANAGED (values the app
writes itself, like the homography). Adding a config key without a field here
fails the test suite, which keeps "every option is editable in the app" true.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Keys the app manages itself; shown read-only or not at all.
MANAGED = {
    ("calibration", "homography"),
    ("calibration", "captured_width"),
    ("calibration", "captured_height"),
    ("calibration", "camera_width"),
    ("calibration", "camera_height"),
}


@dataclass(frozen=True)
class Field:
    section: tuple[str, ...]   # e.g. ("detection",) or ("guns", "p1")
    key: str
    label: str
    kind: str                  # int | float | choice | text | monitor | port | ranges | pair
    help: str = ""
    minimum: float = 0
    maximum: float = 0
    step: float = 1
    decimals: int = 0
    choices: tuple[str, ...] = ()
    live: bool = False         # applied to the running engine immediately on edit
    restart: bool = False      # needs the camera/guns reopened to take effect
    optional: bool = False     # may be absent from the config ("" = unset)

    @property
    def path(self) -> tuple[str, ...]:
        return (*self.section, self.key)


def _f(section: str, key: str, label: str, kind: str, help: str = "", **kw: Any) -> Field:
    return Field(tuple(section.split(".")), key, label, kind, help, **kw)


# Tabs in display order: (title, fields).
STATIC_PAGES: list[tuple[str, list[Field]]] = [
    ("Display", [
        _f("display", "monitor", "Game monitor", "monitor",
           "The monitor the game runs on. Calibration and the cursor use it."),
    ]),
    ("Camera", [
        _f("camera", "index", "Camera index", "int", "OpenCV device index; usually 0.",
           maximum=10, restart=True),
        _f("camera", "backend", "Backend", "choice",
           "DirectShow usually honors manual exposure on the C920; Media Foundation "
           "often fights it.", choices=("dshow", "msmf", "any"), restart=True),
        _f("camera", "width", "Width", "int", maximum=4096, restart=True),
        _f("camera", "height", "Height", "int", maximum=4096, restart=True),
        _f("camera", "fps", "Frame rate", "int", maximum=260, restart=True),
        _f("camera", "fourcc", "Pixel format", "choice",
           "MJPG unlocks the high frame-rate modes on UVC cameras.",
           choices=("MJPG", "YUY2"), restart=True),
        _f("camera", "autofocus", "Autofocus", "int",
           "0 = off. Keep it off: in a crushed scene autofocus hunts and moves the dot.",
           maximum=1, restart=True),
        _f("camera", "focus", "Focus", "int", "Fixed focus, set once the screen is sharp.",
           maximum=255, restart=True),
        _f("camera", "auto_exposure", "Auto-exposure value", "float",
           "OpenCV quirk: 0.25 (or 1) = manual on most builds.",
           maximum=3, step=0.25, decimals=2, restart=True),
        _f("camera", "exposure", "Exposure", "float",
           "Negative log2 seconds on DirectShow. More negative = darker. Lower it until "
           "only the laser dot survives.", minimum=-13, maximum=0, step=1, decimals=0,
           live=True),
        _f("camera", "gain", "Gain", "float", maximum=255, decimals=0, restart=True),
    ]),
    ("Detection", [
        _f("detection", "threshold", "Brightness threshold", "int",
           "A pixel must be brighter than this to count as part of a dot. Raise it until "
           "bright game footage never crosses it.", maximum=255, live=True),
        _f("detection", "min_area", "Min dot area (px)", "float",
           "Rejects sensor noise.", maximum=10000, decimals=1, live=True),
        _f("detection", "max_area", "Max dot area (px)", "float",
           "Rejects glare and reflections.", maximum=100000, decimals=1, live=True),
        _f("detection", "min_circularity", "Min circularity", "float",
           "1 = perfect circle. Loose so a motion-smeared dot still passes.",
           maximum=1, step=0.05, decimals=2, live=True),
        _f("detection", "annulus_inner", "Hue ring inner radius", "float",
           "Color is read from a ring around the dot because the core clips to white.",
           maximum=100, step=0.5, decimals=1, live=True),
        _f("detection", "annulus_outer", "Hue ring outer radius", "float",
           maximum=100, step=0.5, decimals=1, live=True),
    ]),
    ("Laser", [
        _f("laser", "mode", "Laser mode", "choice",
           "always: laser on, aim tracked every frame, instant clicks. on_fire: laser "
           "lights only when the trigger is pulled; no visible dot between shots, but "
           "at least one camera frame of delay per shot.", choices=("always", "on_fire")),
        _f("laser", "fire_pulse_ms", "On-fire pulse (ms)", "int",
           "Minimum time the laser stays lit per pull.", maximum=1000),
        _f("laser", "fire_window_ms", "On-fire window (ms)", "int",
           "Drop the shot if no dot appears within this long. Raise it if shots go "
           "missing.", maximum=2000),
    ]),
    ("Calibration", [
        _f("calibration", "grid_cols", "Grid columns", "int", minimum=2, maximum=6),
        _f("calibration", "grid_rows", "Grid rows", "int", minimum=2, maximum=6),
        _f("calibration", "stable_frames", "Stable frames to auto-accept", "int",
           "Without a gun connected, a target is accepted once the dot holds still this "
           "many frames.", minimum=1, maximum=120),
    ]),
    ("Input", [
        _f("injection", "backend", "Injection backend", "choice", choices=("windows",)),
        _f("injection", "click_hold_ms", "Minimum click hold (ms)", "int",
           "Short trigger pulls are held at least this long so games register them.",
           maximum=500),
    ]),
    ("Filter", [
        _f("filter", "min_cutoff", "Min cutoff", "float",
           "Phase 3 (not active yet). Lower = more smoothing at rest.",
           maximum=20, step=0.1, decimals=3),
        _f("filter", "beta", "Beta", "float", "Higher = less lag during fast motion.",
           maximum=1, step=0.001, decimals=4),
        _f("filter", "d_cutoff", "Derivative cutoff", "float",
           maximum=20, step=0.1, decimals=3),
    ]),
]


def gun_fields(gun_id: str) -> list[Field]:
    s = f"guns.{gun_id}"
    return [
        _f(s, "color", "Color name", "text", "Label only; detection uses the hue ranges."),
        _f(s, "hue_ranges", "Hue ranges", "ranges",
           "OpenCV hue 0-180, e.g. 0-12, 168-180 for red (it wraps around 0)."),
        _f(s, "serial_port", "Serial port", "port",
           "auto finds the gun by asking each CircuitPython port.", restart=True),
        _f(s, "offset", "Aim offset (px)", "pair",
           "Added to every mapped position, x then y."),
        _f(s, "laser_mode", "Laser mode override", "choice",
           "Blank = use the Laser tab's mode.", choices=("", "always", "on_fire"),
           optional=True),
    ]


def pages(cfg: dict[str, Any]) -> list[tuple[str, list[Field]]]:
    """All settings tabs for this config: the static ones plus one per gun."""
    out = list(STATIC_PAGES)
    for gid in cfg.get("guns", {}):
        out.append((f"Gun {gid}", gun_fields(gid)))
    return out


def get_value(cfg: dict[str, Any], field: Field) -> Any:
    node: Any = cfg
    for part in field.section:
        node = node.get(part, {})
    if field.key not in node:
        return "" if field.optional else None
    return node[field.key]


def set_value(cfg: dict[str, Any], field: Field, value: Any) -> None:
    node = cfg
    for part in field.section:
        node = node.setdefault(part, {})
    if field.optional and value == "":
        node.pop(field.key, None)
    else:
        node[field.key] = value


def updates_for(cfg: dict[str, Any], fields: list[Field]) -> dict[str, Any]:
    """Nested {section: {key: value}} for config.save_values, from the given fields.

    Unset optional fields become None, which removes them from the file.
    """
    out: dict[str, Any] = {}
    for f in fields:
        value = get_value(cfg, f)
        if f.optional and value == "":
            value = None
        node = out
        for part in f.section:
            node = node.setdefault(part, {})
        node[f.key] = value
    return out


def format_ranges(ranges: list[list[int]]) -> str:
    return ", ".join(f"{lo}-{hi}" for lo, hi in ranges)


def parse_ranges(text: str) -> list[list[int]]:
    """'0-12, 168-180' -> [[0, 12], [168, 180]]. Raises ValueError on bad input."""
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        lo, hi = (int(v) for v in part.split("-"))
        if not (0 <= lo <= hi <= 180):
            raise ValueError(f"hue range {part!r} must be within 0-180, low first")
        out.append([lo, hi])
    if not out:
        raise ValueError("at least one hue range is required")
    return out
