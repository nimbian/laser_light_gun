"""The settings schema covers every config option and round-trips values."""

from __future__ import annotations

import tomllib

import pytest

from lightgun import config, settings


def _leaf_keys(d, prefix=()):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _leaf_keys(v, (*prefix, k))
        else:
            yield (*prefix, k)


def test_every_default_config_key_is_editable_or_managed():
    with open(config.bundled_default_path(), "rb") as f:
        default = tomllib.load(f)
    described = {f.path for _title, fields in settings.pages(default) for f in fields}
    missing = [
        ".".join(k) for k in _leaf_keys(default)
        if k not in described and k not in settings.MANAGED
    ]
    assert missing == [], f"config keys with no settings field: {missing}"


def test_described_fields_exist_in_default_unless_optional():
    default = config.load(config.bundled_default_path())
    for _title, fields in settings.pages(default):
        for f in fields:
            if not f.optional:
                assert settings.get_value(default, f) is not None, ".".join(f.path)


def test_ranges_parse_and_format():
    assert settings.parse_ranges("0-12, 168-180") == [[0, 12], [168, 180]]
    assert settings.format_ranges([[0, 12], [168, 180]]) == "0-12, 168-180"
    for bad in ("", "12-0", "0-200", "red"):
        with pytest.raises(ValueError):
            settings.parse_ranges(bad)


def test_updates_for_roundtrip_through_save(isolated_user_config):
    p = config.ensure_user_config()
    cfg = config.load(p)
    fields = [f for _t, fs in settings.pages(cfg) for f in fs]
    by_path = {f.path: f for f in fields}
    settings.set_value(cfg, by_path[("detection", "threshold")], 205)
    settings.set_value(cfg, by_path[("guns", "p1", "laser_mode")], "on_fire")
    config.save_values(p, settings.updates_for(cfg, fields))
    again = config.load(p)
    assert again["detection"]["threshold"] == 205
    assert again["guns"]["p1"]["laser_mode"] == "on_fire"

    settings.set_value(cfg, by_path[("guns", "p1", "laser_mode")], "")  # back to default
    config.save_values(p, settings.updates_for(cfg, fields))
    assert "laser_mode" not in config.load(p)["guns"]["p1"]
