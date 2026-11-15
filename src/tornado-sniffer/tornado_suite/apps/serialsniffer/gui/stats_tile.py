"""Vista statistiche: frequenza, jitter e latenza richiesta→risposta per (canale, protocollo, chiave)."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHeaderView, QLabel, QMessageBox, QTableView, QToolButton, QWidget,
)

from ..decoders import Message
from ..stats import StatsCollector, write_csv, write_json
from .decode_tile import ProtoTile
from .hub import DataHub
from .theme import colors_for, mono_font

COLS = (
    ("Canale", "channel", 0), ("Protocollo", "protocol", 0), ("Chiave", "key", 0), ("N", "count", 0),
    ("Err", "errors", 0), ("Hz", "rate_hz", 2), ("Δt medio ms", "interval_mean_ms", 2), ("Δt σ ms", "interval_std_ms", 2),
    ("Lat media ms", "lat_mean_ms", 2), ("Lat p95 ms", "lat_p95_ms", 2), ("Lat max ms", "lat_max_ms", 2),
    ("Senza risp.", "unanswered", 0),
)


class StatsModel(QAbstractTableModel):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section][0]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        _h, key, digits = COLS[index.column()]
        row = self.rows[index.row()]
        v = row[key]
        if role == Qt.ItemDataRole.DisplayRole:
            if v is None:
                return ""
            return f"{v:.{digits}f}" if isinstance(v, float) else str(v)
        if role == Qt.ItemDataRole.UserRole:  # chiave di ordinamento: numeri come numeri, vuoti in fondo
            return -1.0 if v is None else v
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) if index.column() > 2 else None
        if role == Qt.ItemDataRole.ForegroundRole and key in ("errors", "unanswered") and v:
            return colors_for(self.parent().palette())["warn"] if self.parent() else None
        if role == Qt.ItemDataRole.BackgroundRole and index.row() % 2:
            return colors_for(self.parent().palette())["lane"] if self.parent() else None
        return None


class StatsTile(ProtoTile):
    """Decodifica il traffico e ne accumula le statistiche. Riusa scelta protocollo e master di ProtoTile."""

    kind = "stats"
    title = "Statistiche"
    REFRESH_MS = 1000

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.collector = StatsCollector(self._names())
        self.btn_export = QToolButton()
        self.btn_export.setText("Esporta…")
        self.btn_export.setToolTip("Salva le statistiche in CSV o JSON")
        self._insert_extra(self.btn_export)
        self.model = StatsModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setFont(mono_font())
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(False)
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate((90, 90, 150, 60, 50, 70, 90, 80, 100, 90, 90)):
            self.view.setColumnWidth(i, w)
        self.lbl = QLabel("")
        self._root.addWidget(self.view, 1)
        self._root.addWidget(self.lbl)
        self._dirty = True
        self._rt = QTimer(self)
        self._rt.setInterval(self.REFRESH_MS)
        self._rt.timeout.connect(self._refresh)
        self._rt.start()
        self.btn_export.clicked.connect(self._export)
        hub.channels_changed.connect(lambda: setattr(self.collector, "names", self._names()))
        self.rebuild()

    def _names(self) -> dict[int, str]:
        return {i: self.hub.name(i) for i in self.hub.channels}

    # ------------------------------------------------------------- ProtoTile
    def on_messages(self, msgs: list[Message]) -> None:
        if msgs:
            self.collector.add_many(msgs)
            self._dirty = True

    def clear_view(self) -> None:
        self.collector.reset()
        self._dirty = True
        self._refresh()

    def _refresh(self) -> None:
        if not self._dirty or self._paused:
            return
        self._dirty = False
        rows = self.collector.rows()
        sort = self.view.horizontalHeader().sortIndicatorSection()
        order = self.view.horizontalHeader().sortIndicatorOrder()
        sel = None
        cur = self.view.currentIndex()
        if cur.isValid():
            r = self.model.rows[self.proxy.mapToSource(cur).row()]
            sel = (r["channel"], r["protocol"], r["key"])
        self.model.set_rows(rows)
        self.proxy.sort(sort if self.view.isSortingEnabled() else -1, order)
        if sel:
            for i, r in enumerate(rows):
                if (r["channel"], r["protocol"], r["key"]) == sel:
                    self.view.setCurrentIndex(self.proxy.mapFromSource(self.model.index(i, 0)))
                    break
        n = sum(r["count"] for r in rows)
        e = sum(r["errors"] for r in rows)
        self.lbl.setText(f"{len(rows)} voci, {n} messaggi" + (f", {e} errori" if e else "") + self.assignment_text())

    def _export(self) -> None:
        rows = self.collector.rows()
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta statistiche", "statistiche.csv", "CSV (*.csv);;JSON (*.json)")
        if not path:
            return
        try:
            (write_json if path.lower().endswith(".json") else write_csv)(rows, path)
        except OSError as exc:
            QMessageBox.critical(self, "Esportazione non riuscita", str(exc))
            return
        self.lbl.setText(f"Salvato: {path}")
