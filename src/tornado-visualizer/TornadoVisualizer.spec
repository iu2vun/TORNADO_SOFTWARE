# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: onedir (default) oppure onefile con  set APP_ONEFILE=1
import os
from PyInstaller.utils.hooks import collect_submodules

ONEFILE = os.environ.get("APP_ONEFILE", os.environ.get("TS_ONEFILE")) == "1"
datas, binaries, hidden = [("assets", "assets")], [], collect_submodules("tornado_suite") + ["olefile"]

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "notebook", "pytest", "numpy"], noarchive=False)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="TornadoVisualizer", debug=False, strip=False, upx=False, console=False,
              icon="assets/TornadoVisualizer.ico")
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TornadoVisualizer", debug=False, strip=False, upx=False, console=False,
              icon="assets/TornadoVisualizer.ico")
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TornadoVisualizer")
