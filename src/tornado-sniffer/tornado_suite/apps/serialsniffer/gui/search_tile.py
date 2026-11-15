"""Vista Cerca: pattern (testo / hex con jolly / regex) sui byte; doppio clic = salta lì nella riproduzione."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QLabel, QLineEdit, QTableView, QToolButton, QWidget,
)

from ..chunk import DIR_NAMES, Chunk
from ..search import MODE_LABELS, MODES, Hit, PatternError, compile_pattern, find_in_chunk, search_chunks
from .hub import DataHub
from .theme import colors_for, mono_font
from .tiles import BaseTile

HEAD = ["Tempo (s)", "Canale", "Dir", "Offset", "Trovato (hex)", "Contesto"]
CAP = 20_000


def _ascii(b: bytes) -> str:
    return "".join(chr(x) if 32 <= x < 127 else "·" for x in b)


class HitModel(QAbstractTableModel):
    def __init__(self, tile: "SearchTile") -> None:
        super().__init__(tile)
        self.tile = tile
        self.rows: list[Hit] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(HEAD)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEAD[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        h = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            col = index.column()
            if col == 0:
                return f"{h.ts_ns / 1e9:.6f}"
            if col == 1:
                return self.tile.hub.name(h.channel)
            if col == 2:
                return DIR_NAMES.get(h.direction, "?")
            if col == 3:
                return str(h.offset)
            if col == 4:
                # il match è dentro il contesto: ricostruito dall'offset relativo (max 8 byte di contesto a sinistra)
                lead = min(8, h.offset)
                return h.context[lead:lead + h.length].hex(" ").upper()
            return _ascii(h.context)
        if role == Qt.ItemDataRole.BackgroundRole and index.row() % 2:
            return colors_for(self.tile.palette())["lane"]
        return None

    def reset(self, rows: list[Hit]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def append(self, rows: list[Hit]) -> None:
        room = CAP - len(self.rows)
        rows = rows[:max(0, room)]
        if rows:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
            self.rows.extend(rows)
            self.endInsertRows()


class SearchTile(BaseTile):
    kind = "search"
    title = "Cerca"
    goto_requested = Signal(int)

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.pat = None
        self.mode = "text"
        self.ignore_case = False
        self.whole = False
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Pattern (Invio per cercare) — testo: \\r \\n \\xNN ammessi")
        self.cmb_mode = QComboBox()
        for m in MODES:
            self.cmb_mode.addItem(MODE_LABELS[m], m)
        self.chk_case = QCheckBox("Aa")
        self.chk_case.setToolTip("Ignora maiuscole/minuscole (testo e regex)")
        self.chk_all = QCheckBox("Tutta la registrazione")
        self.chk_all.setToolTip("In riproduzione: cerca in tutta la registrazione, non solo nella parte già mostrata")
        self.btn_go = QToolButton()
        self.btn_go.setText("Cerca")
        self._insert_extra(self.cmb_mode)
        self._insert_extra(self.chk_case)
        self._insert_extra(self.chk_all)
        self._insert_extra(self.btn_go)
        self._root.insertWidget(1, self.edit)
        self.model = HitModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setFont(mono_font())
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(False)
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate((92, 70, 40, 60, 200)):
            self.view.setColumnWidth(i, w)
        self.lbl = QLabel("")
        self._root.addWidget(self.view, 1)
        self._root.addWidget(self.lbl)
        self.edit.returnPressed.connect(self.rebuild)
        self.btn_go.clicked.connect(self.rebuild)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode)
        self.chk_case.toggled.connect(lambda on: self._set_case(on))
        self.chk_all.toggled.connect(lambda on: self._set_whole(on))
        self.view.doubleClicked.connect(self._goto)

    # ----------------------------------------------------------------- stato
    def _on_mode(self) -> None:
        self.mode = self.cmb_mode.currentData()
        self.rebuild()

    def _set_case(self, on: bool) -> None:
        self.ignore_case = on
        self.rebuild()

    def _set_whole(self, on: bool) -> None:
        self.whole = on
        self.rebuild()

    def _compile(self) -> bool:
        text = self.edit.text()
        if not text:
            self.pat = None
            return False
        try:
            self.pat = compile_pattern(text, self.mode, self.ignore_case)
        except PatternError as exc:
            self.pat = None
            self.lbl.setText(f"Pattern non valido: {exc}")
            return False
        return True

    # ------------------------------------------------------------- BaseTile
    def reset(self) -> None:
        if self.whole and self.hub.full_source is not None:
            return  # i risultati sull'intera registrazione sopravvivono a seek e loop
        self.model.reset([])
        self._count()

    def rebuild(self) -> None:
        self.model.reset([])
        if not self._compile():
            if not self.edit.text():
                self.lbl.setText("")
            return
        if self.whole and self.hub.full_source is not None:
            src = self.hub.full_source()
        else:
            src = list(self.hub.history)
        hits = search_chunks(src, self.pat, self.channel_filter, self.dir_filter, limit=CAP)
        self.model.reset(hits)
        self._count()

    def append(self, chunks: list[Chunk]) -> None:
        if self.pat is None or (self.whole and self.hub.full_source is not None):
            return
        out: list[Hit] = []
        for c in chunks:
            out += find_in_chunk(self.pat, c)
        if out:
            self.model.append(out)
            self.view.scrollToBottom()
            self._count()

    def _count(self) -> None:
        n = len(self.model.rows)
        self.lbl.setText(f"{n} risultati" + (f" (limite {CAP})" if n >= CAP else ""))

    def _goto(self, idx: QModelIndex) -> None:
        if idx.isValid() and idx.row() < len(self.model.rows):
            self.goto_requested.emit(self.model.rows[idx.row()].ts_ns)

    def get_state(self) -> dict:
        return {**super().get_state(), "pattern": self.edit.text(), "mode": self.mode, "case": self.ignore_case,
                "whole": self.whole}

    def set_state(self, st: dict) -> None:
        self.mode = st.get("mode") if st.get("mode") in MODES else "text"
        self.ignore_case = bool(st.get("case", False))
        self.whole = bool(st.get("whole", False))
        self.edit.setText(str(st.get("pattern", "")))
        self.cmb_mode.blockSignals(True)
        self.cmb_mode.setCurrentIndex(max(0, self.cmb_mode.findData(self.mode)))
        self.cmb_mode.blockSignals(False)
        for w, v in ((self.chk_case, self.ignore_case), (self.chk_all, self.whole)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        super().set_state(st)
        self.rebuild()
