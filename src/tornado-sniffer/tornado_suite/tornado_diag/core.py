from __future__ import annotations

import importlib
import logging
import os
import platform
import sys
import tempfile
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

_state = {"app": "app", "version": "", "installed": False, "on_error": None}
MODULES = ("PySide6", "PyQt5", "PyQt6", "numpy", "scipy", "pyqtgraph", "serial", "pyvisa", "pyvisa_py", "usb",
           "matplotlib", "SoapySDR", "iio", "skrf", "requests")


def log_dir(app: Optional[str] = None) -> Path:
    app = app or _state["app"]
    base = os.environ.get("APPDATA") or os.environ.get("XDG_STATE_HOME") or os.path.join(tempfile.gettempdir())
    p = Path(base) / app / "logs"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        p = Path(tempfile.gettempdir()) / app / "logs"
        p.mkdir(parents=True, exist_ok=True)
    return p


def log_path(app: Optional[str] = None) -> Path:
    return log_dir(app) / "app.log"


def get_logger(name: str = "") -> logging.Logger:
    return logging.getLogger(f"{_state['app']}.{name}" if name else _state["app"])


def _write_exc(kind: str, et, ev, tb) -> str:
    text = "".join(traceback.format_exception(et, ev, tb))
    get_logger().critical("%s non gestita:\n%s", kind, text)
    return text


def install(app_name: str, version: str = "", max_mb: float = 2.0, backups: int = 3, console: bool = True,
            on_error: Optional[Callable[[str], None]] = None) -> Path:
    """Attiva log e hook. Idempotente. ``on_error(testo)`` viene chiamata per ogni eccezione non gestita
    (di default, se esiste una QApplication, mostra il dialogo con «Copia diagnostica»)."""
    _state.update(app=app_name, version=version, on_error=on_error)
    lg = logging.getLogger(app_name)
    if _state["installed"]:
        return log_path()
    lg.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(log_path(), maxBytes=int(max_mb * 1e6), backupCount=backups, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"))
    lg.addHandler(fh)
    if console and sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        lg.addHandler(sh)
    lg.info("=== avvio %s %s | Python %s | %s ===", app_name, version, platform.python_version(), platform.platform())

    def _notify(text: str) -> None:
        cb = _state["on_error"]
        try:
            if cb:
                cb(text)
            else:
                _default_notify(text)
        except Exception:  # noqa: BLE001 - la gestione errori non deve mai generare altri errori
            pass

    def hook(et, ev, tb):
        if issubclass(et, KeyboardInterrupt):
            sys.__excepthook__(et, ev, tb)
            return
        _notify(_write_exc("Eccezione", et, ev, tb))

    def thook(args):
        _notify(_write_exc(f"Eccezione nel thread {getattr(args.thread, 'name', '?')}", args.exc_type, args.exc_value,
                           args.exc_traceback))

    def uhook(u):
        get_logger().error("Eccezione ignorata in %r: %r", u.object, u.exc_value)

    sys.excepthook = hook
    threading.excepthook = thook
    sys.unraisablehook = uhook
    _install_qt_handler()
    _state["installed"] = True
    return log_path()


def _install_qt_handler() -> None:
    for mod in ("PySide6.QtCore", "PyQt6.QtCore", "PyQt5.QtCore"):
        try:
            qc = importlib.import_module(mod)
        except ImportError:
            continue
        lvl = {getattr(qc.QtMsgType, "QtDebugMsg"): logging.DEBUG, qc.QtMsgType.QtInfoMsg: logging.INFO,
               qc.QtMsgType.QtWarningMsg: logging.WARNING, qc.QtMsgType.QtCriticalMsg: logging.ERROR,
               qc.QtMsgType.QtFatalMsg: logging.CRITICAL}

        def handler(mode, ctx, msg):
            get_logger("qt").log(lvl.get(mode, logging.WARNING), "%s", msg)

        try:
            qc.qInstallMessageHandler(handler)
        except Exception:  # noqa: BLE001
            pass
        return


def _default_notify(text: str) -> None:
    try:
        from .dialog import show_error_dialog
        show_error_dialog(text)
    except Exception:  # noqa: BLE001
        pass


def tail_log(n: int = 80) -> str:
    p = log_path()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(log non disponibile)"


def _version(mod: str) -> str:
    try:
        m = importlib.import_module(mod)
    except ImportError:
        return ""
    except Exception as e:  # noqa: BLE001
        return f"ERRORE import: {e}"
    return str(getattr(m, "__version__", getattr(m, "VERSION", "ok")))


def _com_ports() -> list[str]:
    try:
        from serial.tools import list_ports
        return [f"{p.device}: {p.description} [{p.hwid}]" for p in sorted(list_ports.comports())] or ["(nessuna)"]
    except ImportError:
        return ["(pyserial non installato)"]
    except Exception as e:  # noqa: BLE001
        return [f"(errore: {e})"]


def _visa_resources() -> list[str]:
    try:
        import pyvisa
    except ImportError:
        return ["(pyvisa non installato)"]
    try:
        rm = pyvisa.ResourceManager()
        res = list(rm.list_resources())
        return [f"{r}" for r in res] + [f"(backend: {rm.visalib})"] if res else [f"(nessuna risorsa) (backend: {rm.visalib})"]
    except Exception as e:  # noqa: BLE001
        return [f"(VISA non disponibile: {str(e).splitlines()[0][:200]})"]


def collect_diagnostics(log_lines: int = 60, extra: Optional[dict] = None, probe_hw: bool = True) -> str:
    """Testo unico, da incollare in chat, con tutto ciò che serve per capire un problema."""
    L = [f"## Diagnostica {_state['app']} {_state['version']}".strip(),
         f"Python: {sys.version.split()[0]} ({'frozen/exe' if getattr(sys, 'frozen', False) else 'script'}) {platform.architecture()[0]}",
         f"Sistema: {platform.platform()}",
         f"Eseguibile: {sys.executable}",
         f"Cartella lavoro: {os.getcwd()}",
         f"Log: {log_path()}"]
    if os.name == "nt":
        try:
            import ctypes
            L.append(f"Amministratore: {bool(ctypes.windll.shell32.IsUserAnAdmin())}")
        except Exception:  # noqa: BLE001
            pass
    L.append("### Moduli")
    for m in MODULES:
        v = _version(m)
        if v:
            L.append(f"  {m}: {v}")
    if probe_hw:
        L.append("### Porte COM")
        L += ["  " + x for x in _com_ports()]
        L.append("### Risorse VISA")
        L += ["  " + x for x in _visa_resources()]
    if extra:
        L.append("### Extra")
        L += [f"  {k}: {v}" for k, v in extra.items()]
    L.append(f"### Ultime {log_lines} righe di log")
    L.append(tail_log(log_lines).rstrip())
    return "\n".join(L)
