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
- **Phase 6 — packaged Windows app (end goal, stated by the user).** A single
  Windows executable with every config option and calibration built in, so no
  Python install and no hand-editing of TOML. Likely shape: PyInstaller (or
  Nuitka) bundle with a GUI for settings, live detection preview, gun
  status/laser mode, and calibration, plus a tray/run mode. The GUI toolkit is
  still undecided. Constraints to respect now so this stays easy:
  - Config can't live next to the code in a frozen exe. `config.py` currently
    resolves `config/default.toml` from the repo root; move the user's config
    to `%APPDATA%\lightgun\config.toml`, seeded from a bundled default.
  - Keep every module driven by the config dict and callable as a function
    (`run_calibration`, `app.run`, `guns.connect`), with no work at import time.
    The CLIs are thin wrappers that a GUI can replace.
  - `calibrate._write_back` edits TOML line by line; a GUI settings editor
    will want a real TOML writer (e.g. `tomlkit`, which preserves comments).

## Current code state (repo: C:\Users\Brian\source\lightgun)

```
config/default.toml      all tunables (camera/detection/laser/guns/calibration/injection)
src/lightgun/
  capture.py             threaded camera; locks manual exposure/focus, reports
                         whether exposure actually took (C920 quirk); live
                         set_exposure() applied from the grab thread
  detect.py              threshold -> blobs -> subpixel centroid -> annulus hue;
                         pick_blob(); live preview: python -m lightgun.detect [--laser]
  mapping.py             homography compute/apply, resolution rescale, offscreen flag
  calibrate.py           fullscreen 3x3 calibration; trigger = shot when a gun is
                         connected, else auto-accept/SPACE; writes homography to config
  guns.py                serial discovery (CircuitPython VID 0x239A + hello query),
                         reader threads, laser enable + mode (laser_settings());
                         CLI: python -m lightgun.guns [--laser]
  inject/windows.py      SendInput absolute move + left button (ctypes)
  inject/__init__.py     backend factory + DryRunInjector
  app.py                 Phase 2 runtime: python -m lightgun.app [--gun p1] [--dry-run]
  screen.py              DPI-aware primary display size (shared by calibrate/app)
  config.py              TOML loader
firmware/pico/boot.py    enables the USB CDC data port (protocol off the REPL)
firmware/pico/main.py    trigger debounce, laser control + always/on_fire mode,
                         JSON serial (fw 0.3.0)
tests/                   pytest suite + synthetic frame generator (gen_synthetic.py)
.vscode/                 settings.json (venv, pytest, src path) + launch.json configs
```

- **Environment:** `.venv` exists (Python 3.14), package installed editable with
  dev extras. **Tests pass (29/29)** and `ruff check` is clean. Everything runs
  without hardware: detection on synthetic frames, homography, calibration
  write-back, serial protocol against a fake port, SendInput struct
  size/normalization, and trigger→click pairing in both laser modes. `SendInput` was also
  smoke-tested on the real desktop.

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

- **Primary display only.** The calibration window and SendInput's absolute
  coordinates both target the primary monitor. This machine's primary is
  3440×1440 with a second monitor to its left. If the game display (TV /
  projector) isn't primary, either make it primary or add monitor selection
  (EnumDisplayMonitors + `MOUSEEVENTF_VIRTUALDESK`).
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

## Immediate next steps (all need the hardware)

1. Run `python -m lightgun.detect` with the C920 and prove the red dot separates
   cleanly from bright game footage. **This is the make-or-break test.** The
   C920's manual exposure over DirectShow is the likely sticking point. The
   preview warns if exposure didn't lock; switch `backend` between dshow and
   msmf and/or set exposure in the Logitech Camera Settings app. Tune live with
   `[ ]` (threshold) and `- =` (exposure).
2. Flash a Pico with `boot.py` + `main.py`, replug it, and check with
   `python -m lightgun.guns --laser` that trigger events print.
3. Run `python -m lightgun.calibrate` to produce a homography (shoot the targets).
4. Run `python -m lightgun.app --dry-run`, then for real in a game (MAME).
   To try on-fire mode, set `[laser] mode = "on_fire"`; if shots get dropped,
   raise `fire_window_ms` (it must cover the camera's pipeline latency).
5. Then Phase 3: One Euro filter (the `[filter]` params are already in the
   config), temporal sanity filtering, latency harness, and the switch to the
   ELP camera.

## Working style notes

- Files live on a Windows machine under `C:\Users\Brian\source`. VS Code is the
  editor; the repo is not under git yet. Prefer prose over heavy bullet lists in
  chat. Keep decisions above intact.
