"""Esecuzione di funzioni bloccanti (acquisizione, analisi, export) fuori dal thread GUI."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from logic_core import DeviceError


class Worker(QThread):
    """Esegue fn(*args); emette done(tag, risultato) oppure failed(tag, messaggio)."""

    done = Signal(object, object)
    failed = Signal(object, str)

    def __init__(self, tag, fn, *args, parent=None) -> None:
        super().__init__(parent)
        self.tag = tag
        self._fn = fn
        self._args = args

    def run(self) -> None:
        try:
            res = self._fn(*self._args)
        except DeviceError as e:
            self.failed.emit(self.tag, str(e))
            return
        except Exception as e:  # noqa: BLE001 - qualunque errore va mostrato, non deve uccidere il thread
            self.failed.emit(self.tag, f"{type(e).__name__}: {e}")
            return
        self.done.emit(self.tag, res)
