"""Elevazione a amministratore (Windows): rilancia il processo con 'runas' (prompt UAC)."""
from __future__ import annotations

import ctypes
import os
import sys


def is_admin() -> bool:
    if sys.platform != "win32":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def relaunch_as_admin() -> bool:
    """True se è stato avviato il processo elevato (il chiamante deve uscire)."""
    if sys.platform != "win32" or is_admin():
        return False
    args = [a for a in sys.argv[1:] if a != "--elevated"]
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, args
    else:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw
        params = [os.path.abspath(sys.argv[0])] + args
    cmd = " ".join(f'"{p}"' if (" " in p or not p) else p for p in params + ["--elevated"])
    try:
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, cmd, os.getcwd(), 1)
    except Exception:  # noqa: BLE001
        return False
    return r > 32          # <=32: errore o UAC rifiutato -> si continua senza privilegi
