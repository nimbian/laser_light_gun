"""The app icon: a red crosshair, drawn in code so no image asset is needed.

packaging/make_icon.py writes the same drawing to lightgun.ico for the exe.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def crosshair(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0
    c = QPointF(size / 2, size / 2)
    p.setPen(QPen(QColor(230, 30, 30), 5 * s))
    p.drawEllipse(c, 22 * s, 22 * s)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        p.drawLine(c + QPointF(dx * 10 * s, dy * 10 * s), c + QPointF(dx * 30 * s, dy * 30 * s))
    p.setBrush(QColor(230, 30, 30))
    p.drawEllipse(c, 4 * s, 4 * s)
    p.end()
    return pix


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(crosshair(size))
    return icon
