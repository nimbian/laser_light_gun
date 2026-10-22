"""User config creation, default merging, and comment-preserving saves."""

from __future__ import annotations

from lightgun import config


def test_user_config_seeded_from_bundled_default(isolated_user_config):
    assert not isolated_user_config.exists()
    cfg = config.load()
    assert isolated_user_config.exists()
    assert cfg["detection"]["threshold"] == 220
    assert cfg["display"]["monitor"] == "primary"


def test_missing_keys_filled_from_defaults(isolated_user_config):
    isolated_user_config.parent.mkdir(parents=True)
    isolated_user_config.write_text(
        "[detection]\nthreshold = 240\n\n[guns.p1]\nhue_ranges = [[0, 10]]\n",
        encoding="utf-8",
    )
    cfg = config.load()
    assert cfg["detection"]["threshold"] == 240          # user value wins
    assert cfg["detection"]["min_area"] == 2.0           # filled from default
    assert cfg["guns"]["p1"]["hue_ranges"] == [[0, 10]]
    assert cfg["guns"]["p1"]["color"] == "red"
    assert cfg["laser"]["mode"] == "always"              # whole section added


def test_save_values_preserves_comments_and_removes_none(isolated_user_config):
    p = config.ensure_user_config()
    config.save_values(p, {
        "detection": {"threshold": 231},
        "guns": {"p2": {"laser_mode": "on_fire", "offset": (1.5, -2)}},
    })
    text = p.read_text(encoding="utf-8")
    assert "# Grayscale brightness threshold" in text
    cfg = config.load(p)
    assert cfg["detection"]["threshold"] == 231
    assert cfg["guns"]["p2"]["laser_mode"] == "on_fire"
    assert cfg["guns"]["p2"]["offset"] == [1.5, -2]

    config.save_values(p, {"guns": {"p2": {"laser_mode": None}}})
    assert "laser_mode" not in config.load(p)["guns"]["p2"]
