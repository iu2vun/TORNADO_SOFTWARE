"""Avvio dell'applicazione grafica."""
from __future__ import annotations

import sys


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow
    from .theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setOrganizationName("SerialSniffer")
    app.setApplicationName("Serial Sniffer")
    apply_theme(app, str(QSettings("SerialSniffer", "SerialSniffer").value("theme", "system")))
    win = MainWindow()
    win.show()
    return app.exec()
