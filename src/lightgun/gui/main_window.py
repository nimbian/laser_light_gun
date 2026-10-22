"""LightGun main window: live preview, status, settings, calibration, play.

The engine runs the camera from launch so the preview doubles as the
detection-tuning view. Play starts pointer injection; Calibrate opens the
fullscreen calibration screen on the game monitor. Settings edits apply to the
running engine with Apply (detection and exposure changes apply instantly)
and persist to the user config with Save.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config, screen, settings
from ..calibrate import apply_result, save_result
from ..engine import Engine, Snapshot
from . import log as gui_log
from .calibration_window import CalibrationWindow, qscreen_for
from .icon import app_icon
from .settings_form import SettingsForm

_GUN_BGR = {"p1": (0, 0, 255), "p2": (0, 255, 0), "?": (0, 255, 255)}


def render_preview(snap: Snapshot) -> np.ndarray:
    """Camera frame with detected dots (and the mapped pointer) drawn on."""
    view = snap.frame.image.copy()
    for b in snap.blobs:
        color = _GUN_BGR.get(b.gun or "?", (255, 255, 255))
        cv2.circle(view, (int(b.x), int(b.y)), 12, color, 2)
        label = f"{b.gun or '-'} a{b.area:.0f}"
        if b.hue is not None:
            label += f" h{b.hue:.0f}"
        cv2.putText(view, label, (int(b.x) + 14, int(b.y)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color, 1)
    if snap.pointer is not None:
        p = snap.pointer
        text = f"screen ({p.x:.0f}, {p.y:.0f})" + ("" if p.on_screen else " off-screen")
        cv2.putText(view, text, (10, view.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 255, 255), 2)
    return view


class MainWindow(QMainWindow):
    def __init__(self, config_path: str | Path | None = None, start_engine: bool = True):
        super().__init__()
        self.config_path = config.resolve_path(config_path)
        self.cfg: dict[str, Any] = config.load(self.config_path)
        self.engine = Engine(self.cfg, self._first_gun())
        self._dirty = False
        self._last_frame = -1
        self._calib: CalibrationWindow | None = None

        self._build_ui()
        self._update_title()

        self._preview_timer = QTimer(self)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self._preview_timer.start(30)
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(500)

        if start_engine:
            QTimer.singleShot(0, self.start_engine)

    def _first_gun(self) -> str:
        return next(iter(self.cfg.get("guns", {"p1": {}})), "p1")

    # -- UI ------------------------------------------------------------------

    def _build_ui(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_play = QAction("▶ Play", self)
        self.act_play.setCheckable(True)
        self.act_play.setToolTip("Drive the cursor from the gun and click on the trigger.")
        self.act_play.toggled.connect(self._toggle_play)
        tb.addAction(self.act_play)

        self.dry_run = QCheckBox("Dry run")
        self.dry_run.setToolTip("Log clicks instead of moving the cursor (applies on Play).")
        tb.addWidget(self.dry_run)

        tb.addWidget(QLabel("  Gun: "))
        self.gun_box = QComboBox()
        for gid in self.cfg.get("guns", {}):
            self.gun_box.addItem(gid)
        self.gun_box.setToolTip("Which gun drives the cursor (Windows has one cursor).")
        self.gun_box.currentTextChanged.connect(self._gun_changed)
        tb.addWidget(self.gun_box)
        tb.addSeparator()

        for text, tip, slot in (
            ("Calibrate", "Shoot a grid of targets on the game monitor.", self.calibrate),
            ("Rescan guns", "Find guns again after plugging one in.", self._rescan_guns),
            ("Identify guns", "Blink each connected gun's laser.", self._identify_guns),
            ("Restart camera", "Reopen the camera and guns.", self.restart_engine),
            ("Open config folder", "Show config.toml and lightgun.log.",
             self._open_config_folder),
        ):
            act = QAction(text, self)
            act.setToolTip(tip)
            act.triggered.connect(slot)
            tb.addAction(act)

        # Left: preview + status.
        left = QWidget()
        lv = QVBoxLayout(left)
        self.preview = QLabel("Camera not started")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.preview.setStyleSheet("background:#111; color:#aaa;")
        lv.addWidget(self.preview, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setWordWrap(True)
        lv.addWidget(self.status)

        # Right: settings + buttons.
        right = QWidget()
        rv = QVBoxLayout(right)
        self.form = SettingsForm(self.cfg, screen.monitors())
        self.form.edited.connect(self._mark_dirty)
        self.form.live_changed.connect(self._live_change)
        rv.addWidget(self.form, 1)
        buttons = QHBoxLayout()
        for text, tip, slot in (
            ("Revert", "Discard unsaved edits.", self._revert),
            ("Apply", "Use these settings now (restarts the camera if needed).", self.apply),
            ("Save", "Apply and write to config.toml.", self.save),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        rv.addLayout(buttons)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.setCentralWidget(split)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        dock = QDockWidget("Log", self)
        dock.setObjectName("log")
        dock.setWidget(self.log_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.resize(1400, 860)

    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _update_title(self) -> None:
        self.setWindowTitle(f"LightGun {__version__}{' *' if self._dirty else ''}"
                            f"  —  {self.config_path}")

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self._update_title()

    # -- engine control ------------------------------------------------------

    def start_engine(self) -> None:
        try:
            self.engine.start()
        except Exception as e:
            print(f"[gui] camera failed to start: {e}")
            self.preview.setText(f"Camera failed to start:\n{e}\n\n"
                                 "Check the Camera tab, then Restart camera.")
            return
        a = self.engine.cam.stats.applied if self.engine.cam else {}
        print(f"[gui] camera started: {a}")
        if not self.engine.links:
            print("[gui] no guns connected (calibration will auto-accept a steady dot)")

    def restart_engine(self) -> None:
        was_playing = self.engine.injecting
        self.act_play.setChecked(False)
        self.engine.stop()
        self.start_engine()
        if was_playing and self.engine.running:
            self.act_play.setChecked(True)

    def _toggle_play(self, on: bool) -> None:
        if not on:
            if self.engine.injecting:
                self.engine.stop_injection()
                print("[gui] stopped")
            return
        if not self.engine.running:
            self.start_engine()
        try:
            self.engine.start_injection(self.dry_run.isChecked())
        except ValueError as e:
            self.act_play.setChecked(False)
            QMessageBox.warning(self, "Can't start", f"{e}\n\nRun Calibrate first.")
            return
        m = self.engine.monitor
        print(f"[gui] playing: {self.engine.gun_id} on {m.label() if m else '?'}, "
              f"laser mode {self.engine.mode}{' (dry run)' if self.dry_run.isChecked() else ''}")

    def _gun_changed(self, gid: str) -> None:
        if not gid:
            return
        playing = self.engine.injecting
        if playing:
            self.engine.stop_injection()
        self.engine.gun_id = gid
        if playing:
            self._toggle_play(True)

    def _rescan_guns(self) -> None:
        if not self.engine.running:
            self.start_engine()
            return
        self.engine.reconnect_guns()
        print(f"[gui] guns: {', '.join(self.engine.links) or 'none found'}")

    def _identify_guns(self) -> None:
        for link in self.engine.links.values():
            link.identify()
        if not self.engine.links:
            print("[gui] no guns connected")

    def _open_config_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config_path.parent)))

    # -- settings --------------------------------------------------------------

    def _live_change(self, field: settings.Field, value: Any) -> None:
        settings.set_value(self.cfg, field, value)
        if field.path == ("camera", "exposure"):
            self.engine.set_exposure(float(value))

    def apply(self) -> bool:
        changed, errors = self.form.read_into(self.cfg)
        if errors:
            QMessageBox.warning(self, "Invalid settings", "\n".join(errors))
            return False
        if any(f.restart for f in changed) and self.engine.running:
            print("[gui] restarting camera/guns for new settings")
            self.restart_engine()
        elif self.engine.injecting and any(not f.live for f in changed):
            self.engine.stop_injection()
            self._toggle_play(True)
        if changed:
            print(f"[gui] applied: {', '.join('.'.join(f.path) for f in changed)}")
        return True

    def save(self) -> None:
        if not self.apply():
            return
        config.save_values(self.config_path, settings.updates_for(self.cfg, self.form.fields))
        self._dirty = False
        self._update_title()
        print(f"[gui] saved {self.config_path}")

    def _revert(self) -> None:
        self.form.load(self.cfg)
        self._dirty = False
        self._update_title()

    # -- calibration -----------------------------------------------------------

    def calibrate(self) -> None:
        if self._calib is not None:
            return
        if not self.engine.running:
            self.start_engine()
            if not self.engine.running:
                return
        if self.engine.injecting:
            self.act_play.setChecked(False)
        monitor = screen.selected_monitor(self.cfg)
        print(f"[gui] calibrating on {monitor.label()}")
        self._calib = CalibrationWindow(self.engine, self.cfg, monitor)
        self._calib.finished.connect(self._calibration_done)
        self._calib.setScreen(qscreen_for(monitor))
        self._calib.showFullScreen()
        self._calib.activateWindow()

    def _calibration_done(self, result: dict[str, Any] | None) -> None:
        self._calib = None
        if result is None:
            return
        err = result["reprojection_error"]
        apply_result(self.cfg, result)
        save_result(self.config_path, result)
        msg = f"Calibration saved. Mean reprojection error: {err:.2f} px"
        if err > 8.0:
            msg += "\n\nThat's high; aim was probably inconsistent. Consider redoing it."
        print(f"[gui] {msg.splitlines()[0]}")
        QMessageBox.information(self, "Calibration", msg)

    # -- periodic refresh ------------------------------------------------------

    def _refresh_preview(self) -> None:
        snap = self.engine.snapshot()
        if snap is None or snap.frame.index == self._last_frame:
            return
        self._last_frame = snap.frame.index
        view = render_preview(snap)
        h, w = view.shape[:2]
        img = QImage(view.data, w, h, view.strides[0], QImage.Format.Format_BGR888)
        pix = QPixmap.fromImage(img).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(pix)

    def _refresh_status(self) -> None:
        e = self.engine
        parts = []
        if e.error:
            parts.append(f"<b style='color:#d33'>Engine stopped: {e.error}</b>")
        if e.cam is not None:
            a = e.cam.stats.applied
            lock = ("<span style='color:#2a2'>exposure locked</span>" if e.cam.exposure_locked()
                    else "<b style='color:#d33'>exposure NOT locked</b>")
            parts.append(f"Camera {a.get('width')}x{a.get('height')} @ "
                         f"{e.cam.stats.measured_fps:.0f} fps, exposure {a.get('exposure')} "
                         f"({lock})")
        elif not e.error:
            parts.append("Camera stopped")
        now = time.perf_counter()
        if e.links:
            guns_txt = ", ".join(
                f"{gid} on {link.port} (fw {link.fw}, seen {now - link.last_seen:.1f}s ago)"
                for gid, link in e.links.items())
        else:
            guns_txt = "none connected"
        parts.append(f"Guns: {guns_txt}")
        cal = self.cfg["calibration"]
        cal_txt = (f"calibrated for {cal.get('captured_width')}x{cal.get('captured_height')}"
                   if cal.get("homography") else "<b>not calibrated</b>")
        state = (f"<b>PLAYING</b> ({e.gun_id}, laser {e.mode})" if e.injecting else "idle")
        parts.append(f"State: {state}; {cal_txt}")
        for w in e.calibration_warnings():
            parts.append(f"<span style='color:#c80'>⚠ {w}</span>")
        self.status.setText("<br>".join(parts))
        if e.error and self.act_play.isChecked():
            self.act_play.setChecked(False)

    def closeEvent(self, event) -> None:
        if self._dirty:
            r = QMessageBox.question(
                self, "Unsaved settings", "Save settings before quitting?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if r == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if r == QMessageBox.StandardButton.Save:
                self.save()
        self._preview_timer.stop()
        self._status_timer.stop()
        self.engine.stop()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LightGun desktop app.")
    ap.add_argument("--config", default=None, help="Config TOML (default: the user config).")
    ap.add_argument("--smoke", action="store_true",
                    help="Build the window without the camera, then exit (packaging check).")
    args = ap.parse_args(argv)

    if args.config:
        os.environ["LIGHTGUN_CONFIG"] = str(Path(args.config).resolve())
    log_path = config.user_config_path().parent / "lightgun.log"

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("LightGun")
    app.setWindowIcon(app_icon())
    stream = gui_log.install(log_path)
    print(f"[gui] LightGun {__version__} starting; log at {log_path}")

    win = MainWindow(start_engine=not args.smoke)
    stream.line.connect(win.append_log)
    win.show()
    if args.smoke:
        def finish() -> None:
            print(f"[gui] smoke ok: {len(win.form.fields)} settings, "
                  f"{len(screen.monitors())} monitor(s), config {win.config_path}")
            win.close()
            app.quit()
        QTimer.singleShot(300, finish)
    return app.exec()
