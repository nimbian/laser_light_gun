# lightgun

Camera-based light gun system. A stationary camera watches the screen; each gun
emits a visible laser dot (P1 red 650nm, P2 green 520nm). Software detects the
dot, maps it to screen pixels via a calibrated homography, and (later phases)
injects pointer input so games see a light gun.

This repo covers **Phase 0 (detection)**, **Phase 1 (calibration)** and
**Phase 2 (gun serial link + Windows click injection, single player)**.

## Setup

Requires Python 3.11+.

```powershell
cd lightgun
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Open the folder in VS Code; `.vscode/settings.json` points the interpreter at
`.venv`, and the analyzer and pytest at `src/`. `launch.json` has run configs
for the detect preview, calibration, the gun monitor, and the runtime.

## Run the tests (no hardware needed)

```powershell
pytest -q
```

The tests run the full detection and homography pipeline against synthetically
generated frames, so everything is verifiable before any gun is built.

## Phase 0 — prove detection

1. Plug in the camera (C920 for now). In `config/default.toml` confirm
   `[camera] index` (usually 0) and `backend = "dshow"`.
2. Run the live preview:
   ```powershell
   python -m lightgun.detect
   ```
3. On launch it prints the camera settings it actually applied and **warns if
   exposure did not lock**. If it warns, the scene won't go dark enough for
   clean detection — fix this before anything else:
   - Try switching `backend` between `"dshow"` and `"msmf"`.
   - Use the Logitech "Camera Settings" app to confirm the C920 accepts manual
     exposure, and adjust the `exposure` value (more negative = darker).
4. Aim the red laser at the screen. You want the dot circled with a stable
   centroid, and **zero** false blobs even on bright white game footage. Use
   `[` / `]` to nudge the threshold and `-` / `=` to step exposure live. On
   quit it prints the final values to copy into `config/default.toml`.
   (With a Pico-driven laser, add `--laser` so the preview switches it on.)

**Make-or-break check:** point the camera at the brightest, whitest footage you
can find with the laser on. If the dot stays cleanly separated, the rest of the
project is downhill. If not, crush exposure further or add the polarizer/IR
discussion from the plan.

## Phase 1 — calibrate

1. With detection working, run:
   ```powershell
   python -m lightgun.calibrate
   ```
2. A fullscreen 3x3 grid of targets appears on the primary display. Aim at the
   highlighted target and fire. With a gun connected, the trigger records the
   shot. Without one, hold steady and it auto-accepts (or press SPACE). `r`
   steps back to redo the previous target, ESC aborts.
3. It computes the homography, prints the mean reprojection error (want < ~3 px),
   and writes the matrix back into `config/default.toml` under `[calibration]`,
   along with the screen and camera resolutions it was measured at.

## Phase 2 — play

```powershell
python -m lightgun.guns              # bring-up: find guns, blink lasers, print trigger events
python -m lightgun.app --dry-run     # track + log clicks without touching the cursor
python -m lightgun.app               # drive the Windows cursor; Ctrl+C to quit
```

The runtime moves the cursor to the P1 dot each frame and, on a trigger press,
clicks at the latest tracked position (held at least `click_hold_ms`). Only one
gun drives the cursor (`--gun p2` to switch); true two-player is Phase 4.

**Laser mode** (`[laser] mode` in the config, or `laser_mode` per gun):

- `"always"` (default): the laser stays on, aim is tracked every frame, and a
  pull clicks immediately at the tracked position.
- `"on_fire"`: the gun lights the laser only when the trigger is pulled, and the
  click lands where the dot first appears. No visible dot between shots, at the
  cost of at least one camera frame of delay per shot. Shots are dropped if no
  dot shows up within `fire_window_ms`; raise it if that happens.

Calibration and the detect preview always use `"always"`, since they need a
steady dot. If a
game runs as administrator, run this elevated too, or Windows drops the input.

## Gun firmware

`firmware/pico/main.py` is CircuitPython for the Pico: trigger debounce, laser
control, and the JSON serial protocol. Copy **both** `boot.py` and `main.py` to
the Pico's `CIRCUITPY` drive, then unplug/replug (boot.py enables a dedicated
USB data port, so the gun shows up as two COM ports; the PC finds the right one
automatically). Set `GUN_ID` to `"p1"` (red) or `"p2"` (green) per gun.

The laser is off at boot and only runs while the PC holds the port open; it
switches off as soon as the PC software exits or the cable is pulled. In
`on_fire` mode the gun itself lights the laser on the trigger pull, so there's
no USB round trip before the dot appears.

## Layout

```
config/default.toml      all tunables (camera, detection, guns, calibration)
src/lightgun/
  capture.py             threaded camera, manual exposure/focus locking
  detect.py              blob detection + annulus hue classification + preview
  mapping.py             homography apply + compute
  calibrate.py           fullscreen 3x3 calibration app
  guns.py                serial link to the guns (discovery, events, laser)
  inject/windows.py      SendInput absolute move + click
  app.py                 Phase 2 runtime: track, move cursor, click on trigger
  screen.py              primary-display size (DPI aware)
  config.py              TOML loader
firmware/pico/           gun firmware (CircuitPython): boot.py + main.py
tests/                   pytest suite + synthetic frame generator
```

## Safety

- Red 650nm and green 520nm modules, Class 2, under 1mW. **Direct-diode green
  only — never 532nm DPSS** (those can leak invisible IR).
- The firmware forces the laser off at boot. Size a current-limiting resistor so
  the diode can't exceed its rating even if a GPIO sticks high.

## Next phases (not yet built)

3. Robustness, One Euro filter, latency harness, diagnostics overlay.
4. Two-player via per-gun USB HID absolute pointers.
5. Polish: off-screen reload, per-game offsets, recoil.
6. Packaged Windows executable with all settings and calibration built in.
