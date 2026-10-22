# Light Gun Project — Session Handoff

Paste this to a new Claude session to bring it fully up to speed. It captures the
architecture, locked decisions, hardware, current code state, and next steps.

## What this project is

A DIY light gun system with an **inverted architecture**: instead of a camera
inside each gun (Wiimote/AimTrak style), a single **stationary camera watches
the TV/screen**. Each gun emits a **visible laser dot**; software detects the dot
in the camera feed, maps it to screen pixels via a calibrated homography, and
injects pointer input + trigger clicks so games (MAME etc.) see a light gun.
Target OS: **Windows**. Language: **Python 3.11+**.

## Decisions already made — do NOT relitigate without flagging

- **Visible color lasers, not IR.** P1 = red 650nm, P2 = green 520nm. Contrast
  comes from crushing camera exposure/gain (the laser dot is orders of magnitude
  brighter than screen content), optionally helped by a linear polarizer on the
  camera for LCD TVs. IR was rejected on **safety grounds** (no blink reflex);
  an IR-pass filter found inside the old guns is set aside, not used.
- **Continuous tracking, discrete trigger.** Laser stays on while the gun is
  active so aim is tracked every frame; the trigger is just a button event over
  USB serial, paired with the latest tracked position. Never infer a click from
  the dot appearing/disappearing. **Opt-in exception (added 2026-10-21 at the
  user's request):** `[laser] mode = "on_fire"` (or per-gun `laser_mode`) makes
  the gun light the laser only on a trigger pull. The click is still caused by
  the serial trigger event, landing at the first dot seen after the pull
  (≥1 frame extra latency; the shot is dropped if no dot appears within
  `fire_window_ms`). `"always"` stays the default, and calibration and the
  detect preview always force it.
- **Color (hue) disambiguation for two guns**, read from the *annulus* around the
  blob (the saturated core clips to white). No time-division multiplexing.
- **User-shot 3×3 calibration**: the player shoots a grid of on-screen targets;
  homography is computed from those correspondences, which also absorbs each
  gun's mechanical sight offset.
- **Green laser must be direct-diode 520nm, never 532nm DPSS** (DPSS can leak
  invisible IR). Red 650nm Class 2 <1mW.

## Hardware

- **Guns:** reusing existing **AimTrak** shells (gut the IR sensor board, keep
  shell/trigger/buttons/USB, and recoil solenoid if present). Brain = **Raspberry
  Pi Pico** per gun, CircuitPython.
- **Lasers:** Quarton Class-2 modules from Digi-Key — red VLM-650-01 LPT, green
  VLM-520-03/04 LPT. Green needs a small **MT3608 boost converter** (runs ~7–10V);
  red runs off Pico VBUS. Cheap KY-008 red modules for prototyping.
- **Camera:** final = **ELP OV4689** high-fps UVC (120fps@720p), ideally the
  2.8–12mm varifocal version. **Currently prototyping with a Logitech C920** (30fps)
  — fine for Phases 0–2; it won't give real latency numbers.
- **Display:** considering a **projector** (diffuse screen = no specular hotspot,
  easier detection; prefer 3LCD over single-chip DLP to avoid color-wheel strobe).
- **Driver parts:** logic-level MOSFET per laser, current-limiting resistors,
  breadboard/jumpers for Phases 0–1.

## Phase plan

- **Phase 0 — detection** ✅ built. Threaded capture + blob detection + hue
  classification + live preview + synthetic-frame test suite.
- **Phase 1 — calibration** ✅ built. Fullscreen 3×3 app computes & saves homography.
- **Phase 2 — gun integration + Windows click injection** ✅ built in software,
  **not yet tested against real gun hardware**. Serial discovery/listener,
  `SendInput` absolute move + click paired with the latest tracked position,
  trigger wired into calibration, firmware hardened.
- **Phase 3 — robustness/latency** (next, after hardware validation). One Euro
  filter, temporal sanity filtering, diagnostics overlay, latency harness,
  switch to the fast camera.
- **Phase 4 — two players.** Windows has one cursor, so move the HID pointer into
  each Pico (each presents as a USB HID absolute pointing device); MAME rawinput
  binds P1/P2 separately.
- **Phase 5 — polish.** Off-screen reload, per-game offsets, recoil solenoid.
- **Phase 6 — packaged Windows app (end goal, stated by the user)** — 🚧 first
  version built on branch `windows-app`. A single Windows program with every
  config option and calibration built in: no Python install, no hand-editing
  TOML. Choices made (2026-10-21): **PySide6 (Qt)** GUI, **PyInstaller**
  one-folder build (`-OneFile` optional), user config in
  `%APPDATA%\lightgun\config.toml` seeded from a bundled default, saves via
  **tomlkit** (comments preserved). Done: live preview with dots, status
  (fps, exposure lock, guns, calibration), every setting in tabs (schema in
  `settings.py`; a test fails if a config key has no field), Apply/Save/Revert,
  game-monitor selection, fullscreen Qt calibration on that monitor, Play/Stop
  with dry run and gun selector, rescan/identify guns, log pane + log file,
  crosshair icon. Still to do: camera picker by device name, tray/run mode,
  doing gun discovery off the UI thread (it currently blocks the UI ~0.6 s per
  candidate port), an installer, and code signing.

## Current code state

Repo: `C:\Users\Brian\source\lightgun`, on GitHub at
https://github.com/nimbian/laser_light_gun. `main` holds Phases 0–2 plus
laser modes; the desktop app is on branch **`windows-app`** (not merged yet).

```
src/lightgun/
  default_config.toml    bundled default config (all tunables); template for
                         %APPDATA%\lightgun\config.toml. LIGHTGUN_CONFIG overrides.
  config.py              load() merges defaults under the user file; save_values()
                         writes via tomlkit (comments kept; None removes a key)
  settings.py            Qt-free description of every option (label/type/range/help,
                         live vs restart); drives the GUI tabs
  capture.py             threaded camera; locks manual exposure/focus, reports
                         whether exposure actually took (C920 quirk); live
                         set_exposure() applied from the grab thread
  detect.py              threshold -> blobs -> subpixel centroid -> annulus hue;
                         pick_blob(); CLI preview: python -m lightgun.detect [--laser]
  mapping.py             homography compute/apply, resolution rescale, offscreen flag
  calibrate.py           CalibrationSession (UI-free state machine) + OpenCV CLI;
                         save_result() writes [calibration]
  engine.py              Engine: camera + detection + guns + injection on a worker
                         thread (used by CLI and GUI); TriggerHandler (both laser modes)
  guns.py                serial discovery (CircuitPython VID 0x239A + hello query),
                         reader threads, laser enable + mode (laser_settings());
                         CLI: python -m lightgun.guns [--laser]
  inject/windows.py      SendInput absolute move + left button on any monitor
                         (MOUSEEVENTF_VIRTUALDESK)
  inject/__init__.py     backend factory + DryRunInjector
  screen.py              monitor enumeration in physical pixels (per-monitor DPI
                         aware), [display] monitor resolution
  app.py                 CLI runtime: python -m lightgun.app [--gun p1] [--dry-run]
  gui/                   PySide6 app: main_window.py, settings_form.py,
                         calibration_window.py, log.py, icon.py
packaging/               lightgun.spec, launcher.py, make_icon.py, build_exe.ps1
firmware/pico/boot.py    enables the USB CDC data port (protocol off the REPL)
firmware/pico/main.py    trigger debounce, laser control + always/on_fire mode,
                         JSON serial (fw 0.3.0)
tests/                   pytest suite + synthetic frame generator (gen_synthetic.py);
                         conftest isolates the user config and runs Qt offscreen
.vscode/                 settings.json (venv, pytest, src path) + launch.json configs
```

- **Environment:** `.venv` (Python 3.14) with `pip install -e ".[dev,build]"`
  (PySide6-Essentials 6.12, tomlkit, PyInstaller 6.22). **Tests pass (45/45)**
  and `ruff check` is clean, all without hardware.
- **Executable:** `packaging\build_exe.ps1` builds `dist\LightGun\LightGun.exe`
  (~240 MB folder) and smoke-tests it (`--smoke` builds the window without the
  camera, then exits). This passes on this machine. `SendInput` was also tested
  on the real desktop.

### This machine (found 2026-10-21)

- **Three monitors:** `\\.\DISPLAY1` Dell AW3423DW 3440×1440 at (0,0),
  primary; `\\.\DISPLAY3` Acer X233HZ 1080×1920 portrait at (-1080,-488);
  `\\.\DISPLAY2` Acer XB270HU 2560×1440 at (0,-1440). All at 100% scaling.
- Qt names screens by **model**, not device name, so `qscreen_for()` matches by
  geometry (native origin + size×dpr). Verified for all three monitors.
- **Camera index 0 is the NVIDIA Broadcast virtual camera** (spams `[VCAMDS]`
  logs), so the C920 is probably index 1 or 2. A camera picker that shows
  device names (DirectShow enumeration, e.g. `pygrabber`) is the next app
  improvement.

### Fixed in the 2026-10-21 review

- Calibration wrote the *camera* resolution into `captured_width/height`, which
  `Mapper` treats as the *screen* resolution, so every point would have been
  scaled by screen/camera (e.g. 1.5×). It now writes the screen size; the camera
  size goes into new `camera_width/height` keys and the app warns if the camera
  mode has changed since calibration.
- Calibration stability counted UI-loop iterations rather than new camera
  frames, so `stable_frames = 8` could pass after about 2 real frames. It now
  counts new frames only.
- The exposure fallback tried auto-exposure values (3, 0.75) and could leave the
  camera in auto. It now tries manual values only and re-applies the configured
  one.
- Firmware `readline()` could block the trigger loop on a partial line, and
  `identify` forced the laser off instead of restoring it. Both fixed.
- `pyproject` referenced a missing `lightgun.app`. In calibration, `r` now steps
  back to redo the previous target.

### Known limitations / open questions

- **One homography shared by both guns.** Fine for single player. For Phase 4,
  calibrate per gun (`--gun`) and store a matrix per gun so each gun's sight
  offset is absorbed, as the calibration decision requires.
- SendInput is blocked by UIPI if the game runs elevated; run the app elevated too.

## Serial protocol (PC ↔ gun), newline-delimited JSON @115200

- Gun→PC: `{"e":"hello","id":"p1","fw":"0.3.0"}`, `{"e":"trigger","s":1|0}`, `{"e":"hb"}`
- PC→Gun: `{"c":"hello"}` (asks the gun to re-announce; used for discovery
  because the boot-time hello is usually missed), `{"c":"laser","s":1|0}`
  (enable), `{"c":"mode","m":"always"|"on_fire","pulse_ms":N}`, `{"c":"identify"}`
- In on_fire mode the gun lights the laser itself on the press edge (for at
  least `pulse_ms` and while held), so there's no USB round trip before the dot.
- Firmware safety: the laser only runs while the host holds the port open
  (DTR); disconnecting also resets the mode to `always`.
- (Phase 4 adds PC→Gun `{"c":"pos","x":..,"y":..}` for gun-side HID reporting.)

## Immediate next steps

Need the camera and a laser (a plain red laser pointer is enough for 1, 3, 4):

1. Open the app (`lightgun-gui` or `dist\LightGun\LightGun.exe`), set the
   Camera tab's index to the C920 (not 0, see above), and prove the red dot
   separates cleanly from bright game footage. **This is the make-or-break
   test.** The C920's manual exposure over DirectShow is the likely sticking
   point. The status line says whether exposure locked; switch `backend`
   between dshow and msmf and/or set exposure in the Logitech Camera Settings
   app. Tune threshold and exposure live in the Detection and Camera tabs.
2. Flash a Pico with `boot.py` + `main.py`, replug it, and check with
   `python -m lightgun.guns --laser` (or Rescan guns in the app) that trigger
   events print.
3. Pick the game monitor (Display tab), then Calibrate.
4. Play with Dry run, then for real in a game (MAME).
   To try on-fire mode, set `[laser] mode = "on_fire"`; if shots get dropped,
   raise `fire_window_ms` (it must cover the camera's pipeline latency).
5. Then Phase 3: One Euro filter (the `[filter]` params are already in the
   config), temporal sanity filtering, latency harness, and the switch to the
   ELP camera.

Software that doesn't need hardware: camera picker by name, a stand-in
trigger (mouse button/hotkey) for laser-pointer play, a record/replay tool
for tuning detection on recorded clips, the One Euro filter, a screen-flash
camera latency test, a tray mode, moving gun discovery off the UI thread, and
merging `windows-app` into `main`.

## Working style notes

- Files live on a Windows machine under `C:\Users\Brian\source`. VS Code is the
  editor. Git remote: https://github.com/nimbian/laser_light_gun (`main`, plus
  feature branches). Prefer prose over heavy bullet lists in chat. Keep
  decisions above intact.
