"""Write the app's crosshair icon to an .ico for the exe (run by build_exe.ps1)."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from lightgun.gui.icon import crosshair  # noqa: E402

app = QGuiApplication([])
out = sys.argv[1] if len(sys.argv) > 1 else "lightgun.ico"
if not crosshair(256).save(out, "ICO"):
    sys.exit(f"could not write {out}")
print(f"wrote {out}")
