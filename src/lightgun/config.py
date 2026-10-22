"""Config loading. Thin wrapper over tomllib so every module reads the same file."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

# Repo root = three parents up from this file (src/lightgun/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.toml"


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Load the TOML config into a plain dict."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(p, "rb") as f:
        return tomllib.load(f)
