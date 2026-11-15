"""Vista di decodifica di protocollo: tabella dei messaggi decodificati + dettaglio dei campi."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QSplitter,
    QTableView, QToolButton, QWidget,
)

from .. import decoders
from ..chunk import DIR_NAMES, RX, Chunk
from ..decoders import DecoderEngine, Message
from ..exporters import write_messages
from ..formats import hexdump
from .hub import DataHub
from .theme import colors_for, mono_font
from .tiles import BaseTile


class MessageModel(QAbstractTableModel):
    HEAD = ["Tempo (s)", "Canale", "Dir", "Protocollo", "Tipo", "Messaggio", "Stato"]
    CAP = 50_000

    def __init__(self, tile: "DecodeTile") -> None:
        super().__init__(tile)
        self.tile = tile
        self.rows: list[Message] = []
        self.errors = 0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEAD)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEAD[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        m = self.rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return f"{m.ts_ns / 1e9:.6f}"
            if col == 1:
                return self.tile.hub.name(m.channel)
            if col == 2:
                return DIR_NAMES.get(m.direction, "?")
            if col == 3:
                return m.protocol
            if col == 4:
                return m.kind
            if col == 5:
                return m.summary
            return "OK" if m.ok and not m.note else (m.note or "ERRORE")
        if role == Qt.ItemDataRole.ForegroundRole:
            pal = colors_for(self.tile.palette())
            if not m.ok:
                return pal["warn"]
            return pal["rx"] if m.direction == RX else pal["tx"]
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 0:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.BackgroundRole and index.row() % 2:
            return colors_for(self.tile.palette())["lane"]
        if role == Qt.ItemDataRole.ToolTipRole:
            return m.summary
        return None

    def append(self, msgs: list[Message]) -> None:
        if not msgs:
            return
        msgs = msgs[-self.CAP:]
        overflow = len(self.rows) + len(msgs) - self.CAP
        if overflow > 0:
            n = min(overflow, len(self.rows))
            self.beginRemoveRows(QModelIndex(), 0, n - 1)
            self.errors -= sum(not m.ok for m in self.rows[:n])
            del self.rows[:n]
            self.endRemoveRows()
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(msgs) - 1)
        self.rows.extend(msgs)
        self.errors += sum(not m.ok for m in msgs)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.rows.clear()
        self.errors = 0
        self.endResetModel()


class ProtoTile(BaseTile):
    """Base delle viste che lavorano su messaggi decodificati: scelta protocollo (o auto) e master Modbus, motore di
    decodifica con stato, flush periodico dei residui. Le sottoclassi implementano `on_messages` e `clear_view`."""

    FLUSH_MS = 250
    has_clear = True

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.proto = "auto"
        self.master: Optional[tuple[int, int]] = None
        self.cmb_proto = QComboBox()
        self.cmb_proto.setToolTip("Protocollo da decodificare")
        self.cmb_master = QComboBox()
        self.cmb_master.setToolTip("Modbus: flusso da cui parte il master. Automatico = dedotto da struttura e richiesta pendente")
        self._insert_extra(self.cmb_proto)
        self._insert_extra(self.cmb_master)
        self.engine = self._make_engine()
        self._fill_protocols()
        self._fill_masters()
        self.cmb_proto.currentIndexChanged.connect(self._on_proto)
        self.cmb_master.currentIndexChanged.connect(self._on_master)
        hub.channels_changed.connect(self._fill_masters)
        self._timer = QTimer(self)
        self._timer.setInterval(self.FLUSH_MS)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    # --------------------------------------------------------- da specializzare
    def on_messages(self, msgs: list[Message]) -> None:
        raise NotImplementedError

    def clear_view(self) -> None:
        raise NotImplementedError

    # ----------------------------------------------------------------- motore
    def _make_engine(self) -> DecoderEngine:
        opts = {}
        if self.proto in ("modbus", "modbus_ascii", "auto") and self.master is not None:
            opts["masters"] = {self.master}
        try:
            return DecoderEngine(decoders.create(self.proto, **opts))
        except (ValueError, TypeError):
            self.proto = "auto"
            return DecoderEngine(decoders.create("auto"))

    def _fill_protocols(self) -> None:
        self.cmb_proto.blockSignals(True)
        self.cmb_proto.clear()
        for name in sorted(decoders.DECODERS, key=lambda n: (n != "auto", n)):
            self.cmb_proto.addItem(decoders.DECODERS[name].label or name, name)
        self.cmb_proto.setCurrentIndex(max(0, self.cmb_proto.findData(self.proto)))
        self.cmb_proto.blockSignals(False)

    def refresh_protocols(self) -> None:
        """Chiamata dopo il caricamento di un plugin."""
        self._fill_protocols()

    def _fill_masters(self) -> None:
        self.cmb_master.blockSignals(True)
        self.cmb_master.clear()
        self.cmb_master.addItem("Master: auto", None)
        for idx in sorted(self.hub.channels):
            for d in (0, 1):
                self.cmb_master.addItem(f"Master: {self.hub.name(idx)} {DIR_NAMES[d]}", (idx, d))
        pos = self.cmb_master.findData(self.master)
        if pos < 0:
            pos = 0
            self.master = None
        self.cmb_master.setCurrentIndex(pos)
        self.cmb_master.blockSignals(False)
        self.cmb_master.setVisible(self.proto in ("modbus", "modbus_ascii", "auto"))

    def _on_proto(self) -> None:
        self.proto = self.cmb_proto.currentData() or "auto"
        self.cmb_master.setVisible(self.proto in ("modbus", "modbus_ascii", "auto"))
        self.engine = self._make_engine()
        self.rebuild()

    def _on_master(self) -> None:
        self.master = self.cmb_master.currentData()
        self.engine = self._make_engine()
        self.rebuild()

    def _flush(self) -> None:
        if not self._paused:
            self.on_messages(self.engine.flush(self.hub.now_ns()))

    # --------------------------------------------------------------- BaseTile
    def reset(self) -> None:
        self.engine.reset()
        self.clear_view()

    def append(self, chunks: list[Chunk]) -> None:
        self.on_messages(self.engine.feed_many(chunks))

    def assignment_text(self) -> str:
        assign = getattr(self.engine.decoder, "assignments", None)
        if not assign:
            return ""
        parts = [f"{self.hub.name(ch)}→{decoders.DECODERS[p].label if p else '?'}" for ch, p in assign().items()]
        return "   |   " + "  ".join(parts) if parts else ""

    # ------------------------------------------------------------ persistenza
    def get_state(self) -> dict:
        return {**super().get_state(), "proto": self.proto, "master": list(self.master) if self.master else None}

    def set_state(self, st: dict) -> None:
        self.proto = st.get("proto") if st.get("proto") in decoders.DECODERS else "auto"
        m = st.get("master")
        self.master = (int(m[0]), int(m[1])) if isinstance(m, (list, tuple)) and len(m) == 2 else None
        self.engine = self._make_engine()
        self._fill_protocols()
        self._fill_masters()
        super().set_state(st)


class DecodeTile(ProtoTile):
    """Tabella dei messaggi decodificati + dettaglio dei campi del messaggio selezionato."""

    kind = "decode"
    title = "Protocollo"

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.only_errors = False
        self.follow = True
        self.chk_err = QCheckBox("Solo errori")
        self.chk_follow = QCheckBox("Segui")
        self.chk_follow.setChecked(True)
        self.btn_export = QToolButton()
        self.btn_export.setText("Esporta…")
        self.btn_export.setToolTip("Salva i messaggi mostrati in CSV o JSONL")
        self._insert_extra(self.chk_err)
        self._insert_extra(self.chk_follow)
        self._insert_extra(self.btn_export)
        self.lbl_stat = QLabel("")

        self.model = MessageModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setFont(mono_font())
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setAlternatingRowColors(False)  # righe alternate dal modello: l'accento di sistema non le tinge
        self.view.setShowGrid(False)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for i, w in enumerate((92, 70, 40, 90, 72, 420)):
            self.view.setColumnWidth(i, w)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(mono_font())
        self.detail.setPlaceholderText("Seleziona un messaggio per vedere i campi decodificati e i byte grezzi")
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.view)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        self._root.addWidget(split, 1)
        self._root.addWidget(self.lbl_stat)

        self.chk_err.toggled.connect(self._on_errors)
        self.chk_follow.toggled.connect(self._on_follow)
        self.btn_export.clicked.connect(self._export)
        self.view.selectionModel().currentRowChanged.connect(self._show_detail)
        self.rebuild()

    def _on_errors(self, on: bool) -> None:
        self.only_errors = on
        self.rebuild()

    def _on_follow(self, on: bool) -> None:
        self.follow = on
        if on:
            self.view.scrollToBottom()

    def clear_view(self) -> None:
        self.model.clear()
        self.detail.clear()
        self._stats()

    def on_messages(self, msgs: list[Message]) -> None:
        if self.only_errors:
            msgs = [m for m in msgs if not m.ok]
        if msgs:
            self.model.append(msgs)
            if self.follow:
                self.view.scrollToBottom()
        self._stats()

    def _stats(self) -> None:
        n = len(self.model.rows)
        self.lbl_stat.setText(f"{n} messaggi" + (f", {self.model.errors} non validi" if self.model.errors else "")
                              + self.assignment_text())

    def _export(self) -> None:
        if not self.model.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta messaggi", "messaggi.csv", "CSV (*.csv);;JSONL (*.jsonl)")
        if not path:
            return
        try:
            write_messages(self.model.rows, path, {i: self.hub.name(i) for i in self.hub.channels})
        except OSError as exc:
            QMessageBox.critical(self, "Esportazione non riuscita", str(exc))
            return
        self.lbl_stat.setText(f"Salvato: {path}")

    def _show_detail(self, cur: QModelIndex, _prev: QModelIndex) -> None:
        if not cur.isValid() or cur.row() >= len(self.model.rows):
            self.detail.clear()
            return
        m = self.model.rows[cur.row()]
        lines = [f"{m.protocol}  {m.kind}  t = {m.ts_ns / 1e9:.6f} s  {self.hub.name(m.channel)} {DIR_NAMES.get(m.direction, '?')}",
                 m.summary, ""]
        if not m.ok or m.note:
            lines += [f"Stato: {'ERRORE' if not m.ok else 'nota'} — {m.note or 'non valido'}", ""]
        w = max((len(k) for k, _ in m.fields), default=0)
        lines += [f"{k:<{w}}  {v}" for k, v in m.fields]
        if m.raw:
            lines += ["", hexdump(m.raw[:512])]
        self.detail.setPlainText("\n".join(lines))

    # ------------------------------------------------------------ persistenza
    def get_state(self) -> dict:
        return {**super().get_state(), "errors": self.only_errors, "follow": self.follow}

    def set_state(self, st: dict) -> None:
        self.only_errors = bool(st.get("errors", False))
        self.follow = bool(st.get("follow", True))
        for w, v in ((self.chk_err, self.only_errors), (self.chk_follow, self.follow)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        super().set_state(st)
        self.rebuild()
