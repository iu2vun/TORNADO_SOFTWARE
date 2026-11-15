"""Pannello Trasforma: ogni operazione crea una NUOVA rete nel workspace (l'originale resta intatta)."""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ...analysis import network_ops as ops
from ...core.network import Network
from ...core.units import parse_freq
from ...io import OPEN_FILTER, load_networks
from ..model import Workspace


class NetCombo(QComboBox):
    """Combo con le reti del workspace (+ voce vuota opzionale)."""

    def __init__(self, ws: Workspace, nports: Optional[int] = None, optional: bool = False):
        super().__init__()
        self.ws, self.nports, self.optional = ws, nports, optional
        ws.changed.connect(self.fill)
        self.fill()

    def fill(self) -> None:
        cur = self.currentData()
        self.blockSignals(True)
        self.clear()
        if self.optional:
            self.addItem("— nessuna —", None)
        for e in self.ws.entries:
            if self.nports is None or e.net.nports == self.nports:
                self.addItem(e.net.name, e.net.uid)
        k = self.findData(cur)
        if k >= 0:
            self.setCurrentIndex(k)
        self.blockSignals(False)

    def net(self) -> Optional[Network]:
        u = self.currentData()
        return self.ws.network(u) if u is not None else None


class TransformPanel(QWidget):
    def __init__(self, ws: Workspace, message: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.ws = ws
        self.message = message
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
        self.cur = QLabel()
        self.cur.setProperty("dim", True)
        lay.addWidget(self.cur)
        ws.current_changed.connect(self._cur)
        ws.changed.connect(self._cur)

        # --- z0
        g = QGroupBox("Rinormalizza impedenza di porta")
        f = QFormLayout(g)
        self.z0 = QLineEdit("50")
        self.z0.setToolTip("Un valore o uno per porta separati da virgola (reale), es. 50,75")
        f.addRow("Z0 [Ω]:", self.z0)
        f.addRow(self._btn("Applica", self.do_renorm))
        lay.addWidget(g)

        # --- port extension
        g = QGroupBox("Estensione piano di riferimento (port extension)")
        f = QFormLayout(g)
        self.pe_mode = QComboBox()
        self.pe_mode.addItems(["Ritardo [ps]", "Ritardo [ns]", "Lunghezza [mm] con VF", "Lunghezza [mm] con εr"])
        self.pe_val = QLineEdit("0")
        self.pe_val.setToolTip("Un valore per tutte le porte, o uno per porta separati da virgola. "
                               "Positivo: rimuove la linea (sposta il piano verso il DUT).")
        self.pe_vf = QDoubleSpinBox()
        self.pe_vf.setRange(0.01, 1.0)
        self.pe_vf.setDecimals(3)
        self.pe_vf.setValue(0.67)
        self.pe_er = QDoubleSpinBox()
        self.pe_er.setRange(1.0, 100.0)
        self.pe_er.setValue(2.1)
        self.pe_loss = QDoubleSpinBox()
        self.pe_loss.setRange(0, 100)
        self.pe_loss.setDecimals(4)
        self.pe_loss.setSuffix(" dB @1GHz")
        f.addRow("Unità:", self.pe_mode)
        f.addRow("Valore/i:", self.pe_val)
        f.addRow("VF:", self.pe_vf)
        f.addRow("εr:", self.pe_er)
        f.addRow("Perdita linea (∝√f):", self.pe_loss)
        f.addRow(self._btn("Applica", self.do_portext))
        lay.addWidget(g)

        # --- de-embedding
        g = QGroupBox("De-embedding fixture (2 porte)")
        f = QFormLayout(g)
        self.de_l, self.de_r = NetCombo(ws, 2, True), NetCombo(ws, 2, True)
        self.de_flip = QCheckBox("Fixture destra misurata dal lato VNA (gira porte)")
        f.addRow("Fixture sinistra:", self.de_l)
        f.addRow("Fixture destra:", self.de_r)
        f.addRow(self.de_flip)
        r = QHBoxLayout()
        r.addWidget(self._btn("Carica fixture…", self.load_fixture))
        r.addWidget(self._btn("De-embed", self.do_deembed, True))
        r.addWidget(self._btn("Embed", self.do_embed))
        f.addRow(r)
        lay.addWidget(g)

        # --- cascade
        g = QGroupBox("Cascata (corrente → seguente)")
        f = QFormLayout(g)
        self.cas_b = NetCombo(ws, 2)
        f.addRow("Seconda rete:", self.cas_b)
        r = QHBoxLayout()
        r.addWidget(self._btn("Cascata A→B", self.do_cascade))
        r.addWidget(self._btn("Inverti 2 porte", self.do_invert))
        r.addWidget(self._btn("Scambia porte", self.do_flip))
        f.addRow(r)
        lay.addWidget(g)

        # --- mixed mode
        g = QGroupBox("Mixed-mode (differenziale / comune)")
        f = QFormLayout(g)
        self.mm = QLineEdit()
        self.mm.setPlaceholderText("es. 1-2, 3-4  (numeri di porta)")
        f.addRow("Coppie:", self.mm)
        f.addRow(self._btn("Converti", self.do_mixed))
        lay.addWidget(g)

        # --- correzioni
        g = QGroupBox("Correzioni fisiche")
        l = QHBoxLayout(g)
        l.addWidget(self._btn("Forza passività", self.do_passive))
        l.addWidget(self._btn("Forza reciprocità", self.do_recip))
        lay.addWidget(g)

        # --- crop / porte / resample
        g = QGroupBox("Banda, porte, ricampionamento")
        f = QFormLayout(g)
        self.cf1, self.cf2 = QLineEdit(), QLineEdit()
        self.cf1.setPlaceholderText("fmin")
        self.cf2.setPlaceholderText("fmax")
        r = QHBoxLayout()
        r.addWidget(self.cf1)
        r.addWidget(self.cf2)
        f.addRow("Ritaglia:", r)
        f.addRow(self._btn("Ritaglia banda", self.do_crop))
        self.sub = QLineEdit()
        self.sub.setPlaceholderText("es. 1,3  (sotto-rete con quelle porte)")
        f.addRow("Porte:", self.sub)
        f.addRow(self._btn("Estrai sotto-rete", self.do_sub))
        self.rs_n = QSpinBox()
        self.rs_n.setRange(2, 2_000_000)
        self.rs_n.setValue(1001)
        f.addRow("N punti:", self.rs_n)
        f.addRow(self._btn("Ricampiona (lineare)", self.do_resample))
        lay.addWidget(g)
        lay.addStretch(1)
        self._cur()

    # ----------------------------------------------------------------- util
    @staticmethod
    def _btn(text: str, fn, accent: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setProperty("accent", accent)
        b.clicked.connect(fn)
        return b

    def _cur(self, *_) -> None:
        e = self.ws.current_entry()
        self.cur.setText("Rete corrente: " + (f"{e.net.name}  ({e.net.nports} porte)" if e else "—"))

    def _net(self, need_ports: Optional[int] = None) -> Optional[Network]:
        e = self.ws.current_entry()
        if e is None:
            self.message("Nessuna rete corrente")
            return None
        if need_ports and e.net.nports != need_ports:
            self.message(f"Operazione valida solo per reti a {need_ports} porte")
            return None
        return e.net

    def _emit(self, make: Callable[[], Network], cells: Optional[set] = None) -> None:
        try:
            n = make()
        except Exception as ex:  # noqa: BLE001
            QMessageBox.warning(self, "Operazione non riuscita", str(ex))
            return
        self.ws.add_network(n, cells=cells)
        self.message(f"Creata rete «{n.name}»")

    @staticmethod
    def _list(text: str) -> list[float]:
        return [float(x) for x in text.replace(";", ",").split(",") if x.strip()]

    # -------------------------------------------------------------- azioni
    def do_renorm(self) -> None:
        net = self._net()
        if net is None:
            return
        try:
            v = self._list(self.z0.text())
            if not v or any(x <= 0 for x in v):
                raise ValueError("Z0 non valido")
            z = v[0] if len(v) == 1 else np.array(v)
            if len(v) not in (1, net.nports):
                raise ValueError(f"servono 1 o {net.nports} valori")
        except ValueError as e:
            QMessageBox.warning(self, "Z0", str(e))
            return
        self._emit(lambda: ops.renormalize(net, z))

    def do_portext(self) -> None:
        net = self._net()
        if net is None:
            return
        try:
            v = np.array(self._list(self.pe_val.text()))
            m = self.pe_mode.currentIndex()
            if m == 0:
                tau = v * 1e-12
            elif m == 1:
                tau = v * 1e-9
            else:
                tau = np.array([ops.delay_from_length(x * 1e-3, self.pe_vf.value(),
                                                      self.pe_er.value() if m == 3 else None) for x in v])
            if tau.size not in (1, net.nports):
                raise ValueError(f"servono 1 o {net.nports} valori")
            tau = float(tau[0]) if tau.size == 1 else tau
        except ValueError as e:
            QMessageBox.warning(self, "Port extension", str(e))
            return
        self._emit(lambda: ops.port_extension(net, tau, self.pe_loss.value()))

    def load_fixture(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Carica fixture", "", OPEN_FILTER)
        for p in paths:
            try:
                for n in load_networks(p):
                    self.ws.add_network(n, select=False)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Caricamento", f"{p}\n{e}")

    def do_deembed(self) -> None:
        net = self._net(2)
        if net is None:
            return
        l, r = self.de_l.net(), self.de_r.net()
        if l is None and r is None:
            self.message("Seleziona almeno una fixture")
            return
        self._emit(lambda: ops.deembed(net, l, r, self.de_flip.isChecked()))

    def do_embed(self) -> None:
        net = self._net(2)
        if net is None:
            return
        self._emit(lambda: ops.embed(net, self.de_l.net(), self.de_r.net()))

    def do_cascade(self) -> None:
        net = self._net(2)
        b = self.cas_b.net()
        if net is None or b is None:
            return
        self._emit(lambda: _named(ops.cascade(net, b), f"{net.name}+{b.name}"))

    def do_invert(self) -> None:
        net = self._net(2)
        if net is not None:
            self._emit(lambda: ops.invert_2port(net))

    def do_flip(self) -> None:
        net = self._net(2)
        if net is not None:
            self._emit(lambda: ops.flip_ports(net))

    def do_mixed(self) -> None:
        net = self._net()
        if net is None:
            return
        try:
            pairs = []
            for tok in self.mm.text().replace(";", ",").split(","):
                if tok.strip():
                    a, b = tok.replace(" ", "").split("-")
                    pairs.append((int(a) - 1, int(b) - 1))
            if not pairs:
                pairs = [(2 * k, 2 * k + 1) for k in range(net.nports // 2)]
            used = [p for pr in pairs for p in pr]
            if sorted(used) != list(range(net.nports)):
                raise ValueError("le coppie devono coprire tutte le porte una sola volta")
        except ValueError as e:
            QMessageBox.warning(self, "Mixed-mode", str(e))
            return
        self._emit(lambda: ops.mixed_mode(net, pairs), cells=None)

    def do_passive(self) -> None:
        net = self._net()
        if net is not None:
            self._emit(lambda: ops.enforce_passivity(net))

    def do_recip(self) -> None:
        net = self._net()
        if net is not None:
            self._emit(lambda: ops.enforce_reciprocity(net))

    def do_crop(self) -> None:
        net = self._net()
        if net is None:
            return
        try:
            lo = parse_freq(self.cf1.text()) if self.cf1.text().strip() else net.fmin
            hi = parse_freq(self.cf2.text()) if self.cf2.text().strip() else net.fmax
        except ValueError:
            QMessageBox.warning(self, "Ritaglia", "Frequenze non valide (es. 1G, 2.5 GHz)")
            return
        self._emit(lambda: _named(net.cropped(lo, hi), net.name + "_crop"))

    def do_sub(self) -> None:
        net = self._net()
        if net is None:
            return
        try:
            idx = [int(x) - 1 for x in self.sub.text().replace(";", ",").split(",") if x.strip()]
            if not idx or any(i < 0 or i >= net.nports for i in idx) or len(set(idx)) != len(idx):
                raise ValueError("indici di porta non validi")
        except ValueError as e:
            QMessageBox.warning(self, "Sotto-rete", str(e))
            return
        self._emit(lambda: _named(net.subnetwork(idx), f"{net.name}_p{''.join(str(i + 1) for i in idx)}"))

    def do_resample(self) -> None:
        net = self._net()
        if net is None:
            return
        n = self.rs_n.value()
        self._emit(lambda: _named(net.interpolated(np.linspace(net.fmin, net.fmax, n)), net.name + f"_{n}pt"))


def _named(n: Network, name: str) -> Network:
    n.name = name
    return n
