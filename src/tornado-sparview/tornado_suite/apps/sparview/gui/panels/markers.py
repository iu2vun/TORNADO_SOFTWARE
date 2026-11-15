"""Pannello marker: tabella letture, ricerca picchi/minimi, modo delta."""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView)

from ...analysis import basic
from ...core.traces import trace_values
from ...core.units import fmt_eng, parse_freq
from ..model import Marker, Workspace
from . import find_data


def interp_complex(freq: np.ndarray, v: np.ndarray, f: float) -> Optional[complex]:
    if f < freq[0] or f > freq[-1]:
        return None
    return complex(np.interp(f, freq, v.real), np.interp(f, freq, v.imag))


class MarkersPanel(QWidget):
    COLS = ["Marker", "Traccia", "Frequenza", "|S| [dB]", "Fase [°]", "VSWR", "Z [Ω]", "Δ rispetto a riferimento"]

    def __init__(self, ws: Workspace, get_kind: Callable[[], str], parent=None):
        super().__init__(parent)
        self.ws = ws
        self.get_kind = get_kind
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        for text, fn, acc in (("Aggiungi", self.add_marker, True), ("Rimuovi", self.remove_active, False),
                              ("Pulisci", ws.markers.clear, False)):
            b = QPushButton(text)
            b.setProperty("accent", acc)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(4)
        lab = QLabel("Ricerca sul marker attivo:")
        lab.setProperty("dim", True)
        grid.addWidget(lab, 0, 0, 1, 4)
        defs = [("◀ Max", lambda: self.search("max", -1)), ("Max globale", lambda: self.search("max", 0)),
                ("Max ▶", lambda: self.search("max", +1)), ("", None),
                ("◀ Min", lambda: self.search("min", -1)), ("Min globale", lambda: self.search("min", 0)),
                ("Min ▶", lambda: self.search("min", +1))]
        for k, (t, fn) in enumerate(defs):
            if fn is None:
                continue
            b = QPushButton(t)
            b.clicked.connect(fn)
            grid.addWidget(b, 1 + k // 4, k % 4)
        lay.addLayout(grid)
        form = QGridLayout()
        form.setSpacing(4)
        form.addWidget(QLabel("Traccia:"), 0, 0)
        self.trace_combo = QComboBox()
        self.trace_combo.activated.connect(self._set_trace)
        form.addWidget(self.trace_combo, 0, 1, 1, 2)
        form.addWidget(QLabel("Δ rispetto a:"), 1, 0)
        self.ref_combo = QComboBox()
        self.ref_combo.activated.connect(self._set_ref)
        form.addWidget(self.ref_combo, 1, 1, 1, 2)
        form.addWidget(QLabel("Frequenza:"), 2, 0)
        self.freq_edit = QLineEdit()
        self.freq_edit.setPlaceholderText("es. 2.4 GHz")
        self.freq_edit.returnPressed.connect(self._goto)
        form.addWidget(self.freq_edit, 2, 1)
        go = QPushButton("Vai")
        go.clicked.connect(self._goto)
        form.addWidget(go, 2, 2)
        form.setColumnStretch(1, 1)
        lay.addLayout(form)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        f = QFont("Consolas")
        f.setPixelSize(12)
        self.table.setFont(f)
        lay.addWidget(self.table, 1)
        self.table.itemChanged.connect(self._item_edited)
        self.table.itemSelectionChanged.connect(self._row_selected)
        ws.markers.changed.connect(self.refresh)
        ws.changed.connect(self.refresh)

    # ------------------------------------------------------------ dati
    def _resolve(self, m: Marker):
        e = self.ws.entry(m.key[0])
        if e is None:
            return None, None
        return e.net, (m.key[1], m.key[2])

    def refresh(self) -> None:
        self._busy = True
        mm = self.ws.markers
        self.table.setRowCount(len(mm.markers))
        for r, m in enumerate(mm.markers):
            net, ij = self._resolve(m)
            vals = [m.name, "—", fmt_eng(m.freq, "Hz", 9), "—", "—", "—", "—", "—"]
            if net is not None:
                vals[1] = f"{net.name}: {net.label(*ij)}"
                z = interp_complex(net.freq, net.s[:, ij[0], ij[1]], m.freq)
                if z is not None:
                    mag = abs(z)
                    vals[3] = f"{20 * math.log10(max(mag, 1e-30)):.3f}"
                    vals[4] = f"{math.degrees(math.atan2(z.imag, z.real)):.3f}"
                    if ij[0] == ij[1] and mag < 1:
                        vals[5] = f"{(1 + mag) / (1 - mag):.3f}"
                    if ij[0] == ij[1] and abs(1 - z) > 1e-9:
                        zz = net.z0[ij[0]] * (1 + z) / (1 - z)
                        vals[6] = f"{zz.real:.2f} {zz.imag:+.2f}j"
                    ref = mm.get(m.ref)
                    if ref is not None:
                        rn, rij = self._resolve(ref)
                        if rn is not None:
                            zr = interp_complex(rn.freq, rn.s[:, rij[0], rij[1]], ref.freq)
                            if zr is not None:
                                dd = 20 * math.log10(max(abs(z), 1e-30)) - 20 * math.log10(max(abs(zr), 1e-30))
                                dp = math.degrees(np.angle(z / zr)) if zr != 0 else float("nan")
                                vals[7] = (f"{ref.name}: Δf {fmt_eng(m.freq - ref.freq, 'Hz', 6)}, "
                                           f"Δ|S| {dd:+.3f} dB, Δφ {dp:+.2f}°")
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c != 2:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c == 0:
                    t = next((t for t in self.ws.traces() if t.key == m.key), None)
                    if t:
                        it.setForeground(QColor(t.color))
                    if m.id == mm.active:
                        f = it.font()
                        f.setBold(True)
                        it.setFont(f)
                it.setData(Qt.ItemDataRole.UserRole, m.id)
                self.table.setItem(r, c, it)
            if m.id == mm.active:
                self.table.selectRow(r)
        self.table.resizeColumnsToContents()
        self._fill_combos()
        self._busy = False

    def _fill_combos(self) -> None:
        mm = self.ws.markers
        act = mm.active_marker()
        self.trace_combo.blockSignals(True)
        self.trace_combo.clear()
        for t in self.ws.traces():
            self.trace_combo.addItem(f"{t.net.name}: {t.net.label(t.i, t.j)}", t.key)
        if act is not None:
            k = find_data(self.trace_combo, act.key)
            if k >= 0:
                self.trace_combo.setCurrentIndex(k)
        self.trace_combo.blockSignals(False)
        self.ref_combo.blockSignals(True)
        self.ref_combo.clear()
        self.ref_combo.addItem("— nessuno (marker assoluto) —", None)
        for m in mm.markers:
            if act is None or m.id != act.id:
                self.ref_combo.addItem(m.name, m.id)
        if act is not None and act.ref is not None:
            k = self.ref_combo.findData(act.ref)
            if k >= 0:
                self.ref_combo.setCurrentIndex(k)
        self.ref_combo.blockSignals(False)
        if act is not None and not self.freq_edit.hasFocus():
            self.freq_edit.setText(fmt_eng(act.freq, "Hz", 9))

    # ------------------------------------------------------------ azioni
    def add_marker(self) -> None:
        mm = self.ws.markers
        key = (mm.active_marker().key if mm.active_marker() else self.ws.first_trace_key())
        if key is None:
            return
        net = self.ws.network(key[0])
        act = mm.active_marker()
        f = (act.freq + 0.1 * (net.fmax - net.fmin)) if act else 0.5 * (net.fmin + net.fmax)
        f = min(max(f, net.fmin), net.fmax)
        mm.add(f, key)

    def remove_active(self) -> None:
        if self.ws.markers.active is not None:
            self.ws.markers.remove(self.ws.markers.active)

    def _row_selected(self) -> None:
        if self._busy:
            return
        rows = self.table.selectionModel().selectedRows()
        if rows:
            mid = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            self.ws.markers.set_active(mid)

    def _item_edited(self, it: QTableWidgetItem) -> None:
        if self._busy or it.column() != 2:
            return
        mid = it.data(Qt.ItemDataRole.UserRole)
        m = self.ws.markers.get(mid)
        try:
            f = parse_freq(it.text(), 1.0)
        except ValueError:
            self.refresh()
            return
        self._move(m, f)

    def _move(self, m: Optional[Marker], f: float) -> None:
        if m is None:
            return
        net, _ = self._resolve(m)
        if net is not None:
            f = min(max(f, net.fmin), net.fmax)
        self.ws.markers.move(m.id, f)

    def _goto(self) -> None:
        m = self.ws.markers.active_marker()
        if m is None:
            self.add_marker()
            m = self.ws.markers.active_marker()
        try:
            self._move(m, parse_freq(self.freq_edit.text(), 1.0))
        except ValueError:
            pass

    def _set_trace(self, idx: int) -> None:
        m = self.ws.markers.active_marker()
        key = self.trace_combo.itemData(idx)
        if m is not None and key is not None:
            self.ws.markers.move(m.id, m.freq, key)

    def _set_ref(self, idx: int) -> None:
        m = self.ws.markers.active_marker()
        if m is not None:
            self.ws.markers.set_ref(m.id, self.ref_combo.itemData(idx))

    def search(self, mode: str, direction: int) -> None:
        mm = self.ws.markers
        m = mm.active_marker()
        if m is None:
            self.add_marker()
            m = mm.active_marker()
            if m is None:
                return
        net, ij = self._resolve(m)
        if net is None:
            return
        kind = self.get_kind()
        y = trace_values(net, ij[0], ij[1], kind)
        f = net.freq
        if not np.isfinite(y).any():
            return
        if direction == 0:
            idx = int(np.nanargmax(y) if mode == "max" else np.nanargmin(y))
        else:
            cur = int(np.argmin(np.abs(f - m.freq)))
            idx = basic.next_peak(f, y, cur, direction, mode)
            if idx is None:
                return
        mm.move(m.id, float(f[idx]))
