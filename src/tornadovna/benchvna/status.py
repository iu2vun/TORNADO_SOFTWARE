"""Monitor di stato dello strumento: lock PLL, ADC, livello, rif. esterno, temperature, alimentazione.

StatusMonitor  : storico, eventi, riferimento di calibrazione (deriva termica)
StatusCard     : riquadro nel pannello laterale
StatusDialog   : storico temperature + registro eventi + export CSV
"""
from __future__ import annotations

import csv
import time
from collections import deque
from datetime import datetime

import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QListWidget, QPushButton, QVBoxLayout,
)

from tornado_ui import TRACE_COLORS, Card, P, dim, h2
from tornado_ui.plot_style import style_pyqtgraph

FLAGS = (  # chiave, etichetta, valore "buono"
    ("source_locked", "PLL sorgente", True),
    ("lo_locked", "PLL LO", True),
    ("adc_overload", "ADC", False),
    ("unlevel", "Livello potenza", False),
)
TEMPS = (("temp_source", "Temp. sorgente"), ("temp_lo", "Temp. LO"), ("temp_mcu", "Temp. MCU"))
GOOD_TXT = {"source_locked": ("agganciato", "SGANCIATO"), "lo_locked": ("agganciato", "SGANCIATO"),
            "adc_overload": ("ok", "SOVRACCARICO"), "unlevel": ("ok", "NON REGOLATO")}


def _ts(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


class StatusMonitor(QObject):
    updated = Signal(dict)
    event = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history: deque = deque(maxlen=20000)
        self.events: deque = deque(maxlen=500)
        self.last: dict | None = None
        self.cal_ref: dict | None = None      # temperature al momento della calibrazione
        self.drift_limit = 3.0                # [gradi C] oltre cui la cal e' sospetta
        self._drift_warned = False

    def push(self, d: dict):
        prev = self.last
        self.last = d
        if prev is None and d.get("raw_hex"):
            self._ev(d["t"], f"Primo stato ricevuto: hw {d.get('hw_version')}  payload {d['raw_hex']}")
        self.history.append(d)
        if prev is not None:
            for key, label, good in FLAGS:
                a, b = prev.get(key), d.get(key)
                if a is not None and b is not None and a != b:
                    bad = (b != good)
                    self._ev(d["t"], f"{label}: {GOOD_TXT[key][1] if bad else GOOD_TXT[key][0]}")
            a, b = prev.get("ext_ref_use"), d.get("ext_ref_use")
            if a is not None and b is not None and a != b:
                self._ev(d["t"], "Riferimento: " + ("ESTERNO" if b else "interno"))
        dr = self.drift()
        if dr is not None:
            over = max(abs(v) for v in dr.values()) > self.drift_limit
            if over and not self._drift_warned:
                self._ev(d["t"], f"Deriva termica > {self.drift_limit:g} °C dalla calibrazione: rifare la cal")
            self._drift_warned = over
        self.updated.emit(d)

    def _ev(self, t, text):
        s = f"{_ts(t)}  {text}"
        self.events.append(s)
        self.event.emit(s)

    def set_cal_reference(self):
        if self.last is None:
            return False
        self.cal_ref = {k: self.last.get(k) for k, _ in TEMPS}
        self._drift_warned = False
        self._ev(self.last["t"], "Riferimento termico calibrazione impostato")
        return True

    def drift(self):
        """{chiave: delta gradi C rispetto alla calibrazione} o None."""
        if not self.cal_ref or not self.last:
            return None
        out = {}
        for k, _ in TEMPS:
            a, b = self.cal_ref.get(k), self.last.get(k)
            if a is not None and b is not None:
                out[k] = b - a
        return out or None

    def export_csv(self, path):
        cols = ["t", "source_locked", "lo_locked", "adc_overload", "unlevel", "ext_ref_avail", "ext_ref_use",
                "fpga_ok", "temp_source", "temp_lo", "temp_mcu", "supply_v", "supply_i"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["ora"] + cols[1:])
            for d in self.history:
                w.writerow([datetime.fromtimestamp(d["t"]).isoformat(timespec="seconds")]
                           + [("" if d.get(c) is None else d.get(c)) for c in cols[1:]])


def _led(ok, text):
    col = P.ok if ok else P.danger
    return f"<span style='color:{col};'>●</span> {text}"


class StatusCard(Card):
    modeChanged = Signal(str)

    def __init__(self, mon: StatusMonitor):
        super().__init__()
        self._mon = mon
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        head.addWidget(h2("Stato strumento"))
        head.addStretch(1)
        self.cb = QComboBox()
        for txt, key in (("Periodico", "periodic"), ("Continuo", "continuous"), ("Spento", "off")):
            self.cb.addItem(txt, key)
        self.cb.setToolTip("Periodico: legge lo stato ogni 3 s (sweep quasi invariato).\n"
                           "Continuo: a ogni sweep (puo' rallentare lo sweep).")
        self.cb.currentIndexChanged.connect(lambda i: self.modeChanged.emit(self.cb.itemData(i)))
        head.addWidget(self.cb)
        v.addLayout(head)
        f = QFormLayout()
        self.rows = {}
        for key, label, _ in FLAGS:
            self.rows[key] = self._row(f, label)
        self.rows["ext_ref"] = self._row(f, "Rif. esterno")
        for key, label in TEMPS:
            self.rows[key] = self._row(f, label)
        self.rows["supply"] = self._row(f, "Alimentazione")
        v.addLayout(f)
        self.lbl_cal = QLabel("")
        self.lbl_cal.setTextFormat(Qt.RichText)
        self.lbl_cal.setWordWrap(True)
        v.addWidget(self.lbl_cal)
        self.lbl_age = dim("In attesa di dati…")
        v.addWidget(self.lbl_age)
        mon.updated.connect(self.update_status)
        self.show_status(None)

    def _row(self, form, label):
        l = QLabel("—")
        l.setTextFormat(Qt.RichText)
        form.addRow(label, l)
        return l

    def update_status(self, d):
        self.show_status(d)

    def show_status(self, d):
        if not d:
            return
        for key, _, good in FLAGS:
            val = d.get(key)
            self.rows[key].setText("—" if val is None else _led(val == good, GOOD_TXT[key][0 if val == good else 1]))
        if d.get("ext_ref_avail") is None:
            self.rows["ext_ref"].setText("—")
        else:
            use = "in uso" if d.get("ext_ref_use") else ("disponibile" if d.get("ext_ref_avail") else "assente")
            self.rows["ext_ref"].setText(f"<span style='color:{P.text_dim};'>●</span> {use}")
        dr = self._mon.drift() or {}
        for key, _ in TEMPS:
            t = d.get(key)
            if t is None:
                self.rows[key].setText("—")
                continue
            txt = f"{t:.0f} °C"
            if key in dr:
                col = P.danger if abs(dr[key]) > self._mon.drift_limit else P.text_dim
                txt += f"  <span style='color:{col};'>Δ{dr[key]:+.0f}</span>"
            self.rows[key].setText(txt)
        sv, si = d.get("supply_v"), d.get("supply_i")
        self.rows["supply"].setText("non riportata da questo hardware" if sv is None
                                    else f"{sv:.2f} V   {si:.2f} A" if si is not None else f"{sv:.2f} V")
        if dr:
            worst = max(abs(x) for x in dr.values())
            if worst > self._mon.drift_limit:
                self.lbl_cal.setText(f"<span style='color:{P.danger};'>Deriva {worst:.0f} °C dalla calibrazione: "
                                     f"rifare la cal.</span>")
            else:
                self.lbl_cal.setText(f"<span style='color:{P.text_dim};'>Cal valida: deriva max {worst:.0f} °C</span>")
        else:
            self.lbl_cal.setText(f"<span style='color:{P.text_dim};'>Nessun riferimento termico di calibrazione</span>")
        self.lbl_age.setText(f"Ultimo stato: {_ts(d['t'])}")


class StatusDialog(QDialog):
    """Storico temperature + registro eventi (finestra non modale)."""

    def __init__(self, mon: StatusMonitor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stato strumento — storico")
        self.resize(900, 600)
        self.setModal(False)
        self._mon = mon
        v = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Finestra"))
        self.cb_win = QComboBox()
        for m in (5, 15, 60, 240):
            self.cb_win.addItem(f"{m} min", m)
        self.cb_win.setCurrentIndex(1)
        self.cb_win.currentIndexChanged.connect(self._redraw)
        bar.addWidget(self.cb_win)
        bar.addWidget(QLabel("Soglia deriva cal"))
        self.sp = QDoubleSpinBox()
        self.sp.setRange(0.5, 30)
        self.sp.setSuffix(" °C")
        self.sp.setValue(mon.drift_limit)
        self.sp.valueChanged.connect(lambda x: setattr(mon, "drift_limit", x))
        bar.addWidget(self.sp)
        bar.addStretch(1)
        b = QPushButton("Riferimento cal = ora")
        b.clicked.connect(lambda: mon.set_cal_reference())
        bar.addWidget(b)
        b = QPushButton("Esporta CSV…")
        b.clicked.connect(self._export)
        bar.addWidget(b)
        v.addLayout(bar)

        self.plot = pg.PlotWidget()
        style_pyqtgraph(self.plot)
        self.plot.setLabel("left", "Temperatura", units="°C")
        self.plot.setLabel("bottom", "Tempo", units="min")
        self.plot.addLegend(offset=(10, 10))
        self.curves = {}
        for (k, label), col in zip(TEMPS, TRACE_COLORS):
            self.curves[k] = self.plot.plot([], [], pen=pg.mkPen(col, width=2), name=label)
        v.addWidget(self.plot, 1)
        v.addWidget(h2("Eventi"))
        self.log = QListWidget()
        self.log.setMaximumHeight(160)
        for e in mon.events:
            self.log.addItem(e)
        v.addWidget(self.log)
        mon.updated.connect(lambda _d: self._redraw() if self.isVisible() else None)
        mon.event.connect(lambda s: (self.log.addItem(s), self.log.scrollToBottom()))
        self._redraw()

    def _redraw(self):
        win = self.cb_win.currentData() * 60.0
        now = time.time()
        h = [d for d in self._mon.history if now - d["t"] <= win]
        x = [(d["t"] - now) / 60.0 for d in h]
        for k, _ in TEMPS:
            ys = [d.get(k) for d in h]
            pts = [(a, b) for a, b in zip(x, ys) if b is not None]
            self.curves[k].setData([p[0] for p in pts], [p[1] for p in pts])

    def _export(self):
        p, _ = QFileDialog.getSaveFileName(self, "Esporta stato", "stato_vna.csv", "CSV (*.csv)")
        if p:
            self._mon.export_csv(p)
