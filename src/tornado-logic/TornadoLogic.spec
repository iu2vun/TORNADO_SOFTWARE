# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: cartella (default, avvio rapido) oppure file unico con  set APP_ONEFILE=1  (lo fa build.bat onefile)
# Generato da init_build.py; si può modificare a mano.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ONEFILE = os.environ.get("APP_ONEFILE") == "1"
NAME = "TornadoLogic"
ICON = "assets/TornadoLogic.ico"
ENTRY = "main.py"
DATA_DIRS = []          # cartelle di risorse copiate con l'eseguibile
LOCAL_PACKAGES = ['logic_core', 'logic_gui', 'tornado_ui']    # pacchetti del progetto: include anche gli import dinamici
COLLECT_ALL = ["pyqtgraph", "serial"]    # serial = pyserial (porta del tester ESP32, import in funzione)
EXCLUDES = ['PySide2', 'PyQt5', 'PyQt6', 'tkinter', 'IPython', 'notebook', 'pytest']

datas, binaries, hidden = [(d, d) for d in DATA_DIRS if os.path.isdir(d)], [], []
for pkg in LOCAL_PACKAGES:
    hidden += collect_submodules(pkg)
for pkg in COLLECT_ALL:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

a = Analysis([ENTRY], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden,
             hookspath=[], runtime_hooks=[], excludes=EXCLUDES, noarchive=False)
pyz = PYZ(a.pure)
icon = ICON if os.path.exists(ICON) else None

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, debug=False, strip=False, upx=False,
              console=False, icon=icon, uac_admin=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, debug=False, strip=False, upx=False,
              console=False, icon=icon, uac_admin=False)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
