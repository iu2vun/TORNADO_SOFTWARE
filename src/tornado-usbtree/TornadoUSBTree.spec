# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: onedir (default, avvio rapido) oppure onefile con  set TS_ONEFILE=1   (generato da make_tools.py)
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("APP_ONEFILE", os.environ.get("TS_ONEFILE")) == "1"
datas, binaries, hidden = [("assets", "assets")], [], collect_submodules("tornado_suite")
datas += []
for pkg in ('pyqtgraph',):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += []

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "notebook", "pytest"], noarchive=False)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="TornadoUSBTree", debug=False, strip=False, upx=False, console=False,
              icon="assets/TornadoUSBTree.ico", uac_admin=True)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TornadoUSBTree", debug=False, strip=False, upx=False, console=False,
              icon="assets/TornadoUSBTree.ico", uac_admin=True)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TornadoUSBTree")
