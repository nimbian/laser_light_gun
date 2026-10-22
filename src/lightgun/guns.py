"""Serial link to the guns: newline-delimited JSON over USB CDC @115200.

Each gun is a Pico running firmware/pico/main.py. The protocol:

  Gun -> PC:  {"e":"hello","id":"p1","fw":"0.1.0"}   on boot, and in reply to {"c":"hello"}
              {"e":"trigger","s":1|0}                 press / release
              {"e":"hb"}                              heartbeat (~2s)
  PC -> Gun:  {"c":"hello"}                           ask the gun to announce itself
              {"c":"laser","s":1|0}                   laser enable on/off
              {"c":"mode","m":"always"|"on_fire","pulse_ms":N}   laser mode
              {"c":"identify"}                        blink laser to identify

The boot-time hello is usually lost (nobody has the port open yet), so
discovery opens each candidate port and asks with {"c":"hello"}.

Each GunLink runs a reader thread and hands every message to a callback as
(gun_id, message, perf_counter_timestamp). The timestamp is taken on receipt
so the trigger can be paired with the tracked position at that moment.

Run directly for hardware bring-up (list guns, watch events, toggle lasers):
    python -m lightgun.guns
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import serial
from serial.tools import list_ports

from . import config

BAUD = 115200
# USB vendor id CircuitPython boards enumerate with (Adafruit's VID).
CIRCUITPY_VID = 0x239A

EventCallback = Callable[[str, dict[str, Any], float], None]

LASER_MODES = ("always", "on_fire")


def laser_settings(cfg: dict[str, Any], gun_id: str) -> tuple[str, int, int]:
    """(mode, fire_pulse_ms, fire_window_ms) for one gun: [laser] with per-gun override."""
    las = cfg.get("laser", {})
    mode = str(cfg.get("guns", {}).get(gun_id, {}).get("laser_mode", las.get("mode", "always")))
    if mode not in LASER_MODES:
        raise ValueError(f"{gun_id}: laser mode {mode!r} must be one of {LASER_MODES}")
    return mode, int(las.get("fire_pulse_ms", 100)), int(las.get("fire_window_ms", 250))


def parse_line(raw: bytes) -> dict[str, Any] | None:
    """Decode one protocol line. Returns None for blank or malformed input."""
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    try:
        msg = json.loads(text)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def encode(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class GunLink:
    """One open serial connection to one gun."""

    def __init__(self, port: str, ser: Any = None):
        self.port = port
        # `ser` lets tests inject a fake; normally we open the real port.
        self._ser = ser if ser is not None else serial.Serial(port, BAUD, timeout=0.05)
        self.gun_id: str | None = None
        self.fw: str | None = None
        self.last_seen = 0.0
        self._write_lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._on_event: EventCallback | None = None

    def handshake(self, timeout: float = 0.6) -> str | None:
        """Ask the gun for its hello. Returns its id, or None if nothing answered."""
        self.send({"c": "hello"})
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            msg = parse_line(self._ser.readline())
            if msg and msg.get("e") == "hello":
                self.gun_id = str(msg.get("id"))
                self.fw = msg.get("fw")
                self.last_seen = time.perf_counter()
                return self.gun_id
        return None

    def send(self, obj: dict[str, Any]) -> None:
        with self._write_lock:
            self._ser.write(encode(obj))

    def laser(self, on: bool) -> None:
        self.send({"c": "laser", "s": 1 if on else 0})

    def set_mode(self, mode: str, pulse_ms: int = 100) -> None:
        if mode not in LASER_MODES:
            raise ValueError(f"laser mode {mode!r} must be one of {LASER_MODES}")
        self.send({"c": "mode", "m": mode, "pulse_ms": int(pulse_ms)})

    def enable(self, cfg: dict[str, Any], force_always: bool = False) -> str:
        """Apply this gun's configured laser mode and enable the laser. Returns the mode.

        force_always is for calibration and the detect preview, which need a steady dot.
        """
        assert self.gun_id is not None
        mode, pulse_ms, _ = laser_settings(cfg, self.gun_id)
        if force_always:
            mode = "always"
        self.set_mode(mode, pulse_ms)
        self.laser(True)
        return mode

    def identify(self) -> None:
        self.send({"c": "identify"})

    def start(self, on_event: EventCallback) -> None:
        self._on_event = on_event
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"gun-{self.port}", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            try:
                raw = self._ser.readline()
            except serial.SerialException:
                print(f"[guns] {self.gun_id or self.port}: serial error, link closed")
                self._running = False
                break
            msg = parse_line(raw)
            if msg is None:
                continue
            now = time.perf_counter()
            self.last_seen = now
            if msg.get("e") == "hello":
                self.gun_id = str(msg.get("id"))
                self.fw = msg.get("fw")
            if self._on_event is not None and self.gun_id is not None:
                self._on_event(self.gun_id, msg, now)

    def close(self, laser_off: bool = True) -> None:
        self._running = False
        if laser_off:
            try:
                self.laser(False)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        try:
            self._ser.close()
        except Exception:
            pass


def _probe(port: str, timeout: float) -> GunLink | None:
    try:
        link = GunLink(port)
    except (serial.SerialException, OSError):
        return None  # in use by another program, or vanished
    if link.handshake(timeout) is None:
        link.close(laser_off=False)
        return None
    return link


def discover(extra_ports: list[str] | None = None, timeout: float = 0.6) -> dict[str, GunLink]:
    """Find guns on CircuitPython serial ports (plus any explicitly named ports).

    Only CircuitPython-VID ports are probed automatically so we don't write JSON
    at unrelated serial devices. Each Pico exposes two ports (REPL console and
    data); only the data port answers the hello.
    """
    ports = [p.device for p in list_ports.comports() if p.vid == CIRCUITPY_VID]
    for p in extra_ports or []:
        if p not in ports:
            ports.append(p)
    found: dict[str, GunLink] = {}
    for port in ports:
        link = _probe(port, timeout)
        if link is None:
            continue
        if link.gun_id in found:
            print(f"[guns] duplicate id {link.gun_id!r} on {port}; check GUN_ID in firmware")
            link.close(laser_off=False)
            continue
        found[link.gun_id] = link  # type: ignore[index]
    return found


def connect(cfg: dict[str, Any], on_event: EventCallback) -> dict[str, GunLink]:
    """Open every gun named in [guns] that can be found. Missing guns are reported, not fatal."""
    wanted = cfg.get("guns", {})
    explicit = [
        str(g["serial_port"]) for g in wanted.values()
        if str(g.get("serial_port", "auto")).lower() != "auto"
    ]
    found = discover(explicit)
    links: dict[str, GunLink] = {}
    for gid in wanted:
        link = found.pop(gid, None)
        if link is None:
            print(f"[guns] {gid}: not found")
            continue
        print(f"[guns] {gid}: {link.port} (fw {link.fw})")
        link.start(on_event)
        links[gid] = link
    for gid, link in found.items():
        print(f"[guns] ignoring {gid!r} on {link.port}: not in config [guns]")
        link.close(laser_off=False)
    return links


def close_all(links: dict[str, GunLink]) -> None:
    for link in links.values():
        link.close()


# -- bring-up CLI -------------------------------------------------------------


def _monitor(cfg: dict[str, Any], laser: bool) -> None:
    def show(gid: str, msg: dict[str, Any], t: float) -> None:
        if msg.get("e") != "hb":
            print(f"{t:10.3f}  {gid}  {msg}")

    links = connect(cfg, show)
    if not links:
        print("No guns found. Is boot.py on the Pico (it enables the data port)?")
        return
    for link in links.values():
        link.identify()
        if laser:
            print(f"[guns] {link.gun_id}: laser mode {link.enable(cfg)}")
    print("Watching events; Ctrl+C to quit.")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        close_all(links)


def main() -> None:
    ap = argparse.ArgumentParser(description="Find guns and print their serial events.")
    ap.add_argument("--config", default=None, help="Path to config TOML.")
    ap.add_argument("--laser", action="store_true", help="Turn lasers on while watching.")
    args = ap.parse_args()
    _monitor(config.load(args.config), args.laser)


if __name__ == "__main__":
    main()
