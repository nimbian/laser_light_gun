"""Command-line runtime: track one gun's dot, drive the cursor, click on trigger.

A thin wrapper over engine.Engine (the GUI uses the same engine). The cursor
is driven on the monitor chosen by [display] monitor.

Run:  python -m lightgun.app [--gun p1] [--dry-run]
      (or the `lightgun` console script). Ctrl+C to quit.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from . import config
from .engine import Engine


def run(cfg: dict[str, Any], gun_id: str, dry_run: bool) -> None:
    if not cfg["calibration"].get("homography"):
        raise SystemExit("No homography in config; run calibration first.  "
                         "(python -m lightgun.calibrate)")
    engine = Engine(cfg, gun_id)
    engine.start()
    try:
        try:
            engine.start_injection(dry_run)
        except ValueError as e:
            raise SystemExit(f"{e}  (python -m lightgun.calibrate)") from None
        if gun_id not in engine.links:
            print(f"[app] {gun_id} not connected: cursor will track, but there is no trigger.")
        m = engine.monitor
        assert m is not None
        print(f"[app] {gun_id} driving the cursor on {m.label()}, laser mode {engine.mode}"
              f"{' (dry run)' if dry_run else ''}. Ctrl+C to quit.")
        while engine.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()


def main() -> None:
    ap = argparse.ArgumentParser(description="Light gun runtime (single player).")
    ap.add_argument("--config", default=None, help="Config TOML (default: the user config).")
    ap.add_argument("--gun", default="p1", help="Gun that drives the cursor (default p1).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Log clicks instead of injecting input.")
    args = ap.parse_args()
    run(config.load(args.config), args.gun, args.dry_run)


if __name__ == "__main__":
    main()
