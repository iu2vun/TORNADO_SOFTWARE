"""Pannelli Math (operazioni tra tracce) e Info (riepilogo rete)."""
from __future__ import annotations

import html
from typing import Callable

import numpy as np
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QTextBrowser, QVBoxLayout, QWidget)

from ...analysis import network_ops as ops
from ...analysis import trace_math as tm
from ...core.units import fmt_eng
from ..model import Workspace
from . import find_data


class MathPanel(QWidget):
    def __init__(self, ws: Workspace, message: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.ws, self.message = ws, message
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

        g = QGroupBox("Operazione tra due tracce (complesse)")
        f = QFormLayout(g)
        self.a, self.b, self.op = QComboBox(), QComboBox(), QComboBox()
        self.op.addItems(list(tm.BINARY_OPS))
        f.addRow("A:", self.a)
        f.addRow("Operazione:", self.op)
        f.addRow("B:", self.b)
        btn = QPushButton("Calcola → nuova traccia")
        btn.setProperty("accent", True)
        btn.clicked.connect(self.do_binary)
        f.addRow(btn)
        lay.addWidget(g)

        g = QGroupBox("Operazione su una traccia")
        f = QFormLayout(g)
        self.t, self.uop = QComboBox(), QComboBox()
        self.uop.addItems(list(tm.UNARY_OPS))
        self.par = QDoubleSpinBox()
        self.par.setRange(-1e9, 1e9)
        self.par.setDecimals(4)
        self.par.setValue(1.0)
        self.uop.currentTextChanged.connect(self._hint)
        self.hint = QLabel()
        self.hint.setProperty("dim", True)
        f.addRow("Traccia:", self.t)
        f.addRow("Operazione:", self.uop)
        f.addRow("Parametro:", self.par)
        f.addRow(self.hint)
        btn = QPushButton("Applica → nuova traccia")
        btn.setProperty("accent", True)
        btn.clicked.connect(self.do_unary)
        f.addRow(btn)
        lay.addWidget(g)

        g = QGroupBox("Confronto con rete di riferimento")
        f = QFormLayout(g)
        self.cmp_ref = QComboBox()
        f.addRow("Riferimento:", self.cmp_ref)
        n = QLabel("Per ogni traccia selezionata della rete corrente crea Δ ampiezza [dB] e Δ fase [°] "
                   "rispetto allo stesso parametro Sij del riferimento (riferimento interpolato sulla griglia della corrente).")
        n.setWordWrap(True)
        n.setProperty("dim", True)
        f.addRow(n)
        btn = QPushButton("Crea tracce differenza")
        btn.clicked.connect(self.do_compare)
        f.addRow(btn)
        lay.addWidget(g)

        g = QGroupBox("Media di reti")
        l = QVBoxLayout(g)
        l.addWidget(QLabel("Media complessa delle reti visibili con lo stesso numero di porte\n"
                           "della rete corrente (griglia = sovrapposizione di frequenza)."))
        btn = QPushButton("Media reti visibili")
        btn.clicked.connect(self.do_average)
        l.addWidget(btn)
        lay.addWidget(g)
        lay.addStretch(1)
        ws.changed.connect(self._fill)
        self._fill()
        self._hint()

    def _fill(self) -> None:
        for c in (self.a, self.b, self.t):
            cur = c.currentData()
            c.blockSignals(True)
            c.clear()
            for t in self.ws.traces():
                c.addItem(f"{t.net.name}: {t.net.label(t.i, t.j)}", t.key)
            k = find_data(c, cur)
            if k >= 0:
                c.setCurrentIndex(k)
            c.blockSignals(False)
        cur = self.cmp_ref.currentData()
        self.cmp_ref.blockSignals(True)
        self.cmp_ref.clear()
        for e in self.ws.entries:
            self.cmp_ref.addItem(f"{e.net.name} ({e.net.nports}p)", e.net.uid)
        k = self.cmp_ref.findData(cur)
        if k >= 0:
            self.cmp_ref.setCurrentIndex(k)
        self.cmp_ref.blockSignals(False)
        if self.b.count() > 1 and self.b.currentIndex() == self.a.currentIndex():
            self.b.setCurrentIndex(1)

    def _hint(self) -> None:
        h = {"ritardo elettrico": "parametro in ps", "offset di fase": "parametro in gradi",
             "moltiplica (lineare)": "fattore", "moltiplica (dB)": "guadagno in dB",
             "smoothing": "finestra in punti (media mobile)"}
        self.hint.setText(h.get(self.uop.currentText(), "nessun parametro"))

    def _add(self, n) -> None:
        self.ws.add_network(n, cells={(0, 0)}, select=False)
        self.message(f"Creata traccia «{n.name}»")

    def do_binary(self) -> None:
        ka, kb = self.a.currentData(), self.b.currentData()
        if ka is None or kb is None:
            return
        try:
            self._add(tm.binary_op(self.ws.network(ka[0]), ka[1], ka[2], self.ws.network(kb[0]), kb[1], kb[2],
                                   self.op.currentText()))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Math", str(e))

    def do_unary(self) -> None:
        k = self.t.currentData()
        if k is None:
            return
        try:
            self._add(tm.unary_op(self.ws.network(k[0]), k[1], k[2], self.uop.currentText(), self.par.value()))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Math", str(e))

    def do_compare(self) -> None:
        cur = self.ws.current_entry()
        ref = self.ws.network(self.cmp_ref.currentData()) if self.cmp_ref.currentData() is not None else None
        if cur is None or ref is None or ref is cur.net:
            self.message("Seleziona una rete di riferimento diversa dalla corrente")
            return
        net = cur.net
        lo, hi = max(net.fmin, ref.fmin), min(net.fmax, ref.fmax)
        if lo >= hi:
            QMessageBox.warning(self, "Confronto", "Nessuna sovrapposizione di frequenza")
            return
        a = net.cropped(lo, hi)
        r = ref.interpolated(a.freq)
        cells = [(i, j) for (i, j) in sorted(cur.cells) if i < ref.nports and j < ref.nports]
        if not cells:
            self.message("Nessuna traccia selezionata compatibile con il riferimento")
            return
        for (i, j) in cells:
            ratio = a.s[:, i, j] / np.where(r.s[:, i, j] == 0, 1e-30, r.s[:, i, j])
            lab = f"{net.name}−{ref.name} {net.label(i, j)}"
            dmag = 20 * np.log10(np.maximum(np.abs(ratio), 1e-30))
            dph = np.degrees(np.angle(ratio))
            self.ws.add_network(tm.make_trace_network(a.freq, dmag.astype(complex), f"Δ|S| {lab} [dB]",
                                                      name=f"d_dB_{lab}"), cells={(0, 0)}, select=False)
            self.ws.add_network(tm.make_trace_network(a.freq, dph.astype(complex), f"Δφ {lab} [°]",
                                                      name=f"d_ph_{lab}"), cells={(0, 0)}, select=False)
        self.message(f"Create {2 * len(cells)} tracce differenza (grandezza «Valore (tracce derivate)»)")

    def do_average(self) -> None:
        cur = self.ws.current_entry()
        if cur is None:
            return
        nets = [cur.net] + [e.net for e in self.ws.entries if e.visible and e.net is not cur.net
                            and e.net.nports == cur.net.nports]
        try:
            n = tm.average_networks(nets)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Media", str(e))
            return
        self.ws.add_network(n, select=False)
        self.message(f"Media di {len(nets)} reti")


class InfoPanel(QWidget):
    def __init__(self, ws: Workspace, parent=None):
        super().__init__(parent)
        self.ws = ws
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.view = QTextBrowser()
        lay.addWidget(self.view)
        ws.current_changed.connect(lambda *_: self.refresh())
        ws.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        e = self.ws.current_entry()
        if e is None:
            self.view.setHtml("<p style='color:#8a92a0'>Apri un file S-parametri (File → Apri, o trascinalo qui).</p>")
            return
        n = e.net
        row = lambda a, b: f"<tr><td style='color:#8a92a0'>{a}</td><td>&nbsp;&nbsp;{b}</td></tr>"
        rows = [row("Nome", html.escape(n.name)), row("Formato", html.escape(n.source_format or "—")),
                row("File", html.escape(n.path) if n.path else "—"), row("Porte", n.nports),
                row("Punti", n.nfreq), row("Banda", f"{fmt_eng(n.fmin, 'Hz', 6)} – {fmt_eng(n.fmax, 'Hz', 6)}"),
                row("Z0", ", ".join(f"{z:g}" for z in n.z0) + " Ω")]
        if n.nfreq > 1:
            d = np.diff(n.freq)
            rows.append(row("Passo", "uniforme " + fmt_eng(float(d[0]), "Hz", 5) if n.is_uniform()
                            else f"non uniforme ({fmt_eng(float(d.min()), 'Hz', 4)} … {fmt_eng(float(d.max()), 'Hz', 4)})"))
        try:
            p = ops.passivity_report(n)
            r = ops.reciprocity_report(n)
            rows.append(row("σmax(S)", f"{p['max_sv']:.4f} — " + ("passiva" if p["passive"] else
                                                                 "<span style='color:#ff6b6b'>NON passiva</span>")))
            rows.append(row("Reciprocità", "sì" if r["reciprocal"] else f"no (max {r['max_dev']:.2g})"))
        except Exception:  # noqa: BLE001
            pass
        if n.noise is not None:
            rows.append(row("Rumore", "dati presenti"))
        h = "<h3 style='color:#ff8a1f'>%s</h3><table>%s</table>" % (html.escape(n.name), "".join(rows))
        if n.comments:
            h += "<h4 style='color:#ff8a1f'>Commenti</h4><pre style='font-size:11px'>%s</pre>" % html.escape(
                "\n".join(n.comments[:80]))
        self.view.setHtml(h)
