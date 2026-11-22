"""Barra di softkey in stile HP 9845C: otto etichette in video inverso, con scorciatoie F1..F8.

    bar = SoftKeyBar(window)
    bar.set_keys([("GEOM", on_geom), ("MESH", on_mesh), ("PORTE", None), ("RUN\\nSIM", on_run)])
    layout.addWidget(bar)

Le etichette possono avere due righe (``\\n``). Un tasto senza etichetta e' spento (nero su nero).
Cambiando contesto si richiama ``set_keys`` con un nuovo set, come il menu delle softkey di
un analizzatore HP.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

N_KEYS = 8


class SoftKeyBar(QWidget):
    triggered = Signal(int)      # indice 0..7 del tasto premuto

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SoftKeyBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 2)
        lay.setSpacing(4)
        self._btns: list[QPushButton] = []
        self._cbs: list[Optional[Callable[[], None]]] = [None] * N_KEYS
        for i in range(N_KEYS):
            b = QPushButton("")
            b.setObjectName("SoftKey")
            b.setEnabled(False)
            b.clicked.connect(lambda _=False, k=i: self._fire(k))
            lay.addWidget(b, 1)
            self._btns.append(b)
        self._shortcuts: list[QShortcut] = []
        if parent is not None:
            self.bind_shortcuts(parent)

    def bind_shortcuts(self, window: QWidget) -> None:
        """Collega F1..F8 alla finestra indicata (scorciatoie valide in tutta la finestra)."""
        for s in self._shortcuts:
            s.setParent(None)
        self._shortcuts = [QShortcut(QKeySequence(f"F{i + 1}"), window, lambda k=i: self._fire(k)) for i in range(N_KEYS)]

    def set_keys(self, keys: Sequence[tuple[str, Optional[Callable[[], None]]]]) -> None:
        for i, b in enumerate(self._btns):
            label, cb = keys[i] if i < len(keys) else ("", None)
            self._cbs[i] = cb
            b.setText(label)
            b.setEnabled(bool(label))

    def _fire(self, k: int) -> None:
        if not self._btns[k].isEnabled():
            return
        cb = self._cbs[k]
        if cb:
            cb()
        self.triggered.emit(k)
