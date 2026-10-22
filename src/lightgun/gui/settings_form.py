"""Settings tabs generated from lightgun.settings field descriptions."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QWidget,
)
from serial.tools import list_ports

from .. import settings
from ..screen import Monitor
from ..settings import Field
from .calibration_window import qscreen_for


def _serial_ports() -> list[str]:
    try:
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


class SettingsForm(QTabWidget):
    """One tab per settings page. Edits stay in the widgets until read_into()."""

    edited = Signal()
    live_changed = Signal(object, object)  # Field, value: for fields with live=True

    def __init__(self, cfg: dict[str, Any], monitors: list[Monitor], parent=None):
        super().__init__(parent)
        self._monitors = monitors
        self._editors: dict[tuple[str, ...], tuple[Field, QWidget]] = {}
        self._loading = False
        self.fields: list[Field] = []
        for title, fields in settings.pages(cfg):
            self.addTab(self._build_page(fields), title)
        self.load(cfg)

    # -- building ----------------------------------------------------------

    def _build_page(self, fields: list[Field]) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        for f in fields:
            editor = self._make_editor(f)
            label = QLabel(f.label + (" *" if f.restart else ""))
            tip = f.help + ("\n\n* Takes effect after Apply restarts the camera/guns."
                            if f.restart else "")
            label.setToolTip(tip.strip())
            editor.setToolTip(tip.strip())
            form.addRow(label, editor)
            self._editors[f.path] = (f, editor)
            self.fields.append(f)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        return scroll

    def _make_editor(self, f: Field) -> QWidget:
        if f.kind == "int":
            w = QSpinBox()
            w.setRange(int(f.minimum), int(f.maximum))
            w.setSingleStep(int(f.step) or 1)
            w.valueChanged.connect(lambda v, f=f: self._changed(f, v))
            return w
        if f.kind == "float":
            w = QDoubleSpinBox()
            w.setDecimals(f.decimals)
            w.setRange(f.minimum, f.maximum)
            w.setSingleStep(f.step)
            w.valueChanged.connect(lambda v, f=f: self._changed(f, v))
            return w
        if f.kind in ("choice", "monitor", "port"):
            w = QComboBox()
            if f.kind == "choice":
                for c in f.choices:
                    w.addItem(c or "(default)", c)
            elif f.kind == "monitor":
                w.addItem("Primary display", "primary")
                for m in self._monitors:
                    w.addItem(f"{qscreen_for(m).model() or m.name} — {m.label()}", m.name)
            else:
                w.setEditable(True)
                w.addItem("auto", "auto")
                for port in _serial_ports():
                    w.addItem(port, port)
            w.currentIndexChanged.connect(lambda _i, f=f: self._changed(f, None))
            w.editTextChanged.connect(lambda _t, f=f: self._changed(f, None))
            return w
        if f.kind == "pair":
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            for _ in range(2):
                s = QDoubleSpinBox()
                s.setRange(-5000, 5000)
                s.setDecimals(1)
                s.valueChanged.connect(lambda _v, f=f: self._changed(f, None))
                row.addWidget(s)
            return box
        w = QLineEdit()  # text, ranges
        w.textEdited.connect(lambda _t, f=f: self._changed(f, None))
        return w

    # -- values ------------------------------------------------------------

    def load(self, cfg: dict[str, Any]) -> None:
        """Show the values from cfg in every editor."""
        self._loading = True
        try:
            for f, w in self._editors.values():
                self._set_widget(f, w, settings.get_value(cfg, f))
        finally:
            self._loading = False

    def _set_widget(self, f: Field, w: QWidget, value: Any) -> None:
        if f.kind in ("int", "float"):
            w.setValue(value if value is not None else 0)  # type: ignore[attr-defined]
        elif f.kind in ("choice", "monitor", "port"):
            assert isinstance(w, QComboBox)
            i = w.findData(value)
            if i < 0:
                w.addItem(str(value), value)
                i = w.count() - 1
            w.setCurrentIndex(i)
        elif f.kind == "pair":
            spins = w.findChildren(QDoubleSpinBox)
            vals = list(value or (0, 0))
            for s, v in zip(spins, vals, strict=False):
                s.setValue(float(v))
        elif f.kind == "ranges":
            w.setText(settings.format_ranges(value or []))  # type: ignore[attr-defined]
        else:
            w.setText("" if value is None else str(value))  # type: ignore[attr-defined]

    def _read_widget(self, f: Field, w: QWidget) -> Any:
        if f.kind == "int":
            return int(w.value())  # type: ignore[attr-defined]
        if f.kind == "float":
            return float(w.value())  # type: ignore[attr-defined]
        if f.kind in ("choice", "monitor"):
            return w.currentData()  # type: ignore[attr-defined]
        if f.kind == "port":
            assert isinstance(w, QComboBox)
            text = w.currentText().strip()
            return text or "auto"
        if f.kind == "pair":
            return [s.value() for s in w.findChildren(QDoubleSpinBox)]
        if f.kind == "ranges":
            return settings.parse_ranges(w.text())  # type: ignore[attr-defined]
        return w.text()  # type: ignore[attr-defined]

    def read_into(self, cfg: dict[str, Any]) -> tuple[list[Field], list[str]]:
        """Copy editor values into cfg. Returns (fields that changed, error messages)."""
        changed: list[Field] = []
        errors: list[str] = []
        for f, w in self._editors.values():
            try:
                value = self._read_widget(f, w)
            except ValueError as e:
                errors.append(f"{f.label}: {e}")
                continue
            if value != settings.get_value(cfg, f):
                settings.set_value(cfg, f, value)
                changed.append(f)
        return changed, errors

    def _changed(self, f: Field, value: Any) -> None:
        if self._loading:
            return
        self.edited.emit()
        if f.live:
            if value is None:
                value = self._read_widget(f, self._editors[f.path][1])
            self.live_changed.emit(f, value)
