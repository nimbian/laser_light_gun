# PyInstaller spec for LightGun.exe. Build with packaging/build_exe.ps1.
#
# One-folder by default (dist/LightGun/LightGun.exe + its DLLs): starts fast and
# trips antivirus heuristics less than a self-extracting one-file exe. Set
# LIGHTGUN_ONEFILE=1 for a single LightGun.exe instead.

import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
ONEFILE = os.environ.get("LIGHTGUN_ONEFILE") == "1"
ICON = Path(SPECPATH) / "build" / "lightgun.ico"

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(ROOT / "src" / "lightgun" / "default_config.toml"), "lightgun")],
    hiddenimports=["serial.tools.list_ports"],
    excludes=["tkinter", "matplotlib", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe_args = dict(
    name="LightGun",
    console=False,  # windowed: output goes to the log pane and lightgun.log
    icon=str(ICON) if ICON.exists() else None,
    upx=False,
)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **exe_args)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_args)
    coll = COLLECT(exe, a.binaries, a.datas, name="LightGun", upx=False)
