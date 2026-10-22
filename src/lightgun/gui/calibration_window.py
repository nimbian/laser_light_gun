"""Fullscreen calibration screen on the game monitor, driven by CalibrationSession.

Targets are positioned in the monitor's physical pixels (what the homography
maps to), so painting scales by 1/devicePixelRatio to stay exact under Windows
display scaling.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QScreen
from PySide6.QtWidgets import QWidget

from ..calibrate import CalibrationSession
from ..detect import pick_blob
from ..engine import Engine
from ..screen import Monitor


def qscreen_for(monitor: Monitor) -> QScreen:
    """The Qt screen showing this monitor.

    Qt names screens by model ("Dell AW3423DW"), not device name, so match on
    geometry: Qt keeps each screen's origin in native pixels and scales only
    its size by the device pixel ratio.
    """
    def score(s: QScreen) -> int:
        g, r = s.geometry(), s.devicePixelRatio()
        origin = (g.x(), g.y()) == (monitor.x, monitor.y)
        size = (round(g.width() * r), round(g.height() * r)) == monitor.size
        return 2 * origin + size

    best = max(QGuiApplication.screens(), key=score)
    return best if score(best) > 0 else QGuiApplication.primaryScreen()


class CalibrationWindow(QWidget):
    finished = Signal(object)   # result dict, or None if aborted/failed
    _fired = Signal()

    def __init__(
        self,
        engine: Engine,
        cfg: dict[str, Any],
        monitor: Monitor,
        gun_filter: str | None = None,
    ):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("LightGun calibration")
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.engine = engine
        self.gun_filter = gun_filter
        self.session = CalibrationSession.from_config(
            cfg, monitor.size, auto_accept=not engine.links
        )
        self._last_index = -1
        self._done = False

        # Trigger events arrive on serial threads; the signal hops to the UI thread.
        self._fired.connect(self._fire)
        engine.add_listener(self._on_gun_event)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(5)

        screen = qscreen_for(monitor)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

    def _on_gun_event(self, gid: str, msg: dict[str, Any], _t: float) -> None:
        if msg.get("e") == "trigger" and msg.get("s") == 1:
            if self.gun_filter is None or gid == self.gun_filter:
                self._fired.emit()

    def _poll(self) -> None:
        snap = self.engine.snapshot()
        if snap is not None and snap.frame.index != self._last_index:
            self._last_index = snap.frame.index
            self.session.feed(pick_blob(snap.blobs, self.gun_filter))
            self.update()
        if self.engine.error:
            print(f"[calibrate] engine error: {self.engine.error}")
            self._finish(None)
        elif self.session.done:
            self._finish_with_result()

    def _fire(self) -> None:
        self.session.fire()
        self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            print("[calibrate] aborted")
            self._finish(None)
        elif key == Qt.Key.Key_Space:
            self._fire()
        elif key == Qt.Key.Key_R:
            self.session.redo()
            self.update()

    def _finish_with_result(self) -> None:
        try:
            result = self.session.result(self.engine.camera_size())
        except (ValueError, RuntimeError) as e:
            print(f"[calibrate] failed: {e}")
            result = None
        self._finish(result)

    def _finish(self, result: dict[str, Any] | None) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.engine.remove_listener(self._on_gun_event)
        self.close()
        self.finished.emit(result)

    def closeEvent(self, event) -> None:
        if not self._done:  # closed by the window manager (Alt+F4)
            self._done = True
            self._timer.stop()
            self.engine.remove_listener(self._on_gun_event)
            self.finished.emit(None)
        super().closeEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        dpr = self.devicePixelRatioF()
        p.save()
        p.scale(1 / dpr, 1 / dpr)  # draw in physical pixels
        for j, (x, y) in enumerate(self.session.targets):
            live = j == self.session.index
            color = QColor(255, 40, 40) if live else QColor(80, 80, 80)
            p.setPen(QPen(color, 2 if live else 1))
            c = QPointF(x, y)
            p.drawEllipse(c, 24, 24)
            p.drawEllipse(c, 3, 3)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawLine(QPointF(x + dx * 10, y + dy * 10), QPointF(x + dx * 34, y + dy * 34))
        p.restore()

        p.setPen(QColor(200, 200, 200))
        p.setFont(QFont("Segoe UI", 16))
        p.drawText(40, 50, self.session.status())
        p.setPen(QColor(120, 120, 120))
        p.setFont(QFont("Segoe UI", 11))
        hint = "trigger/SPACE = accept    R = redo previous    ESC = abort"
        if self.session.auto_accept:
            hint = "hold the dot steady to accept    " + hint
        p.drawText(40, self.height() - 40, hint)
        p.end()
