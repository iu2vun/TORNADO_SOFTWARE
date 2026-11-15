"""Avvio standard di un'app Tornado: stesso tema, icona, finestra massimizzata, F11 schermo intero.

    from tornado_ui.app import run_app
    sys.exit(run_app(MainWindow, "Nome App", version="1.0"))

``window_factory`` è una classe/funzione che ritorna un QWidget/QMainWindow (chiamata dopo l'applicazione del tema).
"""
from __future__ import annotations

import os
import sys
from typing import Callable, Optional

ORG = "Tornado Laboratori"


def run_app(window_factory: Callable[[], "QWidget"], name: str, version: str = "1.0", *,  # noqa: F821
            icon_path: Optional[str] = None, admin: bool = False, maximized: bool = True,
            app_id: Optional[str] = None, argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if admin and "--no-admin" not in argv:               # solo se serve davvero (accesso a hardware/USB/seriali)
        from .elevate import relaunch_as_admin
        if relaunch_as_admin():
            return 0

    from PySide6.QtGui import QIcon, QShortcut, QKeySequence
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(name)
    app.setApplicationVersion(version)
    app.setOrganizationName(ORG)
    if sys.platform == "win32":                           # icona corretta nella taskbar
        try:
            import ctypes
            ident = app_id or f"TornadoLaboratori.{name.replace(' ', '')}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ident)
        except Exception:  # noqa: BLE001
            pass
    if icon_path and os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    from .theme import apply_theme
    apply_theme(app)

    win = window_factory()
    if not win.windowTitle():
        win.setWindowTitle(name)
    QShortcut(QKeySequence("F11"), win, lambda: win.showNormal() if win.isFullScreen() else win.showFullScreen())
    win.showMaximized() if maximized else win.show()
    return app.exec()
