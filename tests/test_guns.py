"""Serial protocol parsing and GunLink behavior against a fake port."""

from __future__ import annotations

import json
import threading
import time

from lightgun.guns import GunLink, parse_line


class FakeSerial:
    """Minimal pyserial stand-in: queued incoming lines, recorded writes."""

    def __init__(self, lines: list[bytes] | None = None, hello_id: str | None = "p1"):
        self._incoming = list(lines or [])
        self._lock = threading.Lock()
        self.written: list[dict] = []
        self.hello_id = hello_id
        self.closed = False

    def write(self, data: bytes) -> int:
        msg = json.loads(data)
        self.written.append(msg)
        if msg == {"c": "hello"} and self.hello_id:
            self.feed({"e": "hello", "id": self.hello_id, "fw": "0.2.0"})
        return len(data)

    def feed(self, obj: dict) -> None:
        with self._lock:
            self._incoming.append((json.dumps(obj) + "\n").encode())

    def readline(self) -> bytes:
        with self._lock:
            if self._incoming:
                return self._incoming.pop(0)
        time.sleep(0.005)
        return b""

    def close(self) -> None:
        self.closed = True


def test_parse_line():
    assert parse_line(b'{"e":"trigger","s":1}\r\n') == {"e": "trigger", "s": 1}
    assert parse_line(b"") is None
    assert parse_line(b"   \n") is None
    assert parse_line(b"not json\n") is None
    assert parse_line(b"[1,2]\n") is None  # valid JSON but not a message
    assert parse_line(b"\xff\xfe\n") is None


def test_handshake_reads_id_past_other_traffic():
    ser = FakeSerial(lines=[b'{"e":"hb"}\n', b"garbage\n"])
    link = GunLink("COM9", ser=ser)
    assert link.handshake(timeout=0.5) == "p1"
    assert link.fw == "0.2.0"
    assert ser.written[0] == {"c": "hello"}


def test_handshake_times_out_on_silent_port():
    link = GunLink("COM9", ser=FakeSerial(hello_id=None))
    assert link.handshake(timeout=0.05) is None


def test_events_dispatched_with_gun_id_and_laser_off_on_close():
    ser = FakeSerial()
    link = GunLink("COM9", ser=ser)
    link.handshake(timeout=0.5)
    got: list[tuple[str, dict]] = []
    done = threading.Event()

    def on_event(gid, msg, t):
        got.append((gid, msg))
        if msg.get("e") == "trigger" and msg.get("s") == 0:
            done.set()

    link.start(on_event)
    ser.feed({"e": "trigger", "s": 1})
    ser.feed({"e": "trigger", "s": 0})
    assert done.wait(1.0)
    link.close()
    assert got == [("p1", {"e": "trigger", "s": 1}), ("p1", {"e": "trigger", "s": 0})]
    assert ser.written[-1] == {"c": "laser", "s": 0}
    assert ser.closed


def test_enable_sends_mode_then_laser_and_calibration_forces_always():
    cfg = {"laser": {"mode": "on_fire", "fire_pulse_ms": 120}, "guns": {"p1": {}}}
    ser = FakeSerial()
    link = GunLink("COM9", ser=ser)
    link.handshake(timeout=0.5)
    assert link.enable(cfg) == "on_fire"
    assert ser.written[-2:] == [
        {"c": "mode", "m": "on_fire", "pulse_ms": 120},
        {"c": "laser", "s": 1},
    ]
    assert link.enable(cfg, force_always=True) == "always"
    assert ser.written[-2]["m"] == "always"
