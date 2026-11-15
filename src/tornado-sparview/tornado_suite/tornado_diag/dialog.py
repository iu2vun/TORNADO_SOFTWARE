"""Dialoghi Qt: errore non gestito e finestra «Diagnostica» con copia negli appunti."""
from __future__ import annotations

import importlib

_qt = None
for _m in ("PySide6", "PyQt6", "PyQt5"):
    try:
        _qt = importlib.import_module(_m + ".QtWidgets")
        _qtgui = importlib.import_module(_m + ".QtGui")
        break
    except ImportError:
        continue
if _qt is None:  # pragma: no cover
    raise ImportError("nessun binding Qt disponibile")

from .core import _state, collect_diagnostics, log_dir

QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton, QLabel, QApplication = (
    _qt.QDialog, _qt.QVBoxLayout, _qt.QHBoxLayout, _qt.QPlainTextEdit, _qt.QPushButton, _qt.QLabel, _qt.QApplication)


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None, text: str = "", title: str = "Diagnostica", headline: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        if headline:
            lab = QLabel(headline)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.edit = QPlainTextEdit(text)
        self.edit.setReadOnly(True)
        f = self.edit.font()
        f.setFamily("Consolas")
        self.edit.setFont(f)
        lay.addWidget(self.edit, 1)
        row = QHBoxLayout()
        b_copy = QPushButton("Copia diagnostica")
        b_copy.setProperty("primary", "true")
        b_copy.clicked.connect(self.copy)
        b_open = QPushButton("Apri cartella log")
        b_open.clicked.connect(self.open_logs)
        b_close = QPushButton("Chiudi")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_copy)
        row.addWidget(b_open)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)
        self.status = QLabel("")
        lay.addWidget(self.status)

    def copy(self) -> None:
        QApplication.clipboard().setText(self.edit.toPlainText())
        self.status.setText("Copiato negli appunti: incollalo in chat.")

    def open_logs(self) -> None:
        import os
        import subprocess
        import sys
        p = str(log_dir())
        try:
            if os.name == "nt":
                os.startfile(p)  # type: ignore[attr-defined]  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"Impossibile aprire: {e}")


def show_diagnostics_dialog(parent=None, app_name: str = ""):
    d = DiagnosticsDialog(parent, collect_diagnostics(), f"Diagnostica {app_name or _state['app']}",
                          "Premi «Copia diagnostica» e incolla il testo nella chat.")
    d.exec()
    return d


def show_error_dialog(trace: str) -> None:
    app = QApplication.instance()
    if app is None:
        return
    text = "### Errore non gestito\n" + trace + "\n\n" + collect_diagnostics(probe_hw=False)
    d = DiagnosticsDialog(None, text, f"Errore in {_state['app']}",
                          "Si è verificato un errore. Il programma può continuare, ma conviene segnalarlo: "
                          "premi «Copia diagnostica» e incolla il testo nella chat.")
    d.exec()
