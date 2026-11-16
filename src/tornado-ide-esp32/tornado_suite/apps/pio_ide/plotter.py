"""Grafico in tempo reale dei valori numerici stampati sulla seriale.

Formati riconosciuti per riga: ``12.5`` · ``1,2,3`` (valori separati da virgola, punto e virgola, tab o spazio) ·
``temp:21.5 umid=40`` (nome:valore o nome=valore).
"""
from __future__ import annotations

import csv
import math
import re
from collections import deque
from typing import Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from ...core.theme import P

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_KV = re.compile(rf"([A-Za-z_][\w.\-]*)\s*[:=]\s*({_NUM})(?![\w.])")
_NUM_ONLY = re.compile(rf"^{_NUM}$")
COLORS = ("#ffb000", "#ffe08a", "#b87800", "#ff9a00", "#ffd060", "#8a5a00", "#ffc933", "#fff4d6")


def parse_values(line: str) -> list[tuple[str, float]]:
    s = _ANSI.sub("", line).strip()
    if not s:
        return []
    kv = _KV.findall(s)
    if kv:
        return [(k, float(v)) for k, v in kv]
    parts = [p for p in re.split(r"[,;\t ]+", s) if p]
    if parts and len(parts) <= 16 and all(_NUM_ONLY.match(p) for p in parts):
        return [(f"v{i + 1}", float(p)) for i, p in enumerate(parts)]
    return []


def nice_step(span: float, n: int = 5) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


class PlotCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(120)
        self.series: dict[str, deque] = {}
        self.maxlen = 500
        self.autoscale = True
        self.ymin = -1.0
        self.ymax = 1.0
        self.paused = False

    def clear(self) -> None:
        self.series.clear()
        self.update()

    def set_maxlen(self, n: int) -> None:
        self.maxlen = max(20, n)
        for k, d in list(self.series.items()):
            self.series[k] = deque(d, maxlen=self.maxlen)
        self.update()

    def add(self, vals: list[tuple[str, float]]) -> None:
        if self.paused:
            return
        for k, v in vals:
            if not math.isfinite(v):
                continue
            self.series.setdefault(k, deque(maxlen=self.maxlen)).append(v)
        if len(self.series) > 16:                       # difesa: un log testuale non deve creare decine di serie
            for k in list(self.series)[16:]:
                del self.series[k]
        self.update()

    def _range(self) -> tuple[float, float]:
        if not self.autoscale:
            return self.ymin, self.ymax
        vals = [x for d in self.series.values() for x in d]
        if not vals:
            return -1.0, 1.0
        lo, hi = min(vals), max(vals)
        if lo == hi:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.08
        return lo - pad, hi + pad

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(P.canvas))
        lm, rm, tm, bm = 56, 10, 8, 8
        plot = QRectF(lm, tm, max(10, self.width() - lm - rm), max(10, self.height() - tm - bm))
        lo, hi = self._range()
        step = nice_step(hi - lo)
        p.setFont(QFont(self.font().family(), 8))
        g = math.ceil(lo / step) * step
        while g <= hi:
            y = plot.bottom() - (g - lo) / (hi - lo) * plot.height()
            p.setPen(QPen(QColor(P.border), 1))
            p.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            p.setPen(QColor(P.text_dim))
            p.drawText(QRectF(0, y - 8, lm - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{g:.6g}")
            g += step
        p.setPen(QPen(QColor(P.border), 1))
        p.drawRect(plot)
        n_max = max((len(d) for d in self.series.values()), default=0)
        x_scale = plot.width() / max(1, n_max - 1)          # tutti i campioni presenti occupano la larghezza; a finestra piena scorre
        legend_y = plot.top() + 4
        for i, (name, d) in enumerate(self.series.items()):
            col = QColor(COLORS[i % len(COLORS)])
            p.setPen(QPen(col, 1.6))
            pts = list(d)
            off = len(pts) - n_max
            prev = None
            for j, v in enumerate(pts):
                x = plot.right() - (len(pts) - 1 - j) * x_scale
                y = plot.bottom() - (min(max(v, lo), hi) - lo) / (hi - lo) * plot.height()
                if prev is not None:
                    p.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                prev = (x, y)
            last = pts[-1] if pts else 0.0
            p.setPen(col)
            p.drawText(int(plot.left() + 8), int(legend_y + 11), f"■ {name} = {last:.6g}")
            legend_y += 14
        if self.paused:
            p.setPen(QColor(P.accent))
            p.drawText(QRectF(plot.left(), plot.top(), plot.width() - 8, 16), Qt.AlignmentFlag.AlignRight, "PAUSA")
        p.end()


class PlotterPanel(QWidget):
    """Grafico alimentato dai byte della seriale (``feed_bytes``)."""

    def __init__(self) -> None:
        super().__init__()
        self.canvas = PlotCanvas()
        self._buf = ""
        self.b_pause = QPushButton("Pausa")
        self.b_pause.setCheckable(True)
        b_clear = QPushButton("Pulisci")
        b_csv = QPushButton("Esporta CSV…")
        self.spin = QSpinBox()
        self.spin.setRange(20, 20000)
        self.spin.setValue(500)
        self.spin.setSuffix(" campioni")
        self.chk_auto = QCheckBox("Scala automatica")
        self.chk_auto.setChecked(True)
        self.lbl = QLabel("In attesa di numeri dalla seriale (es. «a:1.2 b:3.4» o «1,2,3»)")
        self.lbl.setObjectName("Dim")
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        for w in (self.b_pause, b_clear, b_csv, self.spin, self.chk_auto):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.lbl)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(bar)
        v.addWidget(self.canvas, 1)
        self.b_pause.toggled.connect(lambda on: setattr(self.canvas, "paused", on) or self.canvas.update())
        b_clear.clicked.connect(self.canvas.clear)
        b_csv.clicked.connect(self.export_csv)
        self.spin.valueChanged.connect(self.canvas.set_maxlen)
        self.chk_auto.toggled.connect(lambda on: setattr(self.canvas, "autoscale", on) or self.canvas.update())

    def feed_bytes(self, data: bytes) -> None:
        self._buf += data.decode("utf-8", "replace")
        if len(self._buf) > 65536:
            self._buf = self._buf[-4096:]
        *lines, self._buf = re.split(r"\r\n|\n|\r", self._buf)
        for ln in lines:
            vals = parse_values(ln)
            if vals:
                self.canvas.add(vals)
        if self.canvas.series:
            self.lbl.setText(f"{len(self.canvas.series)} serie")

    def export_csv(self) -> None:
        if not self.canvas.series:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta dati", "grafico.csv", "CSV (*.csv)")
        if path:
            self.write_csv(path)

    def write_csv(self, path: str) -> None:
        names = list(self.canvas.series)
        cols = [list(self.canvas.series[n]) for n in names]
        n = max((len(c) for c in cols), default=0)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["campione", *names])
            for i in range(n):
                w.writerow([i, *[(c[i - (n - len(c))] if i >= n - len(c) else "") for c in cols]])
