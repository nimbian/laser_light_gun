"""Keep tests off the real user config and any real display."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    path = tmp_path / "appdata" / "lightgun" / "config.toml"
    monkeypatch.setenv("LIGHTGUN_CONFIG", str(path))
    return path
