"""Grafici nel piano complesso: carta di Smith (Z/Y) e diagramma polare."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFileDialog, QMenu

from ...core.units import fmt_freq
from ..model import Workspace
from ..theme import DARK, LIGHT, Theme, qcolor
from . import draw as D
from .base import PlotView
from .rect import runs_of_valid, to_polygon
from .ticks import nice_step

MIN_FIT_HALF = 0.05        # semi-lato minimo della vista automatica (zoom massimo sulla traccia)
ZOOM_TRACE_BELOW = 0.4     # Smith: sotto questo |Γ| massimo la vista si stringe sulla traccia
DECIMATE_MIN = 400         # sopra questo numero di punti per tratto si decima in pixel
DECIMATE_STEP = 0.8        # passo (px lungo il percorso) della decimazione
MAX_VERTS = 6000           # tetto di vertici per tratto a piena qualità
MAX_VERTS_FAST = 1500      # ... durante pan/zoom


def decimate_path(px: np.ndarray, py: np.ndarray, step: float = DECIMATE_STEP):
    """Toglie i punti che distano meno di `step` pixel lungo il percorso: stessa forma, molti meno vertici."""
    if px.size <= DECIMATE_MIN:
        return px, py
    cum = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(px), np.diff(py)))))
    b = np.floor(cum / step).astype(np.int64)
    keep = np.empty(px.size, dtype=bool)
    keep[0] = True
    keep[1:] = b[1:] != b[:-1]
    keep[-1] = True
    return px[keep], py[keep]


class ComplexPlot(PlotView):
    supports_markers = True

    def __init__(self, workspace: Workspace, theme: Theme = DARK, parent=None):
        super().__init__(workspace, theme, parent)
        self.center = (0.0, 0.0)
        self.half = 1.15
        self.auto = True
        self.show_minor = True
        self._hover: Optional[QPointF] = None
        self._pan = None
        self._drag_marker: Optional[int] = None
        self._press_pos: Optional[QPointF] = None
        self._press_mods = Qt.KeyboardModifier.NoModifier
        self._moved = False
        self._traces = None
        self._bbox = None
        self._poly_cache: dict = {}
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---------------------------------------------------------------- dati
    def invalidate(self) -> None:
        self._traces = None
        self._bbox = None
        self._poly_cache.clear()

    def traces(self):
        if self._traces is None:
            self._traces = self.ws.traces()
        return self._traces

    def fit(self) -> None:
        self.auto = True
        self.viewChanged.emit()
        self.update()

    def _bounds(self) -> tuple:
        """(re_min, re_max, im_min, im_max, |max|) su tutte le tracce visibili; in cache fino a invalidate()."""
        if self._bbox is None:
            b = None
            for t in self.traces():
                v = t.values
                v = v[np.isfinite(v)]
                if not v.size:
                    continue
                r = (float(v.real.min()), float(v.real.max()), float(v.imag.min()), float(v.imag.max()),
                     float(np.abs(v).max()))
                b = r if b is None else (min(b[0], r[0]), max(b[1], r[1]), min(b[2], r[2]),
                                         max(b[3], r[3]), max(b[4], r[4]))
            self._bbox = b or (0.0, 0.0, 0.0, 0.0, 0.0)
        return self._bbox

    def auto_half(self) -> float:
        return self._half_for(self._bounds()[4])

    def _half_for(self, maxabs: float) -> float:
        return max(1.12, maxabs * 1.08)

    def _auto_view(self, plot: QRectF) -> tuple:
        """((cx, cy), half) della vista automatica: centrata sull'origine, adattata al modulo massimo."""
        return (0.0, 0.0), self.auto_half()

    @staticmethod
    def _fit_region(plot: QRectF, r0: float, r1: float, i0: float, i1: float, margin: float = 1.08,
                    min_half: float = MIN_FIT_HALF) -> tuple:
        """Vista che contiene il rettangolo [r0,r1]x[i0,i1] nel riquadro (qualsiasi proporzione)."""
        hx = max((r1 - r0) / 2 * margin, 1e-9)
        hy = max((i1 - i0) / 2 * margin, 1e-9)
        w, h = max(plot.width(), 1.0), max(plot.height(), 1.0)
        half = max(min(w, h) * max(hx / w, hy / h), min_half)
        return ((r0 + r1) / 2, (i0 + i1) / 2), half

    # ------------------------------------------------------------ geometria
    def plot_rect(self, rect: QRectF) -> QRectF:
        return rect.adjusted(10, 10, -10, -10)

    def _tf(self, plot: QRectF):
        if self.auto:
            self.center, self.half = self._auto_view(plot)
        half = self.half
        side = min(plot.width(), plot.height())
        scale = side / (2 * half)
        return half, scale, plot.center().x(), plot.center().y()

    def g2px(self, z, plot: QRectF):
        half, scale, cx, cy = self._tf(plot)
        z = np.asarray(z)
        return (cx + (z.real - self.center[0]) * scale, cy - (z.imag - self.center[1]) * scale)

    def px2g(self, px: float, py: float, plot: QRectF) -> complex:
        half, scale, cx, cy = self._tf(plot)
        return complex((px - cx) / scale + self.center[0], -(py - cy) / scale + self.center[1])

    # --------------------------------------------------------------- disegno
    def draw_grid(self, p: QPainter, plot: QRectF, th: Theme) -> None:
        raise NotImplementedError

    def readout_lines(self, th) -> list[tuple[str, str]]:
        raise NotImplementedError

    def draw_static(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        p.save()
        p.fillRect(rect, qcolor(th.bg))
        plot = self.plot_rect(rect)
        p.fillRect(plot, qcolor(th.plot_bg))
        p.save()
        p.setClipRect(plot)
        self.draw_grid(p, plot, th)
        traces = self.traces()
        for t in traces:
            self._draw_trace(p, t, plot, th)
        p.restore()
        p.setPen(QPen(qcolor(th.axis), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(plot)
        if not traces:
            p.setPen(qcolor(th.text_dim))
            p.setFont(D.font(13))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Nessuna traccia")
        p.restore()

    def draw_dynamic(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        plot = self.plot_rect(rect)
        traces = self.traces()
        p.save()
        p.setClipRect(plot)
        self._draw_markers(p, plot, th)
        p.restore()
        if self.show_legend and traces:
            D.draw_legend(p, plot, [(t.color, t.label) for t in traces], th,
                          avoid=D.readouts_rect(plot, self.readout_lines(th)))
        D.draw_readouts(p, plot, self.readout_lines(th), th)

    def _trace_polys(self, t, plot: QRectF):
        """Poligoni in pixel della traccia (solo i tratti visibili, decimati), in cache per vista/dimensioni."""
        fast = self._fast_render
        key = (t.key, plot.width(), plot.height(), self.center, self.half, fast)
        hit = self._poly_cache.get(key)
        if hit is not None:
            return hit
        z = t.values
        ok = np.isfinite(z)
        polys: list[QPolygonF] = []
        dots = None
        first = None
        if ok.any():
            px, py = self.g2px(z, plot)
            px, py = D.clamp_px(px), D.clamp_px(py)
            ok = ok & np.isfinite(px) & np.isfinite(py)
            first = (float(px[ok][0]), float(py[ok][0])) if ok.any() else None
            if z.size <= 120:
                dots = list(zip(px[ok].tolist(), py[ok].tolist()))
            if px.size >= 2:
                lo_x, hi_x, lo_y, hi_y = plot.left() - 4, plot.right() + 4, plot.top() - 4, plot.bottom() + 4
                x0, x1, y0, y1 = px[:-1], px[1:], py[:-1], py[1:]
                with np.errstate(invalid="ignore"):
                    seg = ((np.maximum(x0, x1) >= lo_x) & (np.minimum(x0, x1) <= hi_x) &
                           (np.maximum(y0, y1) >= lo_y) & (np.minimum(y0, y1) <= hi_y))
                for a, b in runs_of_valid(seg):          # tratti di segmenti consecutivi che toccano il riquadro
                    qx, qy = decimate_path(px[a:b + 1], py[a:b + 1])
                    cap = MAX_VERTS_FAST if fast else MAX_VERTS
                    if qx.size > cap:                      # traccia molto rumorosa: sottocampiona in modo uniforme
                        k = np.linspace(0, qx.size - 1, cap).astype(np.int64)
                        qx, qy = qx[k], qy[k]
                    polys.append(to_polygon(qx, qy))
        out = (polys, dots, first)
        if len(self._poly_cache) > 48:
            self._poly_cache.clear()
        self._poly_cache[key] = out
        return out

    def _draw_trace(self, p: QPainter, t, plot: QRectF, th: Theme) -> None:
        polys, dots, first = self._trace_polys(t, plot)
        if first is None:
            return
        pen = QPen(qcolor(t.color), t.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.BevelJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for poly in polys:
            if poly.size() >= 2:
                p.drawPolyline(poly)
        if dots:
            p.setBrush(qcolor(t.color))
            for a, b in dots:
                p.drawEllipse(QPointF(a, b), 2.2, 2.2)
        # inizio traccia
        p.setBrush(qcolor(t.color))
        p.setPen(QPen(qcolor(th.text), 1))
        p.drawRect(QRectF(first[0] - 3, first[1] - 3, 6, 6))

    def marker_point(self, key, freq: float) -> Optional[complex]:
        for t in self.traces():
            if t.key == key:
                f = t.net.freq
                if freq < f[0] or freq > f[-1]:
                    return None
                v = t.values
                return complex(np.interp(freq, f, v.real), np.interp(freq, f, v.imag))
        return None

    def _draw_markers(self, p: QPainter, plot: QRectF, th: Theme) -> None:
        mm = self.ws.markers
        for m in mm.markers:
            z = self.marker_point(m.key, m.freq)
            if z is None:
                continue
            x, y = self.g2px(z, plot)
            col = next((t.color for t in self.traces() if t.key == m.key), th.accent)
            D.draw_marker_glyph(p, float(x), float(y), m.name, col, th, m.id == mm.active)

    # ---------------------------------------------------------------- mouse
    def _nearest(self, pos: QPointF):
        plot = self.plot_rect(QRectF(0, 0, self.width(), self.height()))
        best = (None, -1, 1e18)
        for t in self.traces():
            z = t.values
            ok = np.isfinite(z)
            if not ok.any():
                continue
            px, py = self.g2px(z, plot)
            d = np.hypot(px - pos.x(), py - pos.y())
            d[~ok] = np.inf
            k = int(np.argmin(d))
            if d[k] < best[2]:
                best = (t, k, float(d[k]))
        return best

    def _marker_at(self, pos: QPointF, tol: float = 12.0) -> Optional[int]:
        plot = self.plot_rect(QRectF(0, 0, self.width(), self.height()))
        for m in reversed(self.ws.markers.markers):
            z = self.marker_point(m.key, m.freq)
            if z is None:
                continue
            x, y = self.g2px(z, plot)
            if abs(pos.x() - float(x)) <= tol and (-26 <= pos.y() - float(y) <= tol):
                return m.id
        return None

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        pos = ev.position()
        self._press_pos = pos
        self._press_mods = ev.modifiers()
        self._moved = False
        if ev.button() == Qt.MouseButton.LeftButton:
            mid = self._marker_at(pos)
            if mid is not None:
                self.ws.markers.set_active(mid)
                self._drag_marker = mid
                return
            self._start_pan(pos)
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self._start_pan(pos)

    def _start_pan(self, pos: QPointF) -> None:
        self._pan = (pos, self.center)

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        self._hover = pos
        plot = self.plot_rect(QRectF(0, 0, self.width(), self.height()))
        if self._press_pos is not None and (pos - self._press_pos).manhattanLength() > 4:
            self._moved = True
        if self._drag_marker is not None:
            m = self.ws.markers.get(self._drag_marker)
            t = next((t for t in self.traces() if m and t.key == m.key), None)
            if t is not None:
                z = t.values
                px, py = self.g2px(z, plot)
                d = np.hypot(px - pos.x(), py - pos.y())
                d[~np.isfinite(d)] = np.inf
                k = int(np.argmin(d))
                self.ws.markers.move(m.id, float(t.net.freq[k]))
            return
        if self._pan and self._moved:
            p0, c0 = self._pan
            half, scale, _, _ = self._tf(plot)
            self.auto = False
            self.half = half
            self.center = (c0[0] - (pos.x() - p0.x()) / scale, c0[1] + (pos.y() - p0.y()) / scale)
            self.viewChanged.emit()
            self.update()
            return
        z = self.px2g(pos.x(), pos.y(), plot)
        self.hovered.emit(self.hover_text(z))
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._marker_at(pos) is not None
                       else Qt.CursorShape.CrossCursor)

    def hover_text(self, z: complex) -> str:
        m = abs(z)
        return f"Γ = {z.real:+.4f} {z.imag:+.4f}j   |Γ| = {m:.4f}   ∠ {math.degrees(math.atan2(z.imag, z.real)):.2f}°"

    def mouseReleaseEvent(self, ev) -> None:
        pos = ev.position()
        if self._drag_marker is not None:
            self._drag_marker = None
        elif self._pan is not None:
            self._pan = None
            if not self._moved and ev.button() == Qt.MouseButton.LeftButton:
                self._click(pos, bool(self._press_mods & Qt.KeyboardModifier.ControlModifier))
        self._press_pos = None

    def _click(self, pos: QPointF, new_marker: bool) -> None:
        t, k, d = self._nearest(pos)
        if t is None:
            return
        mm = self.ws.markers
        act = mm.active_marker()
        if new_marker or act is None:
            mm.add(float(t.net.freq[k]), t.key)
        else:
            mm.move(act.id, float(t.net.freq[k]), t.key)

    def mouseDoubleClickEvent(self, ev) -> None:
        self._pan = None
        self.fit()

    def leaveEvent(self, ev) -> None:
        self._hover = None
        self.update_overlay()

    def wheelEvent(self, ev) -> None:
        plot = self.plot_rect(QRectF(0, 0, self.width(), self.height()))
        pos = ev.position()
        f = 0.8 if ev.angleDelta().y() > 0 else 1.25
        half, scale, cx, cy = self._tf(plot)
        zc = self.px2g(pos.x(), pos.y(), plot)
        new_half = min(max(half * f, 1e-4), 1e3)
        k = new_half / half
        self.center = (zc.real - (zc.real - self.center[0]) * k, zc.imag - (zc.imag - self.center[1]) * k)
        self.half = new_half
        self.auto = False
        self.viewChanged.emit()
        self.update()
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        mm = self.ws.markers
        key = ev.key()
        if key == Qt.Key.Key_Home:
            self.fit()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and mm.active_marker():
            m = mm.active_marker()
            t = next((t for t in self.traces() if t.key == m.key), None)
            if t is not None:
                k = int(np.argmin(np.abs(t.net.freq - m.freq)))
                step = 10 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                k = min(max(k + (step if key == Qt.Key.Key_Right else -step), 0), t.net.nfreq - 1)
                mm.move(m.id, float(t.net.freq[k]))
        elif key == Qt.Key.Key_Delete and mm.active is not None:
            mm.remove(mm.active)
        else:
            super().keyPressEvent(ev)

    def extra_menu(self, menu: QMenu) -> None:
        pass

    def contextMenuEvent(self, ev) -> None:
        pos = QPointF(ev.pos())
        menu = QMenu(self)
        menu.addAction("Aggiungi marker qui").triggered.connect(lambda: self._click(pos, True))
        menu.addAction("Rimuovi tutti i marker").triggered.connect(self.ws.markers.clear)
        menu.addSeparator()
        menu.addAction("Adatta vista (Home)").triggered.connect(self.fit)
        self.extra_menu(menu)
        a = QAction("Legenda", menu)
        a.setCheckable(True)
        a.setChecked(self.show_legend)
        a.toggled.connect(lambda v: (setattr(self, "show_legend", v), self.update()))
        menu.addAction(a)
        menu.addSeparator()
        menu.addAction("Esporta immagine…").triggered.connect(self.export_dialog)
        menu.exec(QCursor.pos())

    def export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Esporta grafico", "grafico.png",
                                              "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if path:
            side = max(min(self.width(), self.height()), 700)
            self.export_to(path, int(side * 1.2), int(side * 1.0), th=LIGHT)


# =============================================================================== SMITH
class SmithPlot(ComplexPlot):
    def __init__(self, workspace: Workspace, theme: Theme = DARK, parent=None):
        super().__init__(workspace, theme, parent)
        self.admittance = False

    def _auto_view(self, plot: QRectF) -> tuple:
        """Vista adattata alla traccia (non solo al cerchio unitario).

        - traccia piccola (|Γ| < 0.4): zoom sul suo riquadro, la griglia si infittisce;
        - altrimenti: il riquadro delle tracce + il cerchio unitario, centrato sul loro baricentro
          (S21 di un amplificatore con |S21| >> 1 riempie la finestra invece di restare microscopica).
        """
        r0, r1, i0, i1, mx = self._bounds()
        if mx <= 0.0:
            return (0.0, 0.0), 1.12
        if mx < ZOOM_TRACE_BELOW:
            return self._fit_region(plot, r0, r1, i0, i1, margin=1.25)
        center, half = self._fit_region(plot, min(r0, -1.0), max(r1, 1.0), min(i0, -1.0), max(i1, 1.0))
        return center, max(half, 1.12)

    def _r_levels(self, half: float) -> list[float]:
        if half >= 0.6:
            return [0.2, 0.5, 1.0, 2.0, 5.0]
        if half >= 0.25:
            return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 10.0, 20.0]
        fine = [round(0.05 * k, 3) for k in range(1, 21)]
        return fine + [1.2, 1.4, 1.6, 1.8, 2, 2.5, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50]

    def draw_grid(self, p: QPainter, plot: QRectF, th: Theme) -> None:
        half, scale, cx, cy = self._tf(plot)
        sgn = -1.0 if self.admittance else 1.0

        def P(re: float, im: float):
            return QPointF(cx + (re - self.center[0]) * scale, cy - (im - self.center[1]) * scale)

        R = scale
        circle = QPainterPath()
        circle.addEllipse(P(0, 0), R, R)
        # griglia estesa solo se serve: tracce con |Γ| > 1 (S21 di un amplificatore...) o vista allargata a mano
        if self._bounds()[4] > 1.0 or (not self.auto and self.half > 1.5):
            self._draw_extended(p, plot, th, P, R, sgn, circle)
        p.save()
        p.setClipPath(circle, Qt.ClipOperation.IntersectClip)
        levels = self._r_levels(half)
        grid_pen = QPen(qcolor(th.grid), 1)
        p.setPen(grid_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # cerchi a r costante
        for r in levels:
            c = sgn * r / (1 + r)
            rad = R / (1 + r)
            p.drawEllipse(P(c, 0), rad, rad)
        # archi a x costante
        for x in levels:
            for s in (1.0, -1.0):
                xx = s * x
                rad = R / x
                p.drawEllipse(P(sgn * 1.0, sgn * 1.0 / xx), rad, rad)
        # asse reale
        p.setPen(QPen(qcolor(th.axis), 1))
        p.drawLine(P(-1, 0), P(1, 0))
        p.restore()
        p.setPen(QPen(qcolor(th.axis), 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(P(0, 0), R, R)
        # etichette
        p.setFont(D.font(10))
        fm = QFontMetricsF(p.font())
        p.setPen(qcolor(th.text_dim))
        major = [0.2, 0.5, 1.0, 2.0, 5.0] if half >= 0.25 else levels[::2]
        for r in major:
            gx = sgn * (r - 1) / (r + 1)
            pt = P(gx, 0)
            if plot.contains(pt):
                p.drawText(QPointF(pt.x() + 2, pt.y() - 3), f"{r:g}")
        for x in major:
            for s in (1.0, -1.0):
                xx = s * x
                den = xx * xx + 1
                gre, gim = sgn * (xx * xx - 1) / den, sgn * 2 * xx / den
                pt = P(gre, gim)
                if not plot.contains(pt):
                    continue
                ang = math.atan2(-(pt.y() - cy), pt.x() - cx)
                off = 12
                tx = pt.x() + math.cos(ang) * off
                ty = pt.y() - math.sin(ang) * off
                lab = f"{'+' if s > 0 else '-'}{x:g}j"
                p.drawText(QPointF(tx - fm.horizontalAdvance(lab) / 2, ty + 4), lab)
        p.setPen(qcolor(th.text_dim))
        p.setFont(D.font(11, bold=True))
        p.drawText(QPointF(plot.left() + 10, plot.bottom() - 10), "Carta Y" if self.admittance else "Carta Z")

    def _draw_extended(self, p: QPainter, plot: QRectF, th: Theme, P, R: float, sgn: float,
                       circle: QPainterPath) -> None:
        """Griglia fuori dal cerchio unitario (|Γ| > 1, resistenza negativa), tratteggio tenue."""
        outside = QPainterPath()
        outside.addRect(plot)
        outside = outside.subtracted(circle)
        p.save()
        p.setClipPath(outside, Qt.ClipOperation.IntersectClip)
        p.setPen(QPen(qcolor(th.grid_minor), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r in (-0.2, -0.5, -2.0, -5.0):                 # cerchi a r costante negativo
            p.drawEllipse(P(sgn * r / (1 + r), 0), R / abs(1 + r), R / abs(1 + r))
        xl = P(sgn * 1.0, 0).x()                           # r = -1: retta Re(Γ) = ±1
        p.drawLine(QPointF(xl, plot.top() - 1), QPointF(xl, plot.bottom() + 1))
        for x in (0.2, 0.5, 1.0, 2.0, 5.0):                # prosecuzione dei cerchi a x costante
            for s_ in (1.0, -1.0):
                p.drawEllipse(P(sgn * 1.0, sgn / (s_ * x)), R / x, R / x)
        p.restore()

    def extra_menu(self, menu: QMenu) -> None:
        a = QAction("Carta Y (ammettenza)", menu)
        a.setCheckable(True)
        a.setChecked(self.admittance)
        a.toggled.connect(lambda v: (setattr(self, "admittance", v), self.update()))
        menu.addAction(a)

    def readout_lines(self, th) -> list[tuple[str, str]]:
        lines = []
        for m in self.ws.markers.markers[:6]:
            z = self.marker_point(m.key, m.freq)
            if z is None:
                continue
            col = next((t.color for t in self.traces() if t.key == m.key), th.text)
            mag = abs(z)
            db = 20 * math.log10(max(mag, 1e-30))
            txt = f"{m.name}  {fmt_freq(m.freq)}  {mag:.4f}∠{math.degrees(math.atan2(z.imag, z.real)):.2f}° ({db:.2f} dB)"
            tr = next((t for t in self.traces() if t.key == m.key), None)
            if tr is not None and tr.i == tr.j:
                z0 = tr.net.z0[tr.i]
                if abs(1 - z) > 1e-9:
                    zz = z0 * (1 + z) / (1 - z)
                    txt += f"  Z={zz.real:.2f}{zz.imag:+.2f}j Ω"
                    if mag < 1:
                        txt += f"  VSWR={(1 + mag) / (1 - mag):.3f}"
            lines.append((col, txt))
        return lines

    def hover_text(self, z: complex) -> str:
        base = super().hover_text(z)
        if abs(1 - z) > 1e-9 and abs(z) < 50:
            zz = 50 * (1 + z) / (1 - z)
            base += f"   |   Z(50Ω) = {zz.real:.2f}{zz.imag:+.2f}j"
        return base


# =============================================================================== POLARE
class PolarPlot(ComplexPlot):
    def _half_for(self, maxabs: float) -> float:
        if maxabs <= 0:
            return 1.12
        step = nice_step(maxabs, 5)
        rmax = math.ceil(maxabs / step - 1e-9) * step
        return rmax * 1.14

    def draw_grid(self, p: QPainter, plot: QRectF, th: Theme) -> None:
        half, scale, cx, cy = self._tf(plot)
        ox, oy = cx - self.center[0] * scale, cy + self.center[1] * scale   # pixel dello zero
        hw = plot.width() / 2 / scale + abs(self.center[0])
        hh = plot.height() / 2 / scale + abs(self.center[1])
        rvis = math.hypot(hw, hh)
        rmax_data = half / 1.14
        step = nice_step(max(rmax_data, 1e-6), 5)
        if half < rmax_data * 0.9:
            step = nice_step(half * 1.2, 5)
        nmax = int(min(math.ceil(rvis / step), 40))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcolor(th.grid), 1))
        for k in range(1, nmax + 1):
            r = k * step * scale
            p.drawEllipse(QPointF(ox, oy), r, r)
        # raggi
        router = nmax * step * scale
        p.setPen(QPen(qcolor(th.grid), 1))
        for a in range(0, 360, 30):
            ang = math.radians(a)
            p.drawLine(QPointF(ox, oy), QPointF(ox + router * math.cos(ang), oy - router * math.sin(ang)))
        p.setPen(QPen(qcolor(th.axis), 1))
        p.drawLine(QPointF(ox - router, oy), QPointF(ox + router, oy))
        p.drawLine(QPointF(ox, oy - router), QPointF(ox, oy + router))
        # etichette raggio (lungo 45°) e angolo
        p.setFont(D.font(10))
        p.setPen(qcolor(th.text_dim))
        for k in range(1, nmax + 1):
            r = k * step
            pt = QPointF(ox + r * scale * math.cos(math.radians(45)), oy - r * scale * math.sin(math.radians(45)))
            if plot.contains(pt):
                p.drawText(QPointF(pt.x() + 3, pt.y() - 3), f"{r:g}")
        fm = QFontMetricsF(p.font())
        for a in range(-150, 181, 30):
            ang = math.radians(a)
            rr = min(nmax * step * scale, min(plot.width(), plot.height()) / 2) - 16
            pt = QPointF(ox + rr * math.cos(ang), oy - rr * math.sin(ang))
            if plot.adjusted(4, 4, -4, -4).contains(pt):
                lab = f"{a}°"
                p.drawText(QPointF(pt.x() - fm.horizontalAdvance(lab) / 2, pt.y() + 4), lab)
        p.setFont(D.font(11, bold=True))
        p.drawText(QPointF(plot.left() + 10, plot.bottom() - 10), "Polare")

    def readout_lines(self, th) -> list[tuple[str, str]]:
        lines = []
        for m in self.ws.markers.markers[:6]:
            z = self.marker_point(m.key, m.freq)
            if z is None:
                continue
            col = next((t.color for t in self.traces() if t.key == m.key), th.text)
            mag = abs(z)
            db = 20 * math.log10(max(mag, 1e-30))
            lines.append((col, f"{m.name}  {fmt_freq(m.freq)}  {mag:.4f}∠{math.degrees(math.atan2(z.imag, z.real)):.2f}° ({db:.2f} dB)"))
        return lines
