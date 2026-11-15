"""Pannello Analisi: larghezza di banda/Q, soglie, statistiche, stabilità, passività."""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget)

from ...analysis import basic, network_ops as ops
from ...analysis.trace_math import make_trace_network
from ...core.traces import KINDS, trace_values
from ...core.units import fmt_eng, parse_freq
from ..model import Workspace
from . import find_data

BW_KINDS = ["db", "rl", "il", "vswr", "mag", "gd", "phase", "val"]
BW_MODES = ["Banda a −X dB dal massimo", "Banda a +X dB dal minimo", "Dove valore ≥ soglia", "Dove valore ≤ soglia"]


def _hz(v: Optional[float]) -> str:
    return "—" if v is None else fmt_eng(v, "Hz", 7)


class AnalysisPanel(QWidget):
    def __init__(self, ws: Workspace, set_bands: Callable[[list], None],
                 show_kind: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.ws = ws
        self.set_bands = set_bands
        self.show_kind = show_kind
        self._last_bands: list[tuple[float, float]] = []
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

        g = QGroupBox("Traccia e intervallo")
        f = QFormLayout(g)
        self.trace = QComboBox()
        self.kind = QComboBox()
        for k in BW_KINDS:
            self.kind.addItem(KINDS[k][0], k)
        self.f1, self.f2 = QLineEdit(), QLineEdit()
        self.f1.setPlaceholderText("auto")
        self.f2.setPlaceholderText("auto")
        f.addRow("Traccia:", self.trace)
        f.addRow("Grandezza:", self.kind)
        r = QHBoxLayout()
        r.addWidget(self.f1)
        r.addWidget(QLabel("–"))
        r.addWidget(self.f2)
        f.addRow("Intervallo:", r)
        lay.addWidget(g)

        g2 = QGroupBox("Larghezza di banda / soglie")
        f2 = QFormLayout(g2)
        self.mode = QComboBox()
        self.mode.addItems(BW_MODES)
        self.level = QDoubleSpinBox()
        self.level.setRange(-1000, 1000)
        self.level.setDecimals(3)
        self.level.setValue(3.0)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        f2.addRow("Misura:", self.mode)
        f2.addRow("X / soglia:", self.level)
        row = QHBoxLayout()
        b = QPushButton("Calcola")
        b.setProperty("accent", True)
        b.clicked.connect(self.compute_bw)
        row.addWidget(b)
        self.b_mark = QPushButton("Marker sui bordi")
        self.b_mark.clicked.connect(self.mark_edges)
        row.addWidget(self.b_mark)
        b3 = QPushButton("Cancella evidenz.")
        b3.clicked.connect(self.clear_bands)
        row.addWidget(b3)
        f2.addRow(row)
        lay.addWidget(g2)

        g3 = QGroupBox("Statistiche nell'intervallo")
        l3 = QHBoxLayout(g3)
        b = QPushButton("Min / Max / Ripple / Media")
        b.clicked.connect(self.compute_stats)
        l3.addWidget(b)
        lay.addWidget(g3)

        g4 = QGroupBox("Verifiche sulla rete corrente")
        l4 = QVBoxLayout(g4)
        row = QHBoxLayout()
        b = QPushButton("Passività / reciprocità")
        b.clicked.connect(self.check_passivity)
        row.addWidget(b)
        b = QPushButton("Stabilità (2 porte)")
        b.clicked.connect(self.check_stability)
        row.addWidget(b)
        l4.addLayout(row)
        b = QPushButton("Aggiungi tracce K, μ, MAG/MSG")
        b.clicked.connect(self.add_stability_traces)
        l4.addWidget(b)
        lay.addWidget(g4)

        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(190)
        self.out.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        lay.addWidget(self.out, 1)
        ws.changed.connect(self._fill_traces)
        self._fill_traces()
        self._mode_changed()

    # ------------------------------------------------------------- util
    def _fill_traces(self) -> None:
        cur = self.trace.currentData()
        self.trace.blockSignals(True)
        self.trace.clear()
        for t in self.ws.traces():
            self.trace.addItem(f"{t.net.name}: {t.net.label(t.i, t.j)}", t.key)
        k = find_data(self.trace, cur)
        if k >= 0:
            self.trace.setCurrentIndex(k)
        self.trace.blockSignals(False)

    def _mode_changed(self) -> None:
        i = self.mode.currentIndex()
        if i in (0, 1):
            if self.level.value() < 0:
                self.level.setValue(3.0)
        else:
            if self.level.value() == 3.0:
                self.level.setValue(-10.0)

    def _selected(self):
        key = self.trace.currentData()
        if key is None:
            self.out.setPlainText("Nessuna traccia selezionata.")
            return None
        e = self.ws.entry(key[0])
        if e is None:
            return None
        kind = self.kind.currentData()
        f, y = e.net.freq, trace_values(e.net, key[1], key[2], kind)
        lo = hi = None
        try:
            if self.f1.text().strip():
                lo = parse_freq(self.f1.text())
            if self.f2.text().strip():
                hi = parse_freq(self.f2.text())
        except ValueError:
            self.out.setPlainText("Intervallo di frequenza non valido (usa es. 800M, 2.4 GHz).")
            return None
        m = np.ones(f.size, bool)
        if lo is not None:
            m &= f >= lo
        if hi is not None:
            m &= f <= hi
        if m.sum() < 3:
            self.out.setPlainText("Intervallo troppo ristretto.")
            return None
        return e.net, key, kind, f[m], y[m]

    def _html(self, title: str, rows: list[tuple[str, str]]) -> None:
        h = f"<b style='color:#ff8a1f'>{title}</b><br>"
        h += "<table cellspacing=2>" + "".join(
            f"<tr><td style='color:#8a92a0'>{a}</td><td>&nbsp;{b}</td></tr>" for a, b in rows) + "</table>"
        self.out.setHtml(h)

    # --------------------------------------------------------- banda
    def compute_bw(self) -> None:
        s = self._selected()
        if s is None:
            return
        net, key, kind, f, y = s
        mi = self.mode.currentIndex()
        x = self.level.value()
        unit = KINDS[kind][1]
        lab = f"{net.name}: {net.label(key[1], key[2])}"
        self._last_key = key
        if mi in (0, 1):
            info = basic.bandwidth_around(f, y, drop=abs(x), mode="max" if mi == 0 else "min")
            if info is None:
                self.out.setPlainText("Dati non validi.")
                return
            self._last_bands = [(info.f_low, info.f_high)] if info.f_low and info.f_high else []
            self._last_edges = [v for v in (info.f_low, info.f_high) if v is not None]
            rows = [("Picco" if mi == 0 else "Minimo", f"{info.y_peak:.4g} {unit} a {_hz(info.f_peak)}"),
                    ("Livello soglia", f"{info.level:.4g} {unit}"),
                    ("f bassa", _hz(info.f_low)), ("f alta", _hz(info.f_high)),
                    ("Larghezza banda", _hz(info.bandwidth)), ("Frequenza centrale", _hz(info.center)),
                    ("Q (f picco / BW)", "—" if info.q is None else f"{info.q:.4g}"),
                    ("Q (f centro / BW)", "—" if not info.bandwidth else f"{info.center / info.bandwidth:.4g}")]
            if info.f_low is None or info.f_high is None:
                rows.append(("Nota", "il livello non viene attraversato da un lato: banda aperta"))
            self._html(f"BW — {lab}", rows)
        else:
            bands = basic.bands_where(f, y, lo=x) if mi == 2 else basic.bands_where(f, y, hi=x)
            self._last_bands = bands
            self._last_edges = [v for b in bands for v in b]
            cond = "≥" if mi == 2 else "≤"
            rows = [("Condizione", f"valore {cond} {x:g} {unit}"), ("Intervalli", str(len(bands)))]
            tot = 0.0
            for k, (a, b) in enumerate(bands[:30]):
                rows.append((f"#{k + 1}", f"{_hz(a)} → {_hz(b)}   (BW {_hz(b - a)})"))
                tot += b - a
            rows.append(("BW totale", _hz(tot)))
            span = f[-1] - f[0]
            if span > 0:
                rows.append(("Copertura", f"{100 * tot / span:.1f} % dell'intervallo"))
            self._html(f"Soglia — {lab}", rows)
        self.set_bands([(a, b, "") for a, b in self._last_bands])

    def mark_edges(self) -> None:
        if not getattr(self, "_last_edges", None) or getattr(self, "_last_key", None) is None:
            self.compute_bw()
        for fe in getattr(self, "_last_edges", [])[:12]:
            self.ws.markers.add(float(fe), self._last_key)

    def clear_bands(self) -> None:
        self._last_bands = []
        self.set_bands([])

    def compute_stats(self) -> None:
        s = self._selected()
        if s is None:
            return
        net, key, kind, f, y = s
        st = basic.range_stats(f, y)
        if not st:
            self.out.setPlainText("Nessun dato valido.")
            return
        u = KINDS[kind][1]
        self._html(f"Statistiche — {net.name}: {net.label(key[1], key[2])}", [
            ("Intervallo", f"{_hz(st['f1'])} → {_hz(st['f2'])}  ({st['n']} punti)"),
            ("Minimo", f"{st['min']:.5g} {u} a {_hz(st['f_min'])}"),
            ("Massimo", f"{st['max']:.5g} {u} a {_hz(st['f_max'])}"),
            ("Ripple (max−min)", f"{st['ripple']:.5g} {u}"),
            ("Media", f"{st['mean']:.5g} {u}"), ("Deviazione std", f"{st['std']:.5g} {u}")])

    # --------------------------------------------------------- rete
    def _current_net(self):
        e = self.ws.current_entry()
        if e is None:
            self.out.setPlainText("Nessuna rete corrente.")
            return None
        return e.net

    def check_passivity(self) -> None:
        net = self._current_net()
        if net is None:
            return
        p = ops.passivity_report(net)
        r = ops.reciprocity_report(net)
        l = ops.lossless_report(net)
        rows = [("σmax(S)", f"{p['max_sv']:.5f} a {_hz(p['f_worst'])}"),
                ("Passiva (σ ≤ 1+1e-3)", "SÌ" if p["passive"] else f"NO — {p['n_violations']} punti"),
                ("Reciproca (|Sij−Sji| ≤ 1e-3)", "SÌ" if r["reciprocal"] else f"NO — max {r['max_dev']:.3g} a {_hz(r['f_worst'])}"),
                ("Senza perdite", "SÌ" if l["lossless"] else f"NO (max dev. {l['max_dev']:.3g})")]
        if not p["passive"]:
            rows.append(("Violazioni", f"{_hz(p['f_viol_first'])} → {_hz(p['f_viol_last'])}"))
        self._html(f"Verifiche — {net.name}", rows)

    def check_stability(self) -> None:
        net = self._current_net()
        if net is None:
            return
        if net.nports != 2:
            self.out.setPlainText("La stabilità è definita per reti a 2 porte.")
            return
        st = ops.stability_2port(net)
        k, mu = st["k"], st["mu"]
        unst = basic.bands_where(net.freq, (~st["stable"]).astype(float), lo=0.5)
        rows = [("K min / max", f"{np.nanmin(k):.4g} / {np.nanmax(k):.4g}"),
                ("|Δ| max", f"{np.nanmax(st['delta']):.4g}"), ("μ min", f"{np.nanmin(mu):.4g}"),
                ("μ' min", f"{np.nanmin(st['mu_p']):.4g}"),
                ("Incondizionatamente stabile", "SÌ su tutta la banda" if st["stable"].all()
                 else f"NO — {int((~st['stable']).sum())} punti")]
        for a, b in unst[:12]:
            rows.append(("Potenzialm. instabile", f"{_hz(a)} → {_hz(b)}"))
        if np.isfinite(st["gmax_db"]).any():
            i = int(np.nanargmax(st["gmax_db"]))
            rows.append(("Gmax picco", f"{st['gmax_db'][i]:.3f} dB a {_hz(net.freq[i])}"))
        self._html(f"Stabilità — {net.name}", rows)

    def add_stability_traces(self) -> None:
        net = self._current_net()
        if net is None or net.nports != 2:
            self.out.setPlainText("Seleziona una rete a 2 porte.")
            return
        st = ops.stability_2port(net)
        for key, lab in (("k", "K (Rollett)"), ("mu", "μ"), ("mu_p", "μ'"), ("msg_db", "MSG [dB]"),
                         ("mag_db", "MAG [dB]")):
            v = np.where(np.isfinite(st[key]), st[key], np.nan)
            n = make_trace_network(net.freq, v.astype(complex), f"{net.name}: {lab}", name=f"{net.name}_{key}",
                                   z0=net.z0[0], comments=[f"{lab} calcolato da {net.name}"])
            self.ws.add_network(n, cells={(0, 0)}, select=False)
        self.show_kind("val")
        self.out.setPlainText("Aggiunte le tracce derivate (K, μ, μ', MSG, MAG).\nVisualizzale con la grandezza "
                              "«Valore (tracce derivate)» (impostata sul grafico attivo).")
