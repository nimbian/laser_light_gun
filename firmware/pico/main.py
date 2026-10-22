# CircuitPython firmware for a light gun (Raspberry Pi Pico / RP2040).
#
# Phase 2 scope: read the trigger button, control the laser, and speak the
# newline-delimited JSON serial protocol over USB CDC.
#
#   Gun -> PC:  {"e":"hello","id":"p1","fw":"0.3.0"}   on boot and on request
#               {"e":"trigger","s":1}                   press
#               {"e":"trigger","s":0}                   release
#               {"e":"hb"}                              heartbeat (~2s)
#   PC -> Gun:  {"c":"hello"}                           re-send hello (discovery)
#               {"c":"laser","s":1}                     laser enable on/off
#               {"c":"mode","m":"always","pulse_ms":100} laser mode (see below)
#               {"c":"identify"}                        blink laser to identify
#
# Laser modes (the PC sets one per gun from config; "always" after reset):
#   always   - while enabled, the laser is on continuously (aim tracked every frame).
#   on_fire  - while enabled, the laser lights only on a trigger pull: on the
#              press edge, for at least pulse_ms, and for as long as it's held.
#              Lit here on the gun, so there's no USB round trip before the dot.
#
# Copy boot.py alongside this file: it enables the separate USB data port so
# the protocol doesn't share the REPL console. Changes to boot.py take effect
# after a hard reset (unplug/replug).
#
# Wiring (adjust pins to your build):
#   Trigger switch: GP15 to GND, using internal pull-up.
#   Laser gate:     GP16 -> MOSFET gate (laser switched on the high side / low
#                   side per your driver). HIGH = on.
#
# Set GUN_ID per gun: "p1" for the red gun, "p2" for the green gun.

import json
import time

import board
import digitalio
import usb_cdc

GUN_ID = "p1"
FW = "0.3.0"

trigger = digitalio.DigitalInOut(board.GP15)
trigger.direction = digitalio.Direction.INPUT
trigger.pull = digitalio.Pull.UP  # pressed = reads False

laser = digitalio.DigitalInOut(board.GP16)
laser.direction = digitalio.Direction.OUTPUT
laser.value = False  # SAFETY: laser off at boot

serial = usb_cdc.data if usb_cdc.data is not None else usb_cdc.console
serial.timeout = 0  # never block the trigger loop on a partial line

laser_wanted = False  # PC's enable; identify blinks then restores it
mode_on_fire = False
pulse_s = 0.1
fire_until = 0.0
blink_until = 0.0
rx = b""
MAX_LINE = 256


def send(obj):
    try:
        serial.write((json.dumps(obj) + "\n").encode("utf-8"))
    except Exception:
        pass


def read_lines():
    """Return complete lines received so far, keeping any partial tail."""
    global rx
    n = serial.in_waiting
    if n:
        rx += serial.read(n)
    lines = []
    while True:
        i = rx.find(b"\n")
        if i < 0:
            break
        lines.append(rx[:i])
        rx = rx[i + 1:]
    if len(rx) > MAX_LINE:
        rx = b""  # garbage with no newline; drop it
    return lines


def handle_command(raw):
    global laser_wanted, blink_until, mode_on_fire, pulse_s
    try:
        cmd = json.loads(raw.decode("utf-8"))
    except ValueError:
        return
    c = cmd.get("c")
    if c == "laser":
        laser_wanted = bool(cmd.get("s", 0))
    elif c == "mode":
        mode_on_fire = cmd.get("m") == "on_fire"
        pulse_s = max(0.0, float(cmd.get("pulse_ms", 100))) / 1000.0
    elif c == "identify":
        blink_until = time.monotonic() + 0.8
    elif c == "hello":
        send({"e": "hello", "id": GUN_ID, "fw": FW})


DEBOUNCE_S = 0.015
HEARTBEAT_S = 2.0

send({"e": "hello", "id": GUN_ID, "fw": FW})

last_state = True          # True = not pressed (pull-up)
last_change = time.monotonic()
last_hb = time.monotonic()

while True:
    now = time.monotonic()

    # Lockout debounce: report the first edge immediately (lowest latency),
    # then ignore further edges for DEBOUNCE_S.
    cur = trigger.value          # True = released (active low)
    if cur != last_state and (now - last_change) > DEBOUNCE_S:
        last_state = cur
        last_change = now
        if not cur:
            fire_until = now + pulse_s  # light first, then tell the PC
        send({"e": "trigger", "s": 0 if cur else 1})

    for line in read_lines():
        handle_command(line)

    # SAFETY: the laser only runs while a host holds the port open (DTR).
    # The mode resets too, so the next session starts from the default.
    if not serial.connected:
        laser_wanted = False
        mode_on_fire = False
        blink_until = 0.0

    if now < blink_until:
        laser.value = int(now * 10) % 2 == 0
    elif mode_on_fire:
        laser.value = laser_wanted and (not last_state or now < fire_until)
    else:
        laser.value = laser_wanted

    if now - last_hb > HEARTBEAT_S:
        last_hb = now
        send({"e": "hb"})

    time.sleep(0.001)
