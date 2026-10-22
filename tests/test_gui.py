"""GUI smoke tests on Qt's offscreen platform (no camera, no real display)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QComboBox, QSpinBox  # noqa: E402

from lightgun import config  # noqa: E402
from lightgun.gui.main_window import MainWindow, render_preview  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _editor(win, path):
    return win.form._editors[path][1]


def test_main_window_builds_every_settings_tab(qapp):
    win = MainWindow(start_engine=False)
    titles = [win.form.tabText(i) for i in range(win.form.count())]
    assert {"Display", "Camera", "Detection", "Laser", "Gun p1", "Gun p2"} <= set(titles)
    assert not win.engine.running
    win._refresh_status()
    assert "not calibrated" in win.status.text()
    win.engine.stop()


def test_save_writes_edits_to_user_config(qapp, isolated_user_config):
    win = MainWindow(start_engine=False)
    thr = _editor(win, ("detection", "threshold"))
    assert isinstance(thr, QSpinBox)
    thr.setValue(199)
    mode = _editor(win, ("guns", "p2", "laser_mode"))
    assert isinstance(mode, QComboBox)
    mode.setCurrentIndex(mode.findData("on_fire"))
    assert win._dirty
    win.save()
    assert not win._dirty
    saved = config.load(isolated_user_config)
    assert saved["detection"]["threshold"] == 199
    assert saved["guns"]["p2"]["laser_mode"] == "on_fire"
    win.engine.stop()


def test_live_detection_edit_applies_without_save(qapp):
    win = MainWindow(start_engine=False)
    _editor(win, ("detection", "threshold")).setValue(150)
    assert win.cfg["detection"]["threshold"] == 150
    win.engine.stop()


def test_bad_hue_range_rejected_on_apply(qapp, monkeypatch):
    win = MainWindow(start_engine=False)
    warned = []
    monkeypatch.setattr("lightgun.gui.main_window.QMessageBox.warning",
                        lambda *a, **k: warned.append(a))
    _editor(win, ("guns", "p1", "hue_ranges")).setText("banana")
    assert win.apply() is False
    assert warned
    win.engine.stop()


def test_render_preview_draws_on_a_copy():
    import numpy as np

    from lightgun.capture import Frame
    from lightgun.detect import Blob
    from lightgun.engine import Snapshot
    from lightgun.mapping import ScreenPoint

    img = np.zeros((72, 128, 3), np.uint8)
    snap = Snapshot(Frame(img, 0.0, 1), [Blob(60, 30, 20, 1.0, hue=5, gun="p1")],
                    ScreenPoint(10, 20, True))
    out = render_preview(snap)
    assert out.any() and not img.any()
