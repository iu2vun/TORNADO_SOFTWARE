"""Maschera di return loss per misure d'antenna.

La maschera e' definita da tre punti di banda (inizio, centro, fine) con il
return loss MINIMO richiesto in ciascuno. Tra i punti il limite e' lineare in
dB; fuori da [inizio, fine] non c'e' limite. La traccia e' conforme se in ogni
punto della banda  RL_misurato >= RL_minimo  (cioe' S11[dB] <= -RL_minimo).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

# formati nei quali la maschera si puo' disegnare
MASK_FORMATS = ("dB Mag", "Log Mag", "SWR", "Lin Mag")


@dataclass
class Mask:
    f1: float = 1.57e9        # inizio banda [Hz]
    f2: float = 1.58e9        # centro banda [Hz]
    f3: float = 1.59e9        # fine banda [Hz]
    rl1: float = 10.0         # return loss minimo all'inizio [dB]
    rl2: float = 15.0         # ... al centro
    rl3: float = 10.0         # ... alla fine
    enabled: bool = True

    # ---- limite --------------------------------------------------------
    def limit_rl(self, f: np.ndarray) -> np.ndarray:
        """RL minimo richiesto [dB] per ogni frequenza (NaN fuori banda)."""
        f = np.asarray(f, dtype=float)
        xs = [self.f1, self.f2, self.f3]
        out = np.interp(f, xs, [self.rl1, self.rl2, self.rl3])
        out[(f < self.f1) | (f > self.f3)] = np.nan
        return out

    def evaluate(self, f: np.ndarray, rl_meas: np.ndarray) -> Optional[dict]:
        """Esito: ok, margine minimo [dB], frequenza peggiore, punti fuori."""
        lim = self.limit_rl(f)
        sel = np.isfinite(lim) & np.isfinite(rl_meas)
        if not sel.any():
            return None
        margin = rl_meas[sel] - lim[sel]
        k = int(np.argmin(margin))
        return {"ok": bool(margin[k] >= 0), "margin": float(margin[k]),
                "f_worst": float(f[sel][k]), "n_fail": int((margin < 0).sum()),
                "n": int(sel.sum())}

    # ---- disegno -------------------------------------------------------
    def curve(self, fmt: str, y_top: float, n: int = 120):
        """Poligonale (x, y) del limite nelle unita' del formato, con pareti
        verticali ai bordi banda fino a y_top. None se il formato non e' supportato."""
        if fmt not in MASK_FORMATS:
            return None
        f = np.concatenate([np.linspace(self.f1, self.f2, n // 2, endpoint=False),
                            np.linspace(self.f2, self.f3, n // 2)])
        rl = np.interp(f, [self.f1, self.f2, self.f3], [self.rl1, self.rl2, self.rl3])
        g = 10.0 ** (-rl / 20.0)
        if fmt in ("dB Mag", "Log Mag"):
            y = -rl
        elif fmt == "SWR":
            y = (1.0 + g) / np.maximum(1.0 - g, 1e-9)
        else:
            y = g
        x = np.concatenate(([f[0]], f, [f[-1]]))
        yy = np.concatenate(([y_top], y, [y_top]))
        return x, yy

    # ---- (de)serializzazione ------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Mask":
        names = Mask.__dataclass_fields__.keys()
        return Mask(**{k: v for k, v in d.items() if k in names})


def rl_from_complex(data: np.ndarray) -> np.ndarray:
    """Return loss [dB] da un coefficiente complesso (positivo = buon adattamento)."""
    return -20.0 * np.log10(np.abs(data) + 1e-15)


class MaskDialog(QDialog):
    """Editor della maschera: inizio / centro / fine banda + RL minimo."""

    def __init__(self, parent, mask: Optional[Mask] = None, f_span=(1e8, 6e9)):
        super().__init__(parent)
        self.setWindowTitle("Maschera return loss")
        m = mask or Mask(f1=f_span[0], f2=(f_span[0] + f_span[1]) / 2, f3=f_span[1])
        lay = QVBoxLayout(self)
        self.chk = QCheckBox("Maschera attiva")
        self.chk.setChecked(m.enabled)
        lay.addWidget(self.chk)

        g = QGridLayout()
        g.addWidget(QLabel(""), 0, 0)
        g.addWidget(QLabel("Frequenza"), 0, 1)
        g.addWidget(QLabel("RL minimo"), 0, 2)
        self._f, self._r = [], []
        for row, (name, f, rl) in enumerate((("Inizio banda", m.f1, m.rl1),
                                             ("Centro banda", m.f2, m.rl2),
                                             ("Fine banda", m.f3, m.rl3)), start=1):
            g.addWidget(QLabel(name), row, 0)
            sf = QDoubleSpinBox(); sf.setRange(0.0, 6000.0); sf.setDecimals(6)
            sf.setSuffix(" MHz"); sf.setValue(f / 1e6); sf.setMinimumWidth(150)
            sr = QDoubleSpinBox(); sr.setRange(0.0, 100.0); sr.setDecimals(2)
            sr.setSuffix(" dB"); sr.setValue(rl)
            g.addWidget(sf, row, 1); g.addWidget(sr, row, 2)
            self._f.append(sf); self._r.append(sr)
        lay.addLayout(g)
        hint = QLabel("Il limite e' lineare in dB tra i punti; fuori da inizio-fine non c'e' limite. "
                      "Conforme se RL misurato >= RL minimo (S11 sotto la linea).")
        hint.setWordWrap(True); lay.addWidget(hint)

        row = QHBoxLayout()
        b_s = QPushButton("Salva…"); b_s.clicked.connect(self._save)
        b_l = QPushButton("Carica…"); b_l.clicked.connect(self._load)
        row.addWidget(b_s); row.addWidget(b_l); row.addStretch(1)
        lay.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _accept(self):
        f = [s.value() for s in self._f]
        if not (f[0] < f[1] < f[2]):
            QMessageBox.warning(self, "Maschera",
                                "Le frequenze devono essere crescenti: inizio < centro < fine.")
            return
        self.accept()

    def mask(self) -> Mask:
        return Mask(f1=self._f[0].value() * 1e6, f2=self._f[1].value() * 1e6,
                    f3=self._f[2].value() * 1e6,
                    rl1=self._r[0].value(), rl2=self._r[1].value(), rl3=self._r[2].value(),
                    enabled=self.chk.isChecked())

    def _set(self, m: Mask):
        for s, v in zip(self._f, (m.f1, m.f2, m.f3)): s.setValue(v / 1e6)
        for s, v in zip(self._r, (m.rl1, m.rl2, m.rl3)): s.setValue(v)
        self.chk.setChecked(m.enabled)

    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Salva maschera", "maschera.json", "Maschera (*.json)")
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(self.mask().to_dict(), fh, indent=2)

    def _load(self):
        p, _ = QFileDialog.getOpenFileName(self, "Carica maschera", "", "Maschera (*.json)")
        if p:
            try:
                with open(p, encoding="utf-8") as fh:
                    self._set(Mask.from_dict(json.load(fh)))
            except Exception as e:
                QMessageBox.warning(self, "Maschera", f"File non valido: {e}")
