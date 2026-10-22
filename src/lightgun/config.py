"""Config loading and saving, shared by every module, the CLIs and the GUI.

The bundled default (default_config.toml, shipped inside the package and the
frozen exe) is the template. The user's own config lives at
%APPDATA%\\lightgun\\config.toml, or wherever LIGHTGUN_CONFIG points. It is
created from the default on first use, and any keys a newer version adds are
filled in from the default when loading, so old configs keep working.

Saving goes through tomlkit so comments and layout in the user's file survive.
"""

from __future__ import annotations

import copy
import os
import shutil
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import tomlkit

APP_NAME = "lightgun"


def bundled_default_path() -> Path:
    """The default config shipped with the package (works frozen and editable)."""
    return Path(str(resources.files("lightgun") / "default_config.toml"))


def user_config_path() -> Path:
    env = os.environ.get("LIGHTGUN_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "config.toml"


def ensure_user_config(path: Path | None = None) -> Path:
    """Create the user config from the bundled default if it doesn't exist yet."""
    p = Path(path) if path else user_config_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundled_default_path(), p)
    return p


def _merge_defaults(values: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge: keys missing from `values` are taken from `defaults`."""
    out = copy.deepcopy(defaults)
    for k, v in values.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_defaults(v, out[k])
        else:
            out[k] = v
    return out


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Load a config into a plain dict, with defaults filled in for missing keys.

    With no path, loads (creating if needed) the user config.
    """
    p = Path(path) if path else ensure_user_config()
    with open(p, "rb") as f:
        values = tomllib.load(f)
    with open(bundled_default_path(), "rb") as f:
        defaults = tomllib.load(f)
    return _merge_defaults(values, defaults)


def resolve_path(path: str | Path | None = None) -> Path:
    """The file `load(path)` reads and `save_values(path, ...)` writes."""
    return Path(path) if path else ensure_user_config()


def _plain(value: Any) -> Any:
    """numpy scalars/arrays and tuples -> plain Python values tomlkit can write."""
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def save_values(path: str | Path | None, updates: dict[str, Any]) -> Path:
    """Write values into the config file, preserving its comments and layout.

    `updates` is nested like the config, e.g. {"detection": {"threshold": 230},
    "guns": {"p1": {"offset": [0, 4]}}}. Tables that don't exist yet are created;
    a value of None removes that key.
    """
    p = resolve_path(path)
    doc = tomlkit.parse(p.read_text(encoding="utf-8"))

    def apply(table: Any, upd: dict[str, Any]) -> None:
        for k, v in upd.items():
            if isinstance(v, dict):
                if k not in table:
                    table[k] = tomlkit.table()
                apply(table[k], v)
            elif v is None:
                if k in table:
                    del table[k]
            else:
                table[k] = _plain(v)

    apply(doc, updates)
    p.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return p
