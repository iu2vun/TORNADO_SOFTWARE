# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: onedir (default, avvio rapido) oppure onefile con  set TS_ONEFILE=1   (generato da make_tools.py)
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("APP_ONEFILE", os.environ.get("TS_ONEFILE")) == "1"
datas, binaries, hidden = [("assets", "assets")], [], collect_submodules("tornado_suite")
datas += [('tornado_suite/apps/tornado_term/icon.ico', 'tornado_suite/apps/tornado_term'), ('tornado_suite/apps/tornado_term/requirements.txt', 'tornado_suite/apps/tornado_term')]
for pkg in ('pyte',):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += ['PIL._tkinter_finder']

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden, hookspath=[], runtime_hooks=[],
             excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "notebook", "pytest"], noarchive=False)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="TornadoESPIDE", debug=False, strip=False, upx=False, console=False,
              icon="assets/TornadoESPIDE.ico", uac_admin=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TornadoESPIDE", debug=False, strip=False, upx=False, console=False,
              icon="assets/TornadoESPIDE.ico", uac_admin=False)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TornadoESPIDE")
