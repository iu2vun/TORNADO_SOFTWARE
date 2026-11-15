"""Pannello Dominio del tempo: parametri trasformata, distanza (TDR) e time gating."""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...analysis import timedomain as td
from ...core.units import fmt_eng, parse_freq
from ..model import Workspace
from ..plot.timeplot import TimeSettings


def _parse_time(text: str, use_dist: bool, vf: float) -> Optional[float]:
    """'5ns', '1.2 m' (se distanza), numero nudo = unità base. Ritorna secondi."""
    t = text.strip().lower().replace(" ", "")
    if not t:
        return None
    mult = {"ps": 1e-12, "ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0}
    for suf in ("ps", "ns", "us", "µs", "ms"):
        if t.endswith(suf):
            return float(t[:-len(suf)]) * mult[suf]
    if t.endswith("mm"):
        return float(td.distance_to_time(float(t[:-2]) * 1e-3, vf))
    if t.endswith("m"):
        return float(td.distance_to_time(float(t[:-1]), vf))
    if t.endswith("s"):
        return float(t[:-1])
    v = float(t)
    return float(td.distance_to_time(v, vf)) if use_dist else v


class TimePanel(QWidget):
    def __init__(self, ws: Workspace, ts: TimeSettings, open_plot: Callable[[], None],
                 message: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.ws, self.ts, self.message = ws, ts, message
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(sc)
        body = QWidget()
        sc.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)

        b = QPushButton("Mostra grafico dominio del tempo")
        b.setProperty("accent", True)
        b.clicked.connect(open_plot)
        lay.addWidget(b)

        g = QGroupBox("Trasformata")
        f = QFormLayout(g)
        self.mode = QComboBox()
        self.mode.addItem("Lowpass (reale, estrapola a DC)", "lowpass")
        self.mode.addItem("Bandpass (inviluppo)", "bandpass")
        self.resp = QComboBox()
        self.resp.addItem("Impulso", "impulse")
        self.resp.addItem("Gradino", "step")
        self.win = QComboBox()
        self.win.addItems(td.WINDOWS)
        self.beta = QDoubleSpinBox()
        self.beta.setRange(0, 30)
        self.beta.setDecimals(2)
        self.pad = QSpinBox()
        self.pad.setRange(1, 64)
        self.pad.setToolTip("Fattore di zero-padding (interpolazione nel tempo)")
        self.dc = QComboBox()
        for lab, v in (("Auto (estrapola)", "auto"), ("Zero", "zero"), ("Aperto (S=+1)", "open"),
                       ("Corto (S=−1)", "short")):
            self.dc.addItem(lab, v)
        self.db = QCheckBox("Ampiezza in dB")
        for lab, w in (("Modo:", self.mode), ("Risposta:", self.resp), ("Finestra:", self.win),
                       ("β Kaiser:", self.beta), ("Zero-padding:", self.pad), ("Valore DC:", self.dc)):
            f.addRow(lab, w)
        f.addRow(self.db)
        lay.addWidget(g)

        g = QGroupBox("Asse e visualizzazione")
        f = QFormLayout(g)
        self.dist = QCheckBox("Asse in distanza (andata/ritorno)")
        self.vf = QDoubleSpinBox()
        self.vf.setRange(0.01, 1.0)
        self.vf.setDecimals(3)
        self.tmin, self.tmax = QLineEdit(), QLineEdit()
        self.tmin.setPlaceholderText("auto  (es. -1ns, 0 m)")
        self.tmax.setPlaceholderText("auto  (es. 20ns, 3 m)")
        f.addRow(self.dist)
        f.addRow("Fattore velocità:", self.vf)
        f.addRow("Inizio:", self.tmin)
        f.addRow("Fine:", self.tmax)
        lay.addWidget(g)

        g = QGroupBox("Time gating")
        f = QFormLayout(g)
        self.gshow = QCheckBox("Mostra gate sul grafico (trascina i bordi)")
        self.gstart, self.gstop = QLineEdit(), QLineEdit()
        self.gshape = QComboBox()
        self.gshape.addItems(list(td.GATE_SHAPES))
        self.gnotch = QCheckBox("Notch (elimina l'interno del gate)")
        f.addRow(self.gshow)
        f.addRow("Inizio:", self.gstart)
        f.addRow("Fine:", self.gstop)
        f.addRow("Forma bordi:", self.gshape)
        f.addRow(self.gnotch)
        r = QHBoxLayout()
        b1 = QPushButton("Applica alla rete corrente")
        b1.setProperty("accent", True)
        b1.clicked.connect(lambda: self.apply_gate(False))
        b2 = QPushButton("A tutte le visibili")
        b2.clicked.connect(lambda: self.apply_gate(True))
        r.addWidget(b1)
        r.addWidget(b2)
        f.addRow(r)
        note = QLabel("Applica il gate alle tracce selezionate (celle attive) e crea una nuova rete «_gated».")
        note.setWordWrap(True)
        note.setProperty("dim", True)
        f.addRow(note)
        lay.addWidget(g)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setProperty("dim", True)
        lay.addWidget(self.info)
        lay.addStretch(1)

        self._load()
        for w in (self.mode, self.resp, self.win, self.dc, self.gshape):
            w.currentIndexChanged.connect(self._push)
        for w in (self.beta, self.pad, self.vf):
            w.valueChanged.connect(self._push)
        for w in (self.db, self.dist, self.gshow, self.gnotch):
            w.toggled.connect(self._push)
        for w in (self.tmin, self.tmax, self.gstart, self.gstop):
            w.editingFinished.connect(self._push)
        ts.gateMoved.connect(self._gate_moved)
        ws.changed.connect(self._update_info)
        self._update_info()

    # ------------------------------------------------------------------ sync
    def _fmt_t(self, t: Optional[float]) -> str:
        if t is None:
            return ""
        if self.ts.use_distance:
            return fmt_eng(float(td.time_to_distance(t, self.ts.vf)), "m", 5)
        return fmt_eng(t, "s", 5)

    def _load(self) -> None:
        ts = self.ts
        self._busy = True
        self.mode.setCurrentIndex(self.mode.findData(ts.mode))
        self.resp.setCurrentIndex(self.resp.findData(ts.response))
        self.win.setCurrentText(ts.window)
        self.beta.setValue(ts.beta)
        self.pad.setValue(int(ts.pad))
        k = self.dc.findData(ts.dc)
        self.dc.setCurrentIndex(max(k, 0))
        self.db.setChecked(ts.db)
        self.dist.setChecked(ts.use_distance)
        self.vf.setValue(ts.vf)
        self.tmin.setText(self._fmt_t(ts.tmin))
        self.tmax.setText(self._fmt_t(ts.tmax))
        self.gshow.setChecked(ts.gate_show)
        self.gstart.setText(self._fmt_t(ts.gate_start))
        self.gstop.setText(self._fmt_t(ts.gate_stop))
        self.gshape.setCurrentText(ts.gate_shape if isinstance(ts.gate_shape, str) else "normale")
        self.gnotch.setChecked(ts.gate_notch)
        self._busy = False

    def reload(self) -> None:
        self._load()
        self._update_info()

    def _push(self, *_) -> None:
        if self._busy:
            return
        ts = self.ts
        old_view = (ts.use_distance, ts.tmin, ts.tmax, ts.vf)
        ts.mode = self.mode.currentData()
        ts.response = self.resp.currentData()
        ts.window = self.win.currentText()
        ts.beta = self.beta.value()
        ts.pad = self.pad.value()
        ts.dc = self.dc.currentData()
        ts.db = self.db.isChecked()
        ts.use_distance = self.dist.isChecked()
        ts.vf = self.vf.value()
        try:
            ts.tmin = _parse_time(self.tmin.text(), ts.use_distance, ts.vf)
            ts.tmax = _parse_time(self.tmax.text(), ts.use_distance, ts.vf)
            g0 = _parse_time(self.gstart.text(), ts.use_distance, ts.vf)
            g1 = _parse_time(self.gstop.text(), ts.use_distance, ts.vf)
            if g0 is not None:
                ts.gate_start = g0
            if g1 is not None:
                ts.gate_stop = g1
        except ValueError:
            self.message("Valore di tempo/distanza non valido (es. 5ns, 1.2 m)")
        ts.gate_show = self.gshow.isChecked()
        ts.gate_shape = self.gshape.currentText()
        ts.gate_notch = self.gnotch.isChecked()
        if (ts.use_distance, ts.tmin, ts.tmax, ts.vf) != old_view:
            ts.reset_view = True
        if old_view[0] != ts.use_distance:
            self._busy = True
            self.tmin.setText(self._fmt_t(ts.tmin))
            self.tmax.setText(self._fmt_t(ts.tmax))
            self.gstart.setText(self._fmt_t(ts.gate_start))
            self.gstop.setText(self._fmt_t(ts.gate_stop))
            self._busy = False
        self.beta.setEnabled(self.win.currentText() == "kaiser")
        self.resp.setEnabled(self.mode.currentData() == "lowpass")
        self.dc.setEnabled(self.mode.currentData() == "lowpass")
        ts.changed.emit()
        self._update_info()

    def _gate_moved(self) -> None:
        self._busy = True
        self.gstart.setText(self._fmt_t(self.ts.gate_start))
        self.gstop.setText(self._fmt_t(self.ts.gate_stop))
        self._busy = False

    def _update_info(self) -> None:
        e = self.ws.current_entry()
        if e is None:
            self.info.setText("")
            return
        f = e.net.freq
        bw = f[-1] - f[0]
        if bw <= 0:
            self.info.setText("")
            return
        res = 1.0 / bw
        vf = self.ts.vf
        txt = [f"Banda {fmt_eng(f[0], 'Hz', 4)} – {fmt_eng(f[-1], 'Hz', 4)}",
               f"Risoluzione ≈ {fmt_eng(res, 's', 3)}  →  {fmt_eng(float(td.time_to_distance(res, vf)), 'm', 3)} (andata/ritorno)"]
        if len(f) > 1:
            df = float(np.median(np.diff(f)))
            txt.append(f"Range non ambiguo ≈ {fmt_eng(1 / df, 's', 3)}  →  "
                       f"{fmt_eng(float(td.time_to_distance(1 / df, vf)), 'm', 3)}")
        if f[0] > 0.01 * bw and self.ts.mode == "lowpass":
            txt.append("Nota: la banda non parte da DC, il lowpass estrapola; per banda stretta preferire Bandpass.")
        self.info.setText("\n".join(txt))

    # ----------------------------------------------------------------- gate
    def apply_gate(self, all_nets: bool) -> None:
        ts = self.ts
        if ts.gate_stop <= ts.gate_start:
            QMessageBox.warning(self, "Gate", "Il gate ha durata nulla o negativa")
            return
        entries = [e for e in self.ws.entries if e.visible] if all_nets else [self.ws.current_entry()]
        done = 0
        for e in entries:
            if e is None or not e.cells:
                continue
            try:
                n = td.gate_network(e.net, sorted(e.cells), ts.gate_start, ts.gate_stop, ts.gate_shape,
                                    ts.gate_notch, int(ts.pad))
            except Exception as ex:  # noqa: BLE001
                QMessageBox.warning(self, "Gate", f"{e.net.name}: {ex}")
                continue
            n.comments = list(n.comments) + [
                f"time gate {fmt_eng(ts.gate_start, 's', 4)} .. {fmt_eng(ts.gate_stop, 's', 4)} "
                f"({ts.gate_shape}{', notch' if ts.gate_notch else ''})"]
            self.ws.add_network(n, cells=set(e.cells), select=False)
            done += 1
        self.message(f"Gate applicato a {done} rete/i" if done else "Nessuna rete con tracce selezionate")
