"""
DiagramWidget — una finestra-diagramma autonoma (modello ZNB).

Ogni diagramma ha:
  - header dedicato con la trace list (nome, misura, formato, scala) di OGNI
    trace del diagramma; la trace attiva è evidenziata. Niente più overlay
    unico in alto a sinistra della finestra globale.
  - grafico proprio (cartesiano o Smith/polar)
  - scala e marker per-trace
  - click sull'header per selezionare la trace attiva
  - click sul diagramma per renderlo il diagramma attivo (bordo evidenziato)

Segnali:
  selected(diagram_index)          emesso quando il diagramma viene cliccato
  traceSelected(diag_idx, tr_idx)  emesso quando una trace dell'header è cliccata
"""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame

from .formats import FORMATS, transform, scalar_str
from .measure import compute
from .smith import SmithChart
from .const import Z0
from .ui_widgets import fmt_freq
from .mask import Mask, rl_from_complex
from tornado_ui import P, qcolor
from tornado_ui.plot_style import style_pyqtgraph


class Overlay(QLabel):
    """Etichetta semitrasparente sopra il grafico (marker box, esito maschera)."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setTextFormat(Qt.RichText)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setContentsMargins(8, 4, 8, 4)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(qcolor(P.border)))
        p.setBrush(qcolor("rgba(23,26,31,225)"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
        p.end()
        super().paintEvent(e)


class TraceHeaderLabel(QLabel):
    """Etichetta cliccabile per una trace nell'header del diagramma."""
    def __init__(self, text, on_click):
        super().__init__(text)
        self._on_click = on_click
        self.setTextFormat(Qt.RichText)
        self.setObjectName("traceheader")
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if self._on_click:
            self._on_click()
        super().mousePressEvent(e)


class DiagramWidget(QFrame):
    selected      = Signal(int)
    traceSelected = Signal(int, int)
    plotClicked   = Signal(int, float)     # (indice diagramma, frequenza cliccata)

    def __init__(self, index: int):
        super().__init__()
        self.setObjectName("diagram")
        self._index = index
        self._diagram = None          # riferimento al modello Diagram
        self._last_f = None
        self._last_t = {}             # {Sij: ndarray} ultima matrice S
        self._curves = {}             # trace_name → PlotDataItem / smith item
        self._markers = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2); lay.setSpacing(0)

        # header: trace list dedicata
        self._header = QWidget()
        self._hlay = QVBoxLayout(self._header)
        self._hlay.setContentsMargins(6, 3, 6, 3); self._hlay.setSpacing(1)
        lay.addWidget(self._header)

        # area grafico: cartesiano + smith in stack
        self._plot = pg.PlotWidget()
        style_pyqtgraph(self._plot)
        # performance su sweep lunghi (fino a 4501 punti):
        # - downsampling automatico (mostra meno punti dello zoom corrente)
        # - clipToView: disegna solo la porzione visibile
        self._plot.setDownsampling(auto=True, mode="peak")
        self._plot.setClipToView(True)
        # asse X FISSO: esattamente start..stop, niente padding ne' pan/zoom
        vb = self._plot.getPlotItem().getViewBox()
        vb.setMouseEnabled(x=False, y=False)
        try:
            vb.setDefaultPadding(0)
        except Exception:
            pass
        self._plot.enableAutoRange(x=False, y=False)
        self._xr = None
        self._plot.scene().sigMouseClicked.connect(self._on_plot_click)
        self._smith = SmithChart()
        lay.addWidget(self._plot, 1)
        lay.addWidget(self._smith, 1)
        self._smith.setVisible(False)

        # maschera return loss: linea limite + esito (PASS/FAIL)
        self._mask_item = pg.PlotCurveItem(pen=pg.mkPen(P.danger, width=2))
        self._plot.addItem(self._mask_item)
        self._maskbox = Overlay(self._plot)
        self._maskbox.setVisible(False)

        # marker box (in alto a destra DENTRO questo diagramma)
        self._mbox = Overlay(self._plot)
        self._mbox.move(12, 6)

    # ── selezione ────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self.selected.emit(self._index)
        super().mousePressEvent(e)

    def set_active_style(self, is_active: bool):
        self._is_active = is_active
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(qcolor(P.accent if getattr(self, "_is_active", False) else P.border), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # ── binding modello ──────────────────────────────────────────────────────
    def bind(self, diagram):
        self._diagram = diagram
        self._rebuild_header()
        self._rebuild_curves()

    def _rebuild_header(self):
        # svuota
        while self._hlay.count():
            it = self._hlay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        if not self._diagram:
            return
        for ti, tr in enumerate(self._diagram.traces):
            active = (ti == self._diagram.active)
            arrow  = "▸ " if active else "  "
            scale = f"{tr.div:g}/div  ref {tr.ref:g}  (+{tr.up}/-{tr.down})"
            html = (f"<span style='color:{tr.color};'>{arrow}<b>{tr.name}</b> "
                    f"{tr.display_meas}</span> "
                    f"<span style='color:{P.text_dim};'>{tr.fmt}  {scale}</span>")
            if active:
                html = f"<span style='background:{P.raised};'>{html}</span>"
            lbl = TraceHeaderLabel(html,
                                   lambda ti=ti: self.traceSelected.emit(self._index, ti))
            self._hlay.addWidget(lbl)

    def _rebuild_curves(self):
        # rimuovi vecchie curve
        for c in self._curves.values():
            try: self._plot.removeItem(c)
            except Exception: pass
        self._curves.clear()
        self._smith.clear_all()
        if not self._diagram:
            return
        # decide se il diagramma è Smith/polar o cartesiano in base alla trace attiva
        at = self._diagram.active_trace()
        kind = FORMATS.get(at.fmt, {}).get("kind", "rect") if at else "rect"
        is_complex_plane = kind in ("smith", "inv_smith", "polar")
        self._plot.setVisible(not is_complex_plane)
        self._smith.setVisible(is_complex_plane)
        # crea una curva per ogni trace cartesiana
        if not is_complex_plane:
            for tr in self._diagram.traces:
                pen = pg.mkPen(tr.color, width=2)
                c = self._plot.plot([], [], pen=pen, name=tr.name)
                self._curves[tr.name] = c

    # ── update dati ──────────────────────────────────────────────────────────
    def update_data(self, freqs, traces_s):
        """freqs: ndarray, traces_s: dict {Sij: ndarray complex}."""
        self._last_f = freqs
        self._last_t = traces_s
        if not self._diagram or freqs is None:
            return

        at = self._diagram.active_trace()
        kind = FORMATS.get(at.fmt, {}).get("kind", "rect") if at else "rect"
        is_complex_plane = kind in ("smith", "inv_smith", "polar")

        if is_complex_plane:
            self._smith.setVisible(True); self._plot.setVisible(False)
            self._smith.clear_all()
            for tr in self._diagram.traces:
                data = compute(tr.meas, traces_s, Z0)
                if data is not None:
                    self._smith.update_trace(tr.name, data, tr.color)
            return

        self._plot.setVisible(True); self._smith.setVisible(False)
        for tr in self._diagram.traces:
            data = compute(tr.meas, traces_s, Z0)
            if data is None:
                continue
            y = transform(tr.fmt, data, freqs, Z0)
            n = min(len(freqs), len(y))
            c = self._curves.get(tr.name)
            if c is not None:
                c.setData(freqs[:n], y[:n])

        self._apply_scale()
        self._update_markers()
        self._update_mask()

    # ── maschera ─────────────────────────────────────────────────────────────
    def _update_mask(self):
        at = self._diagram.active_trace() if self._diagram else None
        m = Mask.from_dict(at.mask) if (at is not None and at.mask) else None
        if m is None or not m.enabled:
            self._mask_item.setData([], [])
            self._maskbox.setVisible(False)
            return
        top = at.ref + at.up * at.div
        c = m.curve(at.fmt, top)
        if c is None:
            self._mask_item.setData([], [])
            html = f"<span style='color:{P.accent};'>Maschera: usa formato dB Mag, SWR o Lin Mag</span>"
        else:
            self._mask_item.setData(c[0], c[1])
            html = f"<span style='color:{P.text_dim};'>Maschera: nessun punto in banda</span>"
            if self._last_f is not None:
                data = compute(at.meas, self._last_t, Z0)
                if data is not None:
                    n = min(len(self._last_f), len(data))
                    res = m.evaluate(self._last_f[:n], rl_from_complex(data[:n]))
                    if res is not None:
                        col = P.ok if res["ok"] else P.danger
                        word = "PASS" if res["ok"] else "FAIL"
                        html = (f"<b style='color:{col};'>MASK {word}</b> "
                                f"<span style='color:{P.text};'>margine {res['margin']:+.2f} dB "
                                f"@ {fmt_freq(res['f_worst'])}"
                                + (f" &nbsp;({res['n_fail']}/{res['n']} punti fuori)"
                                   if not res["ok"] else "") + "</span>")
        self._maskbox.setText(html)
        self._maskbox.adjustSize()
        self._maskbox.move(64, 6)
        self._maskbox.setVisible(True)

    # ── asse X fisso ─────────────────────────────────────────────────────────
    def set_x_range(self, start: float, stop: float):
        self._xr = (float(start), float(stop))
        self._plot.setXRange(self._xr[0], self._xr[1], padding=0)

    # ── scala Y fissa (ref - down*div ... ref + up*div) ─────────────────────
    @staticmethod
    def _nice_step(raw: float) -> float:
        if not np.isfinite(raw) or raw <= 0:
            return 1.0
        e = np.floor(np.log10(raw))
        m = raw / 10 ** e
        for k in (1, 2, 5, 10):
            if m <= k:
                return k * 10 ** e
        return 10 ** (e + 1)

    def _fit_once(self, tr):
        """Adatta ref/div ai dati correnti UNA volta, poi la scala resta fissa."""
        data = compute(tr.meas, self._last_t, Z0) if self._last_t else None
        if data is None or self._last_f is None:
            return
        y = transform(tr.fmt, data, self._last_f, Z0)
        y = y[np.isfinite(y)]
        if len(y) == 0:
            return
        lo, hi = float(y.min()), float(y.max())
        span = hi - lo
        if span <= 0:
            span = max(abs(hi) * 0.2, 1e-12)
        tr.div = self._nice_step(span / 8.0)
        tr.ref = float(np.ceil(hi / tr.div) * tr.div)
        tr.up, tr.down = 1, 9
        tr.needs_fit = False
        self._rebuild_header()

    def _apply_scale(self):
        at = self._diagram.active_trace() if self._diagram else None
        if at is None:
            return
        if at.needs_fit:
            self._fit_once(at)
        top = at.ref + at.up * at.div
        bot = at.ref - at.down * at.div
        if top <= bot:
            top = bot + at.div
        self._plot.setYRange(bot, top, padding=0)
        if self._xr is not None:
            self._plot.setXRange(self._xr[0], self._xr[1], padding=0)
        # etichetta asse Y dalla trace attiva
        info = FORMATS.get(at.fmt, {})
        self._plot.setLabel("left", info.get("label", ""), units=info.get("unit", ""))
        self._plot.setLabel("bottom", "Frequency", units="Hz")

    # ── marker ────────────────────────────────────────────────────────────────
    def _on_plot_click(self, ev):
        if ev.button() != Qt.LeftButton or not self._plot.isVisible():
            return
        vb = self._plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        self.plotClicked.emit(self._index, float(vb.mapSceneToView(ev.scenePos()).x()))

    def _clear_marker_items(self):
        for m in self._markers:
            try: self._plot.removeItem(m)
            except Exception: pass
        self._markers.clear()

    def _update_markers(self):
        self._clear_marker_items()
        at = self._diagram.active_trace() if self._diagram else None
        if at is None or not at.markers or self._last_f is None:
            self._mbox.setVisible(False)
            return
        data = compute(at.meas, self._last_t, Z0)
        if data is None:
            return
        f = self._last_f
        n = min(len(f), len(data))
        f, data = f[:n], data[:n]
        y = transform(at.fmt, data, f, Z0)
        unit = FORMATS.get(at.fmt, {}).get("unit", "")
        idxs = []
        for mk in at.markers:
            mk.idx = int(np.argmin(np.abs(f - mk.freq)))
            idxs.append(mk.idx)
        rows = []
        for k, mk in enumerate(at.markers):
            idx = mk.idx
            fm = f[idx]
            sel = (k == at.msel)
            line = pg.InfiniteLine(
                pos=fm, angle=90,
                pen=pg.mkPen(P.text if sel else P.text_dim, width=1, style=Qt.DashLine))
            self._plot.addItem(line); self._markers.append(line)
            if np.isfinite(y[idx]):
                dot = pg.ScatterPlotItem([fm], [y[idx]], size=11 if sel else 8,
                                         pen=pg.mkPen(P.text), brush=pg.mkBrush(at.color))
                self._plot.addItem(dot); self._markers.append(dot)
                lab = pg.TextItem(f"M{k+1}", color=at.color, anchor=(0.5, 1.4))
                lab.setPos(fm, y[idx])
                self._plot.addItem(lab); self._markers.append(lab)
            r = mk.ref
            if 0 <= r < len(at.markers) and r != k and np.isfinite(y[idx]) and np.isfinite(y[idxs[r]]):
                txt = (f"M{k+1}\u0394M{r+1}: \u0394{fmt_freq(fm - f[idxs[r]])}  "
                       f"\u0394{y[idx] - y[idxs[r]]:+.4g} {unit}")
            else:
                val = scalar_str(at.fmt, data[idx], fm, Z0, data, f)
                txt = f"M{k+1}: {fmt_freq(fm)}  {val}"
            arrow = "\u25b8 " if sel else ""
            rows.append(f"<span style='color:{at.color};'>{arrow}{txt}</span>")
        self._mbox.setText("<br>".join(rows))
        self._mbox.adjustSize()
        self._mbox.move(max(12, self._plot.width() - self._mbox.width() - 12), 6)
        self._mbox.setVisible(True)

    def refresh(self):
        """Ridisegna header e ricrea curve (dopo cambi struttura)."""
        self._rebuild_header()
        self._rebuild_curves()
        if self._last_f is not None:
            self.update_data(self._last_f, self._last_t)
        else:
            self._apply_scale()
            self._update_mask()
