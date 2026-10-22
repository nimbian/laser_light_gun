# lightgun

Camera-based light gun system. A stationary camera watches the screen; each gun
emits a visible laser dot (P1 red 650nm, P2 green 520nm). Software detects the
dot, maps it to screen pixels via a calibrated homography, and injects pointer
input so games see a light gun.

This repo covers **Phase 0 (detection)**, **Phase 1 (calibration)**, **Phase 2
(gun serial link + Windows click injection, single player)**, and the first
version of the **LightGun desktop app** (Phase 6), which can be packaged as a
Windows executable.

## Setup

Requires Python 3.11+.

```powershell
cd lightgun
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,build]"
```

Open the folder in VS Code; `.vscode/settings.json` points the interpreter at
`.venv`, and the analyzer and pytest at `src/`. `launch.json` has run configs
for the app, the detect preview, calibration, the gun monitor, and the runtime.

## Configuration

Your settings live in **`%APPDATA%\lightgun\config.toml`**. It's created from
the bundled default (`src/lightgun/default_config.toml`) the first time
anything runs, and settings added in later versions are filled in from the
default automatically. The app edits this file for you and keeps its comments.
Every command also takes `--config PATH`, and the `LIGHTGUN_CONFIG` environment
variable points everything at a different file.

## The LightGun app

```powershell
lightgun-gui                 # or: python -m lightgun.gui
```

It opens the camera straight away and shows a live preview with detected dots
circled, so it doubles as the detection-tuning view. From the window you can:

- edit every setting in tabs (detection and exposure changes apply instantly;
  others take effect with **Apply**; **Save** writes them to your config);
- pick the **game monitor** on the Display tab: calibration and the cursor use
  it, whichever display it is;
- **Calibrate**: fullscreen targets on the game monitor; shoot them with the
  gun, or hold a laser pointer steady when no gun is connected;
- **Play**: drive the cursor from the chosen gun (Dry run logs clicks instead);
- rescan or identify guns, restart the camera, and open the config folder.

Output goes to the log pane and to `%APPDATA%\lightgun\lightgun.log`.

### Build the Windows executable

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1           # dist\LightGun\LightGun.exe
powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1 -OneFile  # dist\LightGun.exe
```

The default build is a folder (about 240 MB) holding `LightGun.exe`. Zip the
folder to share it. `-OneFile` makes a single exe that unpacks itself on each
launch, so it starts slower. The script smoke-tests the built exe before
reporting success.

## Run the tests (no hardware needed)

```powershell
pytest -q
```

The tests run the full detection and homography pipeline against synthetically
generated frames, plus the config, settings, calibration, serial and GUI layers
(the GUI on Qt's offscreen platform), so everything is verifiable before any
gun is built.

## Phase 0 — prove detection

1. Plug in the camera (C920 for now) and check `[camera] index` (Camera tab in
   the app). Virtual cameras such as NVIDIA Broadcast can take index 0, so the
   C920 may be 1 or 2. Keep `backend = "dshow"`.
2. Open the app (or run `python -m lightgun.detect`).
3. The status line shows whether **exposure locked**. If it didn't, the scene
   won't go dark enough for clean detection — fix this before anything else:
   - Try switching `backend` between `"dshow"` and `"msmf"`.
   - Use the Logitech "Camera Settings" app to confirm the C920 accepts manual
     exposure, and lower `exposure` (more negative = darker).
4. Aim the red laser at the screen. You want the dot circled with a stable
   centroid, and **zero** false blobs even on bright white game footage. Adjust
   threshold and exposure on the Detection and Camera tabs while watching the
   preview, then Save. (In the CLI preview: `[` / `]` threshold, `-` / `=`
   exposure; `--laser` switches on a Pico-driven laser.)

**Make-or-break check:** point the camera at the brightest, whitest footage you
can find with the laser on. If the dot stays cleanly separated, the rest of the
project is downhill. If not, crush exposure further or add the polarizer/IR
discussion from the plan.

## Phase 1 — calibrate

1. With detection working, click **Calibrate** in the app (or run
   `python -m lightgun.calibrate`).
2. A fullscreen grid of targets appears on the game monitor. Aim at the
   highlighted target and fire. With a gun connected, the trigger records the
   shot. Without one, hold steady and it auto-accepts (or press SPACE). `R`
   steps back to redo the previous target, ESC aborts.
3. It computes the homography, reports the mean reprojection error (want < ~3 px),
   and saves it into your config under `[calibration]`, along with the monitor
   and camera resolutions it was measured at.

## Phase 2 — play

Click **Play** in the app, or from the command line:

```powershell
python -m lightgun.guns              # bring-up: find guns, blink lasers, print trigger events
python -m lightgun.app --dry-run     # track + log clicks without touching the cursor
python -m lightgun.app               # drive the Windows cursor; Ctrl+C to quit
```

The runtime moves the cursor to the gun's dot each frame and, on a trigger
press, clicks at the tracked position (held at least `click_hold_ms`). Only one
gun drives the cursor (gun selector in the app, `--gun p2` in the CLI); true
two-player is Phase 4. If a game runs as administrator, run LightGun elevated
too, or Windows drops the input.

**Laser mode** (Laser tab, `[laser] mode`, or `laser_mode` per gun):

- `"always"` (default): the laser stays on, aim is tracked every frame, and a
  pull clicks immediately at the tracked position.
- `"on_fire"`: the gun lights the laser only when the trigger is pulled, and the
  click lands where the dot first appears. No visible dot between shots, at the
  cost of at least one camera frame of delay per shot. Shots are dropped if no
  dot shows up within `fire_window_ms`; raise it if that happens.

Calibration and the detect preview always use `"always"`, since they need a
steady dot.

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
src/lightgun/
  default_config.toml    bundled default config (template for %APPDATA%)
  config.py              load (with defaults merged) / comment-preserving save
  settings.py            description of every option, drives the settings tabs
  capture.py             threaded camera, manual exposure/focus locking
  detect.py              blob detection + annulus hue classification + CLI preview
  mapping.py             homography apply + compute
  calibrate.py           CalibrationSession + OpenCV fullscreen CLI
  engine.py              shared runtime: camera, detection, guns, trigger, injection
  guns.py                serial link to the guns (discovery, events, laser mode)
  inject/windows.py      SendInput absolute move + click (any monitor)
  screen.py              monitor enumeration (physical pixels)
  app.py                 command-line runtime
  gui/                   PySide6 desktop app (main window, settings, calibration)
packaging/               PyInstaller spec, launcher, icon, build_exe.ps1
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
6. (Started) Desktop app polish: camera picker by name, tray mode, installer.
