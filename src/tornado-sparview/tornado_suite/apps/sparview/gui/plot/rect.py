"""Grafico cartesiano: frequenza (o tempo) in ascissa, grandezza scalare in ordinata."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QCursor, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFileDialog, QMenu

from ...core.traces import KINDS, trace_values
from ...core.units import choose_freq_unit, fmt_freq
from ..model import Workspace
from ..theme import DARK, LIGHT, Theme, qcolor
from . import draw as D
from .base import PlotView
from .ticks import decimals_for, fmt_tick, linear_ticks, log_ticks, minor_ticks, nice_step


@dataclass
class Series:
    x: np.ndarray
    y: np.ndarray
    color: str
    label: str
    key: Optional[tuple] = None
    width: float = 1.6
    dashed: bool = False


def minmax_decimate(px: np.ndarray, py: np.ndarray, fast: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Riduce a ~4 punti per colonna di pixel preservando l'inviluppo (picchi e notch); 2 se `fast`."""
    b = np.floor(px - px[0]).astype(np.int64)
    starts = np.concatenate(([0], np.flatnonzero(np.diff(b)) + 1))
    ends = np.append(starts[1:], px.size)
    cnt = (ends - starts).astype(float)
    with np.errstate(all="ignore"):
        ymin = np.fmin.reduceat(py, starts)
        ymax = np.fmax.reduceat(py, starts)
    xs = np.add.reduceat(px, starts) / cnt
    first, last = py[starts], py[ends - 1]
    min_first = np.abs(first - ymin) <= np.abs(first - ymax)
    a = np.where(min_first, ymin, ymax)
    c = np.where(min_first, ymax, ymin)
    if fast:
        return np.repeat(xs, 2), np.column_stack((a, c)).ravel()
    ox = np.repeat(xs, 4)
    oy = np.column_stack((first, a, c, last)).ravel()
    return ox, oy


def runs_of_valid(valid: np.ndarray) -> list[tuple[int, int]]:
    if valid.all():
        return [(0, valid.size)]
    d = np.diff(np.concatenate(([0], valid.astype(np.int8), [0])))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def to_polygon(px: np.ndarray, py: np.ndarray) -> QPolygonF:
    return QPolygonF([QPointF(a, b) for a, b in zip(px.tolist(), py.tolist())])


class RectPlot(PlotView):
    supports_markers = True
    xRangeChanged = Signal(float, float)
    gateChanged = Signal(float, float)

    def __init__(self, workspace: Workspace, theme: Theme = DARK, parent=None):
        super().__init__(workspace, theme, parent)
        self.kind = "db"
        self.xlog = False
        self.x_manual: Optional[tuple[float, float]] = None
        self.y_manual: Optional[tuple[float, float]] = None
        self.show_minor = True
        self.gd_aperture = 1
        self.smooth = 0
        self.hlines: list[float] = []
        self.bands: list[tuple[float, float, str]] = []     # evidenziazioni (x0, x1, etichetta)
        self.plot_title = ""
        self._series: Optional[list[Series]] = None
        self._poly_cache: dict = {}
        self._geom = None
        self._hover: Optional[QPointF] = None
        self._rb: Optional[tuple[QPointF, QPointF, bool]] = None
        self._pan = None
        self._drag_marker: Optional[int] = None
        self._press_pos: Optional[QPointF] = None
        self._press_mods = Qt.KeyboardModifier.NoModifier
        self._moved = False
        self.setCursor(Qt.CursorShape.CrossCursor)

    # =========================================================== dati
    def invalidate(self) -> None:
        self._series = None
        self._poly_cache.clear()
        self._geom = None

    def series(self) -> list[Series]:
        if self._series is None:
            self._series = self.build_series()
        return self._series

    def build_series(self) -> list[Series]:
        out = []
        for t in self.ws.traces():
            y = trace_values(t.net, t.i, t.j, self.kind, gd_aperture=self.gd_aperture)
            if self.smooth and self.smooth > 1:
                y = _smooth(y, int(self.smooth))
            out.append(Series(t.net.freq, y, t.color, t.label, t.key, t.width))
        return out

    # unità/etichette (sovrascritte dal dominio del tempo)
    def x_unit(self, lo: float, hi: float) -> tuple[str, float]:
        return choose_freq_unit(max(abs(lo), abs(hi)))

    def x_title(self, unit: str) -> str:
        return f"Frequenza [{unit}]"

    def y_title(self) -> str:
        return KINDS[self.kind][0]

    def y_kind_for_format(self) -> str:
        return self.kind

    def format_x(self, x: float) -> str:
        return fmt_freq(x)

    # =========================================================== viste
    def _auto_x(self, series: list[Series]) -> tuple[float, float]:
        ext = [(s.x[0], s.x[-1]) for s in series if s.x.size]
        if not ext:
            return 0.0, 1.0
        lo, hi = min(a for a, _ in ext), max(b for _, b in ext)
        if self.xlog and lo <= 0:
            pos = [s.x[s.x > 0][0] for s in series if (s.x > 0).any()]
            lo = min(pos) if pos else 1.0
        if hi <= lo:
            hi = lo + (abs(lo) * 0.01 if lo else 1.0)
        return float(lo), float(hi)

    def _auto_y(self, series: list[Series], xr: tuple[float, float]) -> tuple[float, float]:
        lo, hi = math.inf, -math.inf
        k = self.y_kind_for_format()
        for s in series:
            i0 = np.searchsorted(s.x, xr[0], "left")
            i1 = np.searchsorted(s.x, xr[1], "right")
            yy = s.y[i0:i1]
            yy = yy[np.isfinite(yy)]
            if k in ("db", "rl", "il"):
                yy = yy[np.abs(yy) < 250]
            if yy.size:
                lo, hi = min(lo, float(yy.min())), max(hi, float(yy.max()))
        if not math.isfinite(lo):
            return -1.0, 1.0
        if k == "phase" and lo >= -180.0001 and hi <= 180.0001:
            return -180.0, 180.0
        if k == "vswr":
            lo = 1.0
        if k in ("mag", "q") and lo >= 0 and lo < 0.25 * hi:
            lo = 0.0
        span = hi - lo
        if span <= 0:
            span = max(abs(hi) * 0.1, 1.0)
            lo, hi = lo - span / 2, hi + span / 2
            span = hi - lo
        lo2, hi2 = lo - 0.04 * span, hi + 0.04 * span
        step = nice_step(hi2 - lo2, 8)
        lo2 = math.floor(lo2 / step + 1e-9) * step
        hi2 = math.ceil(hi2 / step - 1e-9) * step
        if k == "vswr" and lo2 > 1.0:
            lo2 = 1.0
        return lo2, hi2

    def view_x(self, series: Optional[list[Series]] = None) -> tuple[float, float]:
        if self.x_manual:
            return self.x_manual
        return self._auto_x(series if series is not None else self.series())

    def view_y(self, series: Optional[list[Series]] = None, xr=None) -> tuple[float, float]:
        if self.y_manual:
            return self.y_manual
        series = series if series is not None else self.series()
        return self._auto_y(series, xr or self.view_x(series))

    def autoscale(self) -> None:
        self.x_manual = None
        self.y_manual = None
        self._after_view_change()

    def set_ranges(self, x: Optional[tuple[float, float]] = None, y: Optional[tuple[float, float]] = None,
                   keep: bool = False) -> None:
        if not keep or x is not None:
            self.x_manual = x
        if not keep or y is not None:
            self.y_manual = y
        self._after_view_change()

    def _after_view_change(self) -> None:
        self._poly_cache.clear()
        self._geom = None
        self.update()
        xr = self.view_x()
        self.viewChanged.emit()
        self.xRangeChanged.emit(xr[0], xr[1])

    # =========================================================== geometria
    def plot_rect(self, rect: QRectF) -> QRectF:
        return QRectF(rect.left() + 68, rect.top() + 14, max(rect.width() - 68 - 18, 50),
                      max(rect.height() - 14 - 46, 50))

    def geometry(self, rect: Optional[QRectF] = None):
        """(plot_rect, xr, yr, series) con cache per le dimensioni correnti del widget."""
        if rect is None:
            rect = QRectF(0, 0, self.width(), self.height())
            if self._geom is not None and self._geom[0] == (rect.width(), rect.height()):
                return self._geom[1]
        series = self.series()
        plot = self.plot_rect(rect)
        xr = self.view_x(series)
        yr = self.view_y(series, xr)
        g = (plot, xr, yr, series)
        if rect.left() == 0 and rect.top() == 0 and rect.width() == self.width():
            self._geom = ((rect.width(), rect.height()), g)
        return g

    def _fx(self, x):
        return np.log10(np.maximum(x, 1e-300)) if self.xlog else x

    def x2px(self, x, plot: QRectF, xr):
        a, b = self._fx(xr[0]), self._fx(xr[1])
        return plot.left() + (self._fx(x) - a) / (b - a) * plot.width()

    def px2x(self, px, plot: QRectF, xr):
        a, b = self._fx(xr[0]), self._fx(xr[1])
        v = a + (px - plot.left()) / plot.width() * (b - a)
        return 10 ** v if self.xlog else v

    @staticmethod
    def y2px(y, plot: QRectF, yr):
        return plot.bottom() - (y - yr[0]) / (yr[1] - yr[0]) * plot.height()

    @staticmethod
    def px2y(py, plot: QRectF, yr):
        return yr[0] + (plot.bottom() - py) / plot.height() * (yr[1] - yr[0])

    # =========================================================== disegno
    def draw_static(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        p.save()
        p.fillRect(rect, qcolor(th.bg))
        plot, xr, yr, series = self.geometry(rect if not interactive else None)
        p.fillRect(plot, qcolor(th.plot_bg))
        self._draw_grid(p, plot, xr, yr, th)
        p.save()
        p.setClipRect(plot)
        self._draw_bands(p, plot, xr, th)
        for si, s in enumerate(series):
            self._draw_series(p, s, si, plot, xr, yr, interactive)
        self._draw_hlines(p, plot, yr, th)
        p.restore()
        p.setPen(QPen(qcolor(th.axis), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(plot)
        if not series:
            p.setPen(qcolor(th.text_dim))
            p.setFont(D.font(13))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter,
                       "Nessuna traccia — apri un file (Ctrl+O) o trascinalo qui")
        p.restore()

    def draw_dynamic(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        plot, xr, yr, series = self.geometry(rect if not interactive else None)
        p.save()
        p.setClipRect(plot)
        self.draw_overlay(p, plot, xr, yr, th, interactive)
        if self.supports_markers:
            self._draw_markers(p, plot, xr, yr, th, series)
        if interactive and self._rb:
            D.draw_rubber(p, QRectF(self._rb[0], self._rb[1]), th)
        if interactive and self._hover is not None and plot.contains(self._hover) and not self._rb:
            p.setPen(QPen(qcolor(th.text_dim), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(self._hover.x(), plot.top()), QPointF(self._hover.x(), plot.bottom()))
        p.restore()
        if self.show_legend and series:
            D.draw_legend(p, plot, [(s.color, s.label) for s in series], th,
                          avoid=self._readouts_rect(plot, th, series))
        if self.supports_markers:
            self._draw_readouts(p, plot, th, series)

    def draw_overlay(self, p, plot, xr, yr, th, interactive) -> None:
        """Hook per sottoclassi (gate del dominio del tempo...)."""

    def _draw_grid(self, p: QPainter, plot: QRectF, xr, yr, th: Theme) -> None:
        f = D.font(11)
        p.setFont(f)
        fm = QFontMetricsF(f)
        unit, mult = self.x_unit(*xr)
        # ---- x
        if self.xlog and xr[0] > 0:
            major, minor = log_ticks(*xr)
            if self.show_minor:
                p.setPen(QPen(qcolor(th.grid_minor), 1))
                for t in minor:
                    x = self.x2px(t, plot, xr)
                    p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            labels = [(t, self._fmt_log_tick(t, mult)) for t in major]
            xt = major
        else:
            tick_lo, tick_hi = xr[0] / mult, xr[1] / mult
            ticks, step = linear_ticks(tick_lo, tick_hi, max(int(plot.width() / 85), 2))
            if self.show_minor:
                p.setPen(QPen(qcolor(th.grid_minor), 1))
                for t in minor_ticks(tick_lo, tick_hi, step):
                    x = self.x2px(t * mult, plot, xr)
                    p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            dec = decimals_for(step)
            xt = ticks * mult
            labels = [(t * mult, fmt_tick(t, dec)) for t in ticks]
        p.setPen(QPen(qcolor(th.grid), 1))
        for t, _ in labels:
            x = self.x2px(t, plot, xr)
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        p.setPen(qcolor(th.text_dim))
        for t, lab in labels:
            x = self.x2px(t, plot, xr)
            w = fm.horizontalAdvance(lab)
            p.drawText(QPointF(x - w / 2, plot.bottom() + 15), lab)
        p.setPen(qcolor(th.text))
        title = self.x_title(unit)
        p.setFont(D.font(12))
        tw = QFontMetricsF(p.font()).horizontalAdvance(title)
        p.drawText(QPointF(plot.center().x() - tw / 2, plot.bottom() + 34), title)
        # ---- y
        p.setFont(f)
        yt, ystep = linear_ticks(yr[0], yr[1], max(int(plot.height() / 42), 2))
        if self.show_minor:
            p.setPen(QPen(qcolor(th.grid_minor), 1))
            for t in minor_ticks(yr[0], yr[1], ystep):
                y = self.y2px(t, plot, yr)
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(QPen(qcolor(th.grid), 1))
        for t in yt:
            y = self.y2px(t, plot, yr)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        dec = decimals_for(ystep)
        p.setPen(qcolor(th.text_dim))
        for t in yt:
            y = self.y2px(t, plot, yr)
            lab = fmt_tick(t, dec)
            w = fm.horizontalAdvance(lab)
            p.drawText(QPointF(plot.left() - w - 7, y + 4), lab)
        p.save()
        p.setPen(qcolor(th.text))
        p.setFont(D.font(12))
        p.translate(plot.left() - 52, plot.center().y())
        p.rotate(-90)
        yt_title = self.y_title()
        tw = QFontMetricsF(p.font()).horizontalAdvance(yt_title)
        p.drawText(QPointF(-tw / 2, 0), yt_title)
        p.restore()

    @staticmethod
    def _fmt_log_tick(t: float, mult: float) -> str:
        v = t / mult
        return f"{v:g}"

    def _draw_bands(self, p, plot, xr, th) -> None:
        for (a, b, lab) in self.bands:
            xa, xb = self.x2px(max(a, xr[0]), plot, xr), self.x2px(min(b, xr[1]), plot, xr)
            if xb <= xa:
                continue
            c = qcolor(th.accent)
            c.setAlpha(34)
            p.fillRect(QRectF(xa, plot.top(), xb - xa, plot.height()), c)
            p.setPen(QPen(qcolor(th.accent), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(xa, plot.top()), QPointF(xa, plot.bottom()))
            p.drawLine(QPointF(xb, plot.top()), QPointF(xb, plot.bottom()))
            if lab:
                p.setFont(D.font(10))
                p.drawText(QPointF(xa + 4, plot.bottom() - 6), lab)

    def _draw_hlines(self, p, plot, yr, th) -> None:
        for v in self.hlines:
            y = self.y2px(v, plot, yr)
            p.setPen(QPen(qcolor(th.accent), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setFont(D.font(10))
            p.drawText(QPointF(plot.left() + 4, y - 3), D.format_value(self.y_kind_for_format(), v))

    def _polys_for(self, s: Series, si: int, plot: QRectF, xr, yr) -> list[QPolygonF]:
        fast = self._fast_render
        key = (si, plot.width(), plot.height(), xr, yr, self.xlog, fast)
        hit = self._poly_cache.get(key)
        if hit is not None:
            return hit
        x, y = s.x, s.y
        n = x.size
        i0 = max(int(np.searchsorted(x, xr[0], "left")) - 1, 0)
        i1 = min(int(np.searchsorted(x, xr[1], "right")) + 1, n)
        x, y = x[i0:i1], y[i0:i1]
        polys: list[QPolygonF] = []
        if x.size:
            if self.xlog:
                m = x > 0
                x, y = x[m], y[m]
            px = D.clamp_px(self.x2px(x, plot, xr))
            py = D.clamp_px(self.y2px(y, plot, yr))
            if px.size > 3 * plot.width():
                px, py = minmax_decimate(px, py, fast)
            valid = np.isfinite(py)
            for a, b in runs_of_valid(valid):
                if b - a >= 1:
                    polys.append(to_polygon(px[a:b], py[a:b]))
        if len(self._poly_cache) > 64:
            self._poly_cache.clear()
        self._poly_cache[key] = polys
        return polys

    def _draw_series(self, p: QPainter, s: Series, si: int, plot, xr, yr, interactive: bool) -> None:
        polys = self._polys_for(s, si, plot, xr, yr)
        pen = QPen(qcolor(s.color), s.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.BevelJoin)
        if s.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        total = 0
        for poly in polys:
            total += poly.size()
            if poly.size() >= 2:
                p.drawPolyline(poly)
        if 0 < total <= max(60, plot.width() / 7):
            p.setBrush(qcolor(s.color))
            for poly in polys:
                for k in range(poly.size()):
                    p.drawEllipse(poly.at(k), 2.2, 2.2)

    # ------------------------------------------------------------ marker
    def marker_value(self, series: list[Series], key, freq: float) -> Optional[float]:
        for s in series:
            if s.key == key and s.x.size:
                if freq < s.x[0] or freq > s.x[-1]:
                    return None
                return float(np.interp(freq, s.x, s.y))
        return None

    def _draw_markers(self, p, plot, xr, yr, th, series) -> None:
        mm = self.ws.markers
        for m in mm.markers:
            v = self.marker_value(series, m.key, m.freq)
            if v is None or not math.isfinite(v):
                continue
            if m.freq < xr[0] or m.freq > xr[1]:
                continue
            x = self.x2px(m.freq, plot, xr)
            y = self.y2px(v, plot, yr)
            col = next((s.color for s in series if s.key == m.key), th.accent)
            D.draw_marker_glyph(p, float(x), float(y), ("Δ" if m.ref else "") + m.name, col, th,
                                m.id == mm.active, m.ref is not None)

    def _readouts_rect(self, plot, th, series):
        if not self.supports_markers:
            return None
        return D.readouts_rect(plot, self._readout_lines(th, series))

    def _draw_readouts(self, p, plot, th, series) -> None:
        D.draw_readouts(p, plot, self._readout_lines(th, series), th)

    def _readout_lines(self, th, series) -> list:
        mm = self.ws.markers
        lines = []
        k = self.y_kind_for_format()
        for m in mm.markers[:6]:
            v = self.marker_value(series, m.key, m.freq)
            col = next((s.color for s in series if s.key == m.key), th.text)
            if v is None:
                continue
            ref = mm.get(m.ref) if m.ref else None
            if ref is not None:
                vr = self.marker_value(series, ref.key, ref.freq)
                if vr is not None:
                    txt = (f"Δ{m.name}-{ref.name}  Δf={self.format_x(m.freq - ref.freq)}  "
                           f"Δy={D.format_value(k, v - vr, True)}")
                    lines.append((col, txt))
                    continue
            lines.append((col, f"{m.name}  {self.format_x(m.freq)}  {D.format_value(k, v)}"))
        return lines

    # =========================================================== mouse
    def _nearest(self, pos: QPointF, max_dist: float = 1e9):
        """Punto di traccia più vicino al cursore in pixel: (series, idx, dist)."""
        plot, xr, yr, series = self.geometry()
        best = (None, -1, max_dist)
        for s in series:
            if s.key is None or not s.x.size:
                continue
            i0 = max(int(np.searchsorted(s.x, xr[0], "left")) - 1, 0)
            i1 = min(int(np.searchsorted(s.x, xr[1], "right")) + 1, s.x.size)
            if i1 <= i0:
                continue
            xs, ys = s.x[i0:i1], s.y[i0:i1]
            ok = np.isfinite(ys)
            if self.xlog:
                ok &= xs > 0
            if not ok.any():
                continue
            px = self.x2px(xs, plot, xr)
            py = self.y2px(ys, plot, yr)
            d = np.hypot(px - pos.x(), py - pos.y())
            d[~ok] = np.inf
            k = int(np.argmin(d))
            if d[k] < best[2]:
                best = (s, i0 + k, float(d[k]))
        return best

    def _marker_at(self, pos: QPointF, tol: float = 12.0) -> Optional[int]:
        plot, xr, yr, series = self.geometry()
        for m in reversed(self.ws.markers.markers):
            v = self.marker_value(series, m.key, m.freq)
            if v is None or not math.isfinite(v):
                continue
            x = float(self.x2px(m.freq, plot, xr))
            y = float(self.y2px(v, plot, yr))
            if abs(pos.x() - x) <= tol and (-24 - 2 <= pos.y() - y <= tol):
                return m.id
        return None

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        pos = ev.position()
        plot = self.geometry()[0]
        self._press_pos = pos
        self._press_mods = ev.modifiers()
        self._moved = False
        if not plot.contains(pos):
            return
        if ev.button() == Qt.MouseButton.LeftButton:
            if self.supports_markers:
                mid = self._marker_at(pos)
                if mid is not None:
                    self.ws.markers.set_active(mid)
                    self._drag_marker = mid
                    return
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._start_pan(pos)
            else:
                self._rb = (pos, pos, False)
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self._start_pan(pos)

    def _start_pan(self, pos: QPointF) -> None:
        _, xr, yr, _ = self.geometry()
        self._pan = (pos, xr, yr)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        plot, xr, yr, series = self.geometry()
        self._hover = pos
        if self._press_pos is not None and (pos - self._press_pos).manhattanLength() > 4:
            self._moved = True
        if self._drag_marker is not None:
            s, k, _ = self._nearest_for_marker(pos)
            if s is not None:
                self.ws.markers.move(self._drag_marker, float(s.x[k]), s.key)
            return
        if self._pan:
            p0, xr0, yr0 = self._pan
            dx, dy = pos.x() - p0.x(), pos.y() - p0.y()
            a, b = self._fx(xr0[0]), self._fx(xr0[1])
            shift = -dx / plot.width() * (b - a)
            na, nb = a + shift, b + shift
            nx = (10 ** na, 10 ** nb) if self.xlog else (na, nb)
            sy = dy / plot.height() * (yr0[1] - yr0[0])
            self.x_manual = nx
            self.y_manual = (yr0[0] + sy, yr0[1] + sy)
            self._after_view_change()
            return
        if self._rb:
            self._rb = (self._rb[0], pos, self._rb[2])
        if plot.contains(pos):
            x = self.px2x(pos.x(), plot, xr)
            y = self.px2y(pos.y(), plot, yr)
            self.hovered.emit(f"{self.format_x(x)}   |   {D.format_value(self.y_kind_for_format(), y)}")
            if self.supports_markers and self._marker_at(pos) is not None:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif not self._pan:
                self.setCursor(Qt.CursorShape.CrossCursor)
        self.update_overlay()

    def _nearest_for_marker(self, pos: QPointF):
        """Durante il trascinamento resta sulla traccia del marker."""
        m = self.ws.markers.get(self._drag_marker)
        plot, xr, yr, series = self.geometry()
        s = next((s for s in series if m and s.key == m.key), None)
        if s is None or not s.x.size:
            return None, -1, 0
        x = self.px2x(pos.x(), plot, xr)
        k = int(np.argmin(np.abs(s.x - x)))
        return s, k, 0.0

    def mouseReleaseEvent(self, ev) -> None:
        pos = ev.position()
        plot, xr, yr, series = self.geometry()
        if self._drag_marker is not None:
            self._drag_marker = None
        elif self._pan:
            self._pan = None
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._rb:
            rb = QRectF(self._rb[0], self._rb[1]).normalized()
            self._rb = None
            if rb.width() > 6 and rb.height() > 6:
                x0, x1 = self.px2x(rb.left(), plot, xr), self.px2x(rb.right(), plot, xr)
                y1, y0 = self.px2y(rb.top(), plot, yr), self.px2y(rb.bottom(), plot, yr)
                self.x_manual = (float(x0), float(x1))
                self.y_manual = (float(y0), float(y1))
                self._after_view_change()
            elif not self._moved and ev.button() == Qt.MouseButton.LeftButton and plot.contains(pos):
                self._click(pos, bool(self._press_mods & Qt.KeyboardModifier.ControlModifier))
        self._press_pos = None
        self.update_overlay()

    def _click(self, pos: QPointF, new_marker: bool) -> None:
        if not self.supports_markers:
            return
        s, k, d = self._nearest(pos)
        if s is None:
            return
        mm = self.ws.markers
        act = mm.active_marker()
        if new_marker or act is None:
            mm.add(float(s.x[k]), s.key)
        else:
            mm.move(act.id, float(s.x[k]), s.key)

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._rb = None
            self.autoscale()

    def leaveEvent(self, ev) -> None:
        self._hover = None
        self.update_overlay()

    def wheelEvent(self, ev) -> None:
        plot, xr, yr, _ = self.geometry()
        pos = ev.position()
        if not plot.contains(pos):
            return
        f = 0.8 if ev.angleDelta().y() > 0 else 1.25
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            cy = self.px2y(pos.y(), plot, yr)
            self.y_manual = (cy - (cy - yr[0]) * f, cy + (yr[1] - cy) * f)
        else:
            a, b = self._fx(xr[0]), self._fx(xr[1])
            c = a + (pos.x() - plot.left()) / plot.width() * (b - a)
            na, nb = c - (c - a) * f, c + (b - c) * f
            self.x_manual = (10 ** na, 10 ** nb) if self.xlog else (na, nb)
        self._after_view_change()
        ev.accept()

    def keyPressEvent(self, ev) -> None:
        mm = self.ws.markers
        key = ev.key()
        if key == Qt.Key.Key_Home:
            self.autoscale()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and mm.active_marker() and self.supports_markers:
            m = mm.active_marker()
            s = next((s for s in self.series() if s.key == m.key), None)
            if s is not None and s.x.size:
                k = int(np.argmin(np.abs(s.x - m.freq)))
                k = min(max(k + (1 if key == Qt.Key.Key_Right else -1) * (10 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1), 0), s.x.size - 1)
                mm.move(m.id, float(s.x[k]))
        elif key == Qt.Key.Key_Delete and mm.active is not None and self.supports_markers:
            mm.remove(mm.active)
        else:
            super().keyPressEvent(ev)

    # =========================================================== menu
    def contextMenuEvent(self, ev) -> None:
        pos = QPointF(ev.pos())
        menu = QMenu(self)
        if self.supports_markers:
            a = menu.addAction("Aggiungi marker qui")
            a.triggered.connect(lambda: self._click(pos, True))
            a = menu.addAction("Rimuovi tutti i marker")
            a.triggered.connect(self.ws.markers.clear)
            menu.addSeparator()
        menu.addAction("Autoscala (Home)").triggered.connect(self.autoscale)
        a = menu.addAction("Linea orizzontale a questo livello")
        plot, xr, yr, _ = self.geometry()
        yv = self.px2y(pos.y(), plot, yr)
        a.triggered.connect(lambda: (self.hlines.append(float(yv)), self.update()))
        menu.addAction("Rimuovi linee orizzontali").triggered.connect(lambda: (self.hlines.clear(), self.update()))
        menu.addSeparator()
        for text, attr in (("Legenda", "show_legend"), ("Griglia secondaria", "show_minor")):
            a = QAction(text, menu)
            a.setCheckable(True)
            a.setChecked(getattr(self, attr))
            a.toggled.connect(lambda v, at=attr: (setattr(self, at, v), self.update()))
            menu.addAction(a)
        a = QAction("Asse X logaritmico", menu)
        a.setCheckable(True)
        a.setChecked(self.xlog)
        a.toggled.connect(self._set_xlog)
        menu.addAction(a)
        menu.addSeparator()
        menu.addAction("Esporta immagine…").triggered.connect(self.export_dialog)
        menu.exec(QCursor.pos())

    def _set_xlog(self, v: bool) -> None:
        self.xlog = v
        self.x_manual = None
        self._after_view_change()

    def export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Esporta grafico", "grafico.png",
                                              "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if not path:
            return
        dark = path.lower().endswith(".png") and False
        self.export_to(path, th=DARK if dark else LIGHT)


def _smooth(y: np.ndarray, n: int) -> np.ndarray:
    if n < 2 or y.size < 3:
        return y
    n = min(n, y.size)
    ok = np.isfinite(y)
    if not ok.all():
        idx = np.arange(y.size)
        yy = np.interp(idx, idx[ok], y[ok]) if ok.any() else y
    else:
        yy = y
    k = np.ones(n) / n
    pad = n // 2
    ext = np.pad(yy, (pad, n - 1 - pad), mode="edge")
    out = np.convolve(ext, k, mode="valid")
    out[~ok] = np.nan
    return out
