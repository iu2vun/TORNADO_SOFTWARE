#!/usr/bin/env python3
"""
TornadoVNA — frontend benchtop per LibreVNA.

Default: tenta USB diretto. Se il device non c'è → demo.
Con --scpi: usa LibreVNA-GUI via SCPI.

Usage:
  python main.py                  # USB diretto (default)
  python main.py --scpi           # SCPI con LibreVNA-GUI
  python main.py --no-autostart   # SCPI, GUI già aperto
  python main.py --demo           # demo sintetico
  python main.py --fullscreen
  python main.py --backend-exe "C:\\...\\LibreVNA-GUI.exe"
"""

APP_VERSION = "1.0.0"

import sys
import argparse
import socket

from PySide6.QtCore import QThread, Signal, QObject, Slot
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFileDialog,
)

from benchvna.mainwindow import MainWindow


# ---------------------------------------------------------------------------
# Dialog avvio SCPI (solo --scpi senza --no-autostart)
# ---------------------------------------------------------------------------
class LaunchWorker(QObject):
    done = Signal(bool, str)

    def __init__(self, backend, host, port, exe, timeout):
        super().__init__()
        self._b, self._host, self._port = backend, host, port
        self._exe, self._timeout = exe, timeout

    @Slot()
    def run(self):
        ok, msg = self._b.ensure_running(
            self._host, self._port, self._exe, self._timeout)
        self.done.emit(ok, msg)


class StartupDialog(QDialog):
    def __init__(self, backend, host, port, exe, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TornadoVNA — Connessione SCPI")
        self.setFixedWidth(480)
        self._backend = backend
        self._host, self._port, self._exe = host, port, exe
        self._ok = False

        lay = QVBoxLayout(self)
        lay.setSpacing(12); lay.setContentsMargins(20, 20, 20, 20)
        self.lbl_status = QLabel("Ricerca LibreVNA-GUI…")
        self.lbl_status.setWordWrap(True); lay.addWidget(self.lbl_status)
        self.bar = QProgressBar(); self.bar.setRange(0, 0); lay.addWidget(self.bar)
        self.lbl_exe = QLabel("")
        self.lbl_exe.setObjectName("Dim")
        self.lbl_exe.setWordWrap(True); lay.addWidget(self.lbl_exe)

        brow = QHBoxLayout()
        self.btn_browse = QPushButton("Sfoglia…")
        self.btn_browse.clicked.connect(self._browse)
        self.btn_retry  = QPushButton("Riprova")
        self.btn_retry.clicked.connect(self._start)
        self.btn_demo   = QPushButton("Demo")
        self.btn_demo.clicked.connect(self.reject)
        self.btn_ok     = QPushButton("Continua")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_ok.setVisible(False)
        for b in (self.btn_browse, self.btn_retry, self.btn_demo):
            brow.addWidget(b)
        brow.addStretch(); brow.addWidget(self.btn_ok)
        lay.addLayout(brow)

        self._thread = None
        self._start()

    def _start(self):
        self.btn_retry.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.btn_ok.setVisible(False)
        self.bar.setRange(0, 0)
        from benchvna.backend import find_executable
        exe = self._exe or find_executable()
        self.lbl_exe.setText(
            f"Eseguibile: {exe}" if exe else "LibreVNA-GUI.exe non trovato.")
        self.lbl_status.setText(
            "Avvio LibreVNA-GUI in background…" if exe
            else "Avvialo manualmente e premi Riprova.")
        w = LaunchWorker(self._backend, self._host, self._port, self._exe, 15.0)
        t = QThread(self)
        w.moveToThread(t)
        w.done.connect(self._on_done)
        t.started.connect(w.run)
        t.start()
        self._thread = t

    @Slot(bool, str)
    def _on_done(self, ok, msg):
        self.bar.setRange(0, 1); self.bar.setValue(1 if ok else 0)
        self.btn_retry.setEnabled(True); self.btn_browse.setEnabled(True)
        self.lbl_status.setText(msg)
        if ok:
            self._ok = True
            self.btn_ok.setVisible(True)
            self.btn_ok.setDefault(True)
            from PySide6.QtCore import QTimer
            QTimer.singleShot(1200, self.accept)
        else:
            pass

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "LibreVNA-GUI.exe", "",
            "Eseguibile (*.exe);;Tutti (*)")
        if path:
            self._exe = path
            self.lbl_exe.setText(f"Eseguibile: {path}")
            self._start()

    @property
    def success(self):
        return self._ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="TornadoVNA")
    ap.add_argument("--host",         default="127.0.0.1")
    ap.add_argument("--port",         type=int, default=19542)
    ap.add_argument("--backend-exe",  default=None)
    ap.add_argument("--scpi",         action="store_true",
                    help="Usa LibreVNA-GUI via SCPI invece di USB diretto")
    ap.add_argument("--no-autostart", action="store_true",
                    help="SCPI: non avviare il GUI, gia' aperto")
    ap.add_argument("--demo",         action="store_true",
                    help="Dati sintetici, zero hardware")
    ap.add_argument("--no-splash",    action="store_true",
                    help="Salta lo splash screen di avvio")
    ap.add_argument("--no-admin",     action="store_true",
                    help="Non richiedere i privilegi di amministratore")
    args, _ = ap.parse_known_args()

    from tornado_ui import run_app
    from benchvna.branding import app_icon

    state = {"backend": None}

    def factory():
        import time
        splash = None
        t0 = time.monotonic()
        if not args.no_splash:
            from benchvna.splash import SplashScreen
            splash = SplashScreen(APP_VERSION)
            splash.show()
            splash.set_progress(0.0)
        demo, worker_class = args.demo, None
        if demo:
            pass
        elif args.scpi:
            if not args.no_autostart:
                from benchvna.backend import BackendProcess
                state["backend"] = BackendProcess()
                dlg = StartupDialog(state["backend"], args.host, args.port, args.backend_exe)
                if dlg.exec() == QDialog.Rejected or not dlg.success:
                    demo = True
        else:
            from benchvna.usb.worker import VnaWorkerUSB       # USB diretto (default)
            worker_class = VnaWorkerUSB
        win = MainWindow(args.host, args.port, demo, worker_class=worker_class)
        win.setWindowIcon(app_icon())
        if splash is not None:
            splash.run(10.0, t0)          # il programma si apre dopo 10 s dall'avvio
        if state["backend"]:
            _orig = win.closeEvent
            def _close(ev):
                _orig(ev)
                state["backend"].stop()
            win.closeEvent = _close
        return win

    # USB diretto => servono i privilegi (driver WinUSB); in demo/SCPI no.
    need_admin = not (args.demo or args.scpi)
    sys.exit(run_app(factory, "TornadoVNA", APP_VERSION, admin=need_admin))


if __name__ == "__main__":
    main()
