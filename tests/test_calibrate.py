"""Calibration write-back into the TOML config."""

from __future__ import annotations

from lightgun import config
from lightgun.calibrate import _grid_targets, _write_back

RESULT = {
    "homography": [[1.5, 0.0, -10.25], [0.0, 1.5, 3.0], [1e-05, 0.0, 1.0]],
    "captured_width": 1920,
    "captured_height": 1080,
    "camera_width": 1280,
    "camera_height": 720,
}


def test_write_back_roundtrips_default_config(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_text(config.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    _write_back(p, RESULT)
    cfg = config.load(p)
    for k, v in RESULT.items():
        assert cfg["calibration"][k] == v
    # Untouched sections survive.
    assert cfg["calibration"]["grid_cols"] == 3
    assert cfg["guns"]["p1"]["color"] == "red"


def test_write_back_appends_missing_keys_inside_section(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_text(
        "[calibration]\ngrid_cols = 3\nhomography = []\n\n[filter]\nbeta = 0.1\n",
        encoding="utf-8",
    )
    _write_back(p, RESULT)
    cfg = config.load(p)
    assert cfg["calibration"]["camera_width"] == 1280
    assert cfg["calibration"]["captured_height"] == 1080
    assert cfg["filter"] == {"beta": 0.1}


def test_grid_targets_inset_and_ordered():
    t = _grid_targets(1000, 500, 3, 3)
    assert t[0] == (100, 50) and t[-1] == (900, 450)
    assert len(t) == 9
