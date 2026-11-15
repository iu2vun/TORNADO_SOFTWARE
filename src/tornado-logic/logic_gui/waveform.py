"""Vista delle forme d'onda: canali digitali + lane dei decoder, cursori A/B, zoom orizzontale.

Il disegno è a fronti: per ogni canale si precalcolano gli indici dei cambi di livello e a ogni
variazione della vista si ricostruisce solo la polilinea dell'intervallo visibile. Con troppi fronti
nell'intervallo si passa a una modalità a densità (una barra per pixel), quindi anche 24 M campioni
restano fluidi.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QApplication

from logic_core import N_CHANNELS, Capture
from logic_core.analysis import Lane
from tornado_ui import P, PLOT, TRACE_COLORS, style_pyqtgraph

LOW, HIGH = 0.22, 0.78       # livelli del segnale dentro la riga (0..1)
BOX0, BOX1 = 0.12, 0.88      # altezza dei riquadri dei decoder dentro la riga
MAX_STEP_EDGES = 6000        # oltre questo numero di fronti visibili: modalità densità
TRIGGER_SYMBOLS = {"rising": "t1", "falling": "t", "either": "d", "high": "o", "low": "o"}  # t1 = ▲, t = ▼
TRACE_WIDTH = 1.0            # px, spessore delle tracce
TRIGGER_MARK_SIZE = 8       # px, triangolino del fronte di trigger
MAX_BOXES = 4000             # riquadri decoder disegnati al massimo per lane


def _mono_font() -> QFont:
    f = QFont(QApplication.font())
    f.setFamilies(["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "monospace"])
    return f


@dataclass
class _Row:
    y0: float
    y1: float
    starts: np.ndarray
    ends: np.ndarray
    texts: list[str]
    errors: np.ndarray
    color: QColor


class AnnotationLayer(pg.GraphicsObject):
    """Riquadri con testo dei decoder, disegnati in coordinate-pixel per avere testo non deformato."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[_Row] = []
        self._rect = QRectF(0, 0, 1, 1)
        self._font = _mono_font()
        self.setZValue(20)

    def set_rows(self, rows: list[_Row], rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._rows, self._rect = rows, rect
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self._rect

    def paint(self, p: QPainter, *_args) -> None:
        vb = self.getViewBox()
        if vb is None or not self._rows:
            return
        x0, x1 = vb.viewRange()[0]
        tr = p.transform()
        p.save()
        p.resetTransform()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(self._font)
        fm = QFontMetricsF(self._font)
        danger, text_col = QColor(P.danger), QColor(P.text)
        for row in self._rows:
            if row.starts.size == 0:
                continue
            lo = max(0, int(np.searchsorted(row.starts, x0)) - 1)
            hi = int(np.searchsorted(row.starts, x1, side="right"))
            if hi - lo > MAX_BOXES:
                continue  # troppo fitto per essere leggibile: si disegna solo la traccia
            for k in range(lo, hi):
                a = tr.map(QPointF(float(row.starts[k]), row.y1))
                b = tr.map(QPointF(float(row.ends[k]), row.y0))
                r = QRectF(a, b).normalized()
                if r.width() < 1.0:
                    r.setWidth(1.0)
                col = danger if row.errors[k] else row.color
                fill = QColor(col)
                fill.setAlpha(46)
                p.setPen(QPen(col, 1))
                p.setBrush(QBrush(fill))
                p.drawRoundedRect(r, 3, 3)
                if r.width() > 16:
                    p.setPen(text_col)
                    p.drawText(r.adjusted(3, 0, -3, 0), Qt.AlignmentFlag.AlignCenter,
                               fm.elidedText(row.texts[k], Qt.TextElideMode.ElideRight, r.width() - 6))
        p.restore()


class WaveformView(pg.PlotWidget):
    cursorsMoved = Signal(float, float)   # tempi (s) dei cursori A e B

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        style_pyqtgraph(self)
        self._pi = self.getPlotItem()
        self._pi.hideButtons()
        self._pi.setMenuEnabled(False)
        self._pi.showGrid(x=True, y=False, alpha=0.25)
        self._pi.setLabel("bottom", "Tempo", units="s")
        self._pi.getAxis("left").setWidth(86)
        self._vb = self._pi.getViewBox()
        self._vb.setMouseEnabled(x=True, y=False)
        self._vb.disableAutoRange()
        self._vb.setDefaultPadding(0.0)

        self._cap: Capture | None = None
        self._edges: list[np.ndarray] = []
        self._visible: list[int] = list(range(N_CHANNELS))
        self._names: list[str] = [f"CH{i + 1}" for i in range(N_CHANNELS)]
        self._lanes: list[Lane] = []
        self._seps: list[pg.InfiniteLine] = []

        self._curves: list[pg.PlotCurveItem] = []
        for ch in range(N_CHANNELS):
            c = pg.PlotCurveItem(pen=pg.mkPen(TRACE_COLORS[ch % len(TRACE_COLORS)], width=TRACE_WIDTH))
            c.setSkipFiniteCheck(True)
            self._pi.addItem(c)
            self._curves.append(c)

        self._annos = AnnotationLayer()
        self._pi.addItem(self._annos)

        # marcatore del fronte di riferimento (trigger) sulla traccia
        self._trig_mark = pg.ScatterPlotItem(pxMode=True, pen=pg.mkPen(P.on_accent, width=0.5),
                                             brush=pg.mkBrush(P.accent))
        self._trig_mark.setZValue(8)
        self._trig_mark.setVisible(False)
        self._pi.addItem(self._trig_mark)

        self._trig = pg.InfiniteLine(angle=90, movable=False,
                                     pen=pg.mkPen(P.text_dim, width=1, style=Qt.PenStyle.DashLine))
        self._trig.setVisible(False)
        self._trig.setZValue(5)
        self._pi.addItem(self._trig)

        self._ca = pg.InfiniteLine(pos=0, angle=90, movable=True, pen=pg.mkPen(P.accent, width=1.4),
                                   label="A", labelOpts={"position": 0.97, "color": P.accent})
        self._cb = pg.InfiniteLine(pos=0, angle=90, movable=True, pen=pg.mkPen("#ffffff", width=1.4),
                                   label="B", labelOpts={"position": 0.91, "color": "#ffffff"})
        for c in (self._ca, self._cb):
            c.setZValue(30)
            self._pi.addItem(c)
            c.sigPositionChanged.connect(self._emit_cursors)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._refresh)
        self._vb.sigXRangeChanged.connect(lambda *_: self._timer.start())
        self._relayout()

    # ------------------------------------------------------------------ API
    @property
    def capture(self) -> Capture | None:
        return self._cap

    def set_capture(self, cap: Capture) -> None:
        self._cap = cap
        d = cap.data
        x = d[1:] ^ d[:-1]
        self._edges = [np.flatnonzero((x >> ch) & 1) + 1 for ch in range(N_CHANNELS)]
        T = len(cap) / cap.samplerate
        self._vb.setLimits(xMin=-0.02 * T, xMax=1.02 * T, minXRange=4.0 / cap.samplerate)
        tt = cap.trigger_time
        self._trig.setVisible(tt is not None)
        if tt is not None:
            self._trig.setValue(tt)
        self.set_cursors(0.25 * T, 0.5 * T)
        self._relayout()
        self.fit()

    def set_channels(self, visible: list[int], names: list[str]) -> None:
        self._visible = list(visible)
        self._names = list(names)
        self._relayout()

    def set_lanes(self, lanes: list[Lane]) -> None:
        self._lanes = list(lanes)
        self._relayout()

    def fit(self) -> None:
        if self._cap is not None:
            self._vb.setXRange(0, len(self._cap) / self._cap.samplerate, padding=0)

    def view_range(self) -> tuple[float, float]:
        x0, x1 = self._vb.viewRange()[0]
        return float(x0), float(x1)

    def set_view(self, x0: float, x1: float) -> None:
        self._vb.setXRange(x0, x1, padding=0)

    def zoom_by(self, factor: float) -> None:
        x0, x1 = self.view_range()
        c, w = (x0 + x1) / 2, (x1 - x0) * factor / 2
        self.set_view(c - w, c + w)

    def center_on(self, t0: float, t1: float | None = None) -> None:
        """Centra la vista su [t0, t1] lasciando spazio attorno (utile per un riquadro di decoder)."""
        t1 = t0 if t1 is None else t1
        fs = self._cap.samplerate if self._cap else 1e6
        w = max((t1 - t0) * 3.0, 40.0 / fs)
        c = (t0 + t1) / 2
        self.set_view(c - w / 2, c + w / 2)

    def cursors(self) -> tuple[float, float]:
        return float(self._ca.value()), float(self._cb.value())

    def set_cursors(self, a: float, b: float) -> None:
        for c, v in ((self._ca, a), (self._cb, b)):
            c.blockSignals(True)
            c.setValue(v)
            c.blockSignals(False)
        self._emit_cursors()

    # ------------------------------------------------------------ interni
    def _emit_cursors(self) -> None:
        a, b = self.cursors()
        self.cursorsMoved.emit(a, b)

    def _n_rows(self) -> int:
        return max(1, len(self._visible) + len(self._lanes))

    def _relayout(self) -> None:
        vis, R = self._visible, self._n_rows()
        self._vb.setLimits(yMin=0, yMax=R)
        self._vb.setYRange(0, R, padding=0)

        for s in self._seps:
            self._pi.removeItem(s)
        self._seps = []
        for y in range(1, R):
            line = pg.InfiniteLine(pos=y, angle=0, pen=pg.mkPen(PLOT["grid_minor"], width=1))
            line.setZValue(-5)
            self._pi.addItem(line)
            self._seps.append(line)

        ticks = [(R - 1 - r + 0.5, self._names[ch]) for r, ch in enumerate(vis)]
        ticks += [(R - 1 - (len(vis) + j) + 0.5, lane.name) for j, lane in enumerate(self._lanes)]
        self._pi.getAxis("left").setTicks([ticks])

        for ch, c in enumerate(self._curves):
            c.setVisible(ch in vis)

        rows: list[_Row] = []
        if self._cap is not None:
            fs = self._cap.samplerate
            for j, lane in enumerate(self._lanes):
                base = R - 1 - (len(vis) + j)
                ann = lane.annotations
                starts = np.array([a.start for a in ann], dtype=np.float64) / fs
                ends = np.array([a.end for a in ann], dtype=np.float64) / fs
                order = np.argsort(starts, kind="stable")
                rows.append(_Row(base + BOX0, base + BOX1, starts[order], ends[order],
                                 [ann[i].text for i in order],
                                 np.array([ann[i].error for i in order], dtype=bool),
                                 QColor(TRACE_COLORS[(j + 1) % len(TRACE_COLORS)])))
            T = len(self._cap) / fs
            self._annos.set_rows(rows, QRectF(-0.02 * T, 0, 1.04 * T, R))
        else:
            self._annos.set_rows([], QRectF(0, 0, 1, R))
        self._update_trigger_mark()
        self._refresh()

    def _refresh(self) -> None:
        cap = self._cap
        if cap is None:
            return
        fs, n = cap.samplerate, len(cap)
        x0, x1 = self.view_range()
        i0 = max(0, int(np.floor(x0 * fs)) - 1)
        i1 = min(n - 1, int(np.ceil(x1 * fs)) + 1)
        R = self._n_rows()
        if i1 <= i0:
            for c in self._curves:
                c.setData([], [])
            return
        width_px = max(200, min(int(self._vb.width()), 4000))
        for r, ch in enumerate(self._visible):
            xs, lv = self._trace(ch, i0, i1, width_px)
            self._curves[ch].setData(xs, (R - 1 - r) + LOW + (HIGH - LOW) * lv)
        self._annos.update()

    def _update_trigger_mark(self) -> None:
        """Triangolo sul canale di trigger: ▲ fronte di salita, ▼ discesa, ◆ entrambi, ● livello."""
        cap = self._cap
        ch, mode = (cap.trigger_channel, cap.trigger_mode) if cap is not None else (None, None)
        tt = cap.trigger_time if cap is not None else None
        if tt is None or ch is None or mode is None or ch not in self._visible:
            self._trig_mark.setVisible(False)
            return
        R = self._n_rows()
        base = R - 1 - self._visible.index(ch)
        sym = TRIGGER_SYMBOLS.get(mode, "o")
        self._trig_mark.setData([tt], [base + HIGH + 0.1], symbol=sym, size=TRIGGER_MARK_SIZE)
        self._trig_mark.setVisible(True)

    def _trace(self, ch: int, i0: int, i1: int, width_px: int) -> tuple[np.ndarray, np.ndarray]:
        """Punti (x in s, livello 0/1) della traccia del canale nell'intervallo di campioni [i0, i1]."""
        cap = self._cap
        fs, data = cap.samplerate, cap.data
        e = self._edges[ch]
        lo, hi = np.searchsorted(e, [i0 + 1, i1 + 1])
        sel = e[lo:hi]
        l0 = (int(data[i0]) >> ch) & 1

        if sel.size <= MAX_STEP_EDGES:  # polilinea a gradini
            seq = np.concatenate(([l0], (data[sel] >> ch) & 1)).astype(np.float64)
            xs = np.concatenate(([i0 / fs], np.repeat(sel / fs, 2), [i1 / fs]))
            ys = np.concatenate(([seq[0]], np.column_stack((seq[:-1], seq[1:])).ravel(), [seq[-1]]))
            return xs, ys

        span = i1 - i0 + 1  # modalità densità: una colonna per pixel
        nb = min(width_px, span)
        bin_start = i0 + (np.arange(nb, dtype=np.int64) * span) // nb
        active = np.zeros(nb, dtype=bool)
        active[((sel - i0) * nb) // span] = True
        level = ((data[bin_start] >> ch) & 1).astype(np.float64)
        npts = 1 + active.astype(np.int64)
        off = np.cumsum(npts) - npts
        ys = np.empty(int(npts.sum()))
        ys[off] = np.where(active, 0.0, level)
        ys[off[active] + 1] = 1.0
        xs = np.repeat((bin_start + 0.5 * span / nb) / fs, npts)
        return xs, ys
