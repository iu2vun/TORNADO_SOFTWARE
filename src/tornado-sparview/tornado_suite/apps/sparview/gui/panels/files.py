"""Albero delle reti caricate + selettore a matrice dei parametri S."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QPushButton, QScrollArea, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ...core.units import fmt_eng
from ..model import Workspace


class MatrixSelector(QWidget):
    """Griglia N×N di pulsanti Sij (riga = porta di risposta, colonna = porta eccitata)."""

    def __init__(self, ws: Workspace, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.uid: Optional[int] = None
        self.buttons: dict[tuple[int, int], QToolButton] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        quick = QHBoxLayout()
        quick.setSpacing(3)
        for text, tip, fn in (("Tutti", "Seleziona tutti i parametri", self.select_all),
                              ("Nessuno", "Deseleziona tutto", self.select_none),
                              ("Sii", "Solo riflessioni", self.select_diag),
                              ("i>j", "Trasmissioni (triangolo inferiore, utile per reti reciproche)", self.select_lower),
                              ("i≠j", "Tutte le trasmissioni", self.select_trans)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            quick.addWidget(b)
        quick.addStretch(1)
        lay.addLayout(quick)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.holder = QWidget()
        self.grid = QGridLayout(self.holder)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setSpacing(3)
        self.holder.setObjectName('holder')
        self.holder.setStyleSheet('#holder { background: transparent; }')
        self.area.setStyleSheet('QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }')
        self.area.setWidget(self.holder)
        self.area.viewport().setAutoFillBackground(False)
        self.holder.setAutoFillBackground(False)
        lay.addWidget(self.area, 1)
        self.hint = QLabel("")
        self.hint.setProperty("dim", True)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        ws.current_changed.connect(self.rebuild)
        ws.changed.connect(self.sync)
        ws.network_removed.connect(self._removed)

    def _removed(self, uid: int) -> None:
        if self.uid == uid:
            self.rebuild(self.ws.current_uid if self.ws.current_uid is not None else -1)

    def rebuild(self, uid: int) -> None:
        while self.grid.count():
            it = self.grid.takeAt(0)
            wd = it.widget()
            if wd is not None:
                wd.hide()
                wd.setParent(None)
                wd.deleteLater()
        self.buttons.clear()
        e = self.ws.entry(uid) if uid is not None and uid >= 0 else None
        self.uid = e.net.uid if e else None
        if e is None:
            self.hint.setText("Nessuna rete selezionata")
            return
        net = e.net
        n = net.nports
        small = n > 8
        corner = QLabel("↓ rx / tx →")
        corner.setProperty("dim", True)
        self.grid.addWidget(corner, 0, 0)
        for j in range(n):
            h = QToolButton()
            h.setText(net.port_label(j))
            h.setAutoRaise(True)
            h.setToolTip(f"Seleziona/deseleziona la colonna: eccitazione sulla porta {net.port_label(j)}")
            h.clicked.connect(lambda _=False, j=j: self._toggle_line(col=j))
            self.grid.addWidget(h, 0, j + 1)
        for i in range(n):
            h = QToolButton()
            h.setText(net.port_label(i))
            h.setAutoRaise(True)
            h.setToolTip(f"Seleziona/deseleziona la riga: risposta sulla porta {net.port_label(i)}")
            h.clicked.connect(lambda _=False, i=i: self._toggle_line(row=i))
            self.grid.addWidget(h, i + 1, 0)
            for j in range(n):
                b = QToolButton()
                b.setCheckable(True)
                lab = net.label(i, j)
                b.setText(lab if not small else lab[1:])
                b.setMinimumSize(40 if not small else 28, 24)
                b.setToolTip(f"{lab}  (clic destro: colore traccia)")
                b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda _p, i=i, j=j: self._color_menu(i, j))
                b.clicked.connect(lambda _=False, i=i, j=j: self.ws.toggle_cell(net.uid, i, j))
                self.grid.addWidget(b, i + 1, j + 1)
                self.buttons[(i, j)] = b
        self.grid.setColumnStretch(n + 1, 1)
        self.grid.setRowStretch(n + 1, 1)
        self.sync()

    def sync(self) -> None:
        e = self.ws.entry(self.uid) if self.uid is not None else None
        if e is None:
            return
        for (i, j), b in self.buttons.items():
            on = (i, j) in e.cells
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
            col = e.colors.get((i, j), "#FFFFFF")
            b.setStyleSheet(f"QToolButton:checked {{ background: {col}; border-color: {col}; color: #000000; }}")
        net = e.net
        self.hint.setText(f"{net.nports} porte · {len(e.cells)} tracce selezionate")

    def _color_menu(self, i: int, j: int) -> None:
        e = self.ws.entry(self.uid)
        if not e:
            return
        c = QColorDialog.getColor(QColor(e.colors.get((i, j), "#FFFFFF")), self, "Colore traccia")
        if c.isValid():
            self.ws.set_color(self.uid, i, j, c.name())

    def _cells(self, pred) -> set:
        e = self.ws.entry(self.uid)
        if not e:
            return set()
        n = e.net.nports
        return {(i, j) for i in range(n) for j in range(n) if pred(i, j)}

    def _toggle_line(self, row: Optional[int] = None, col: Optional[int] = None) -> None:
        e = self.ws.entry(self.uid)
        if not e:
            return
        cells = self._cells(lambda i, j: (row is not None and i == row) or (col is not None and j == col))
        if cells <= e.cells:
            self.ws.set_cells(self.uid, e.cells - cells)
        else:
            self.ws.set_cells(self.uid, e.cells | cells)

    def select_all(self) -> None:
        self.ws.set_cells(self.uid, self._cells(lambda i, j: True))

    def select_none(self) -> None:
        self.ws.set_cells(self.uid, set())

    def select_diag(self) -> None:
        self.ws.set_cells(self.uid, self._cells(lambda i, j: i == j))

    def select_lower(self) -> None:
        self.ws.set_cells(self.uid, self._cells(lambda i, j: i > j))

    def select_trans(self) -> None:
        self.ws.set_cells(self.uid, self._cells(lambda i, j: i != j))


class FilesPanel(QWidget):
    openRequested = Signal()
    saveRequested = Signal(int)
    duplicateRequested = Signal(int)

    def __init__(self, ws: Workspace, parent=None):
        super().__init__(parent)
        self.ws = ws
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        bar = QHBoxLayout()
        b_open = QPushButton("Apri…")
        b_open.setProperty("accent", True)
        b_open.clicked.connect(self.openRequested)
        self.b_remove = QPushButton("Rimuovi")
        self.b_remove.clicked.connect(self._remove_current)
        bar.addWidget(b_open)
        bar.addWidget(self.b_remove)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Rete", "Dati"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 170)
        lay.addWidget(self.tree, 3)
        box = QGroupBox("Parametri S")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(6, 14, 6, 6)
        self.matrix = MatrixSelector(ws)
        bl.addWidget(self.matrix)
        lay.addWidget(box, 2)
        self._items: dict[int, QTreeWidgetItem] = {}
        self._busy = False
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(lambda it, c: self._rename(it.data(0, Qt.ItemDataRole.UserRole)))
        ws.network_added.connect(self._added)
        ws.network_removed.connect(self._removed)
        ws.changed.connect(self._refresh_text)
        ws.current_changed.connect(self._select)

    # ------------------------------------------------------------- albero
    @staticmethod
    def _info(net) -> str:
        step = ""
        if net.nfreq > 1:
            step = f" · Δ {fmt_eng((net.fmax - net.fmin) / (net.nfreq - 1), 'Hz', 4)}"
        return f"{net.nports}p · {net.nfreq} pt · {fmt_eng(net.fmin, 'Hz', 4)} – {fmt_eng(net.fmax, 'Hz', 4)}{step}"

    def _added(self, uid: int) -> None:
        e = self.ws.entry(uid)
        if not e:
            return
        it = QTreeWidgetItem([e.net.name, self._info(e.net)])
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(0, Qt.CheckState.Checked if e.visible else Qt.CheckState.Unchecked)
        it.setData(0, Qt.ItemDataRole.UserRole, uid)
        it.setToolTip(0, e.net.path or e.net.name)
        it.setToolTip(1, f"Formato: {e.net.source_format or '—'}   z0: " +
                      ", ".join(f"{z:g}" for z in e.net.z0) + " Ω")
        self._busy = True
        self.tree.addTopLevelItem(it)
        self._busy = False
        self._items[uid] = it

    def _removed(self, uid: int) -> None:
        it = self._items.pop(uid, None)
        if it is not None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))

    def _refresh_text(self) -> None:
        self._busy = True
        for uid, it in list(self._items.items()):
            e = self.ws.entry(uid)
            if e is None:
                # rete sostituita (uid cambiato): rimappa
                continue
            if it.text(0) != e.net.name:
                it.setText(0, e.net.name)
            it.setText(1, self._info(e.net))
            st = Qt.CheckState.Checked if e.visible else Qt.CheckState.Unchecked
            if it.checkState(0) != st:
                it.setCheckState(0, st)
        self._busy = False
        # uid sostituiti
        known = set(self._items)
        for e in self.ws.entries:
            if e.net.uid not in known:
                self._added(e.net.uid)
        live = {e.net.uid for e in self.ws.entries}
        for uid in list(self._items):
            if uid not in live:
                self._removed(uid)

    def _item_changed(self, it: QTreeWidgetItem, col: int) -> None:
        if self._busy or col != 0:
            return
        uid = it.data(0, Qt.ItemDataRole.UserRole)
        self.ws.set_visible(uid, it.checkState(0) == Qt.CheckState.Checked)

    def _current_changed(self, cur: QTreeWidgetItem, prev) -> None:
        if cur is not None:
            self.ws.set_current(cur.data(0, Qt.ItemDataRole.UserRole))

    def _select(self, uid: int) -> None:
        it = self._items.get(uid)
        if it is not None and self.tree.currentItem() is not it:
            self.tree.setCurrentItem(it)

    def _remove_current(self) -> None:
        it = self.tree.currentItem()
        if it is not None:
            self.ws.remove_network(it.data(0, Qt.ItemDataRole.UserRole))

    def _rename(self, uid: int) -> None:
        net = self.ws.network(uid)
        if not net:
            return
        name, ok = QInputDialog.getText(self, "Rinomina rete", "Nome:", text=net.name)
        if ok and name.strip():
            self.ws.rename(uid, name.strip())

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        uid = it.data(0, Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("Rinomina…").triggered.connect(lambda: self._rename(uid))
        m.addAction("Duplica").triggered.connect(lambda: self.duplicateRequested.emit(uid))
        m.addAction("Salva come…").triggered.connect(lambda: self.saveRequested.emit(uid))
        m.addSeparator()
        m.addAction("Rimuovi").triggered.connect(lambda: self.ws.remove_network(uid))
        m.exec(self.tree.viewport().mapToGlobal(pos))
