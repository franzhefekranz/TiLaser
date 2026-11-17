# PyInstaller-Spezifikation für TiLaser (Windows und Linux)
# Aufruf aus dem Repository-Wurzelordner:  pyinstaller --noconfirm packaging/tilaser.spec
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "packaging", "icon", "tilaser.ico" if sys.platform.startswith("win") else "tilaser.png")

hidden = (
    collect_submodules("fontTools.ttLib.tables")      # Tabellenmodule werden dynamisch geladen
    + collect_submodules("serial.tools")
    + ["fontTools.pens.basePen", "PyQt6.QtMultimedia", "PyQt6.QtSvg", "pyclipper"]   # QtSvg: SVG-Symbole
)

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "tilaser", "data"), os.path.join("tilaser", "data"))],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest",
              "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
              "PyQt6.Qt3DCore", "PyQt6.QtBluetooth"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TiLaser" if sys.platform.startswith("win") else "tilaser",
    console=False,
    icon=ICON,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="TiLaser", upx=False)
