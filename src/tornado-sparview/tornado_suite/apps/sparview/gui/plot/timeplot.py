"""Grafico del dominio del tempo (risposta all'impulso / gradino) con gate trascinabile."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen

from ...analysis import timedomain as td
from ...core.units import choose_time_unit, fmt_eng
from ..model import Workspace
from ..theme import DARK, Theme, qcolor
from . import draw as D
from .rect import RectPlot, Series


class TimeSettings(QObject):
    """Parametri condivisi tra il dock 'Dominio tempo' e i grafici temporali."""

    changed = Signal()
    gateMoved = Signal()

    def __init__(self):
        super().__init__()
        self.mode = "lowpass"
        self.response = "impulse"
        self.window = "kaiser"
        self.beta = 6.0
        self.pad = 4
        self.dc = "auto"
        self.db = False
        self.vf = 1.0
        self.use_distance = False
        self.tmin: Optional[float] = None
        self.tmax: Optional[float] = None
        self.gate_show = False
        self.gate_start = 1e-9
        self.gate_stop = 5e-9
        self.gate_shape = "normale"
        self.gate_notch = False
        self.reset_view = False

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and not callable(v)
                and k not in ("changed", "gateMoved")}

    def from_dict(self, d: dict) -> None:
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)


class TimePlot(RectPlot):
    supports_markers = False

    def __init__(self, workspace: Workspace, settings: TimeSettings, theme: Theme = DARK, parent=None):
        super().__init__(workspace, theme, parent)
        self.ts = settings
        self.error = ""
        self._gate_drag: Optional[str] = None

    # ------------------------------------------------------------------ dati
    def build_series(self) -> list[Series]:
        ts = self.ts
        out: list[Series] = []
        self.error = ""
        for t in self.ws.traces():
            try:
                r = td.transform(t.net.freq, t.values, ts.mode, ts.response, ts.window, ts.beta,
                                 ts.pad, ts.dc)
            except Exception as e:  # noqa: BLE001
                self.error = f"{t.label}: {e}"
                continue
            if ts.mode == "bandpass":
                y = np.abs(r.y)
            else:
                y = r.y.real if r.response == "step" else r.y
                if ts.db:
                    y = np.abs(y)
            if ts.db:
                with np.errstate(divide="ignore"):
                    y = 20 * np.log10(np.maximum(np.abs(y), 1e-12))
            x = td.time_to_distance(r.t, ts.vf) if ts.use_distance else r.t
            out.append(Series(np.asarray(x), np.asarray(y, float), t.color, t.label, None, t.width))
        return out

    def y_kind_for_format(self) -> str:
        return "td_db" if self.ts.db else "td_lin"

    def x_unit(self, lo: float, hi: float):
        if self.ts.use_distance:
            return ("m", 1.0)
        return choose_time_unit(max(abs(lo), abs(hi)))

    def x_title(self, unit: str) -> str:
        return f"Distanza [{unit}] (VF={self.ts.vf:g}, andata/ritorno)" if self.ts.use_distance else f"Tempo [{unit}]"

    def y_title(self) -> str:
        ts = self.ts
        if ts.mode == "bandpass":
            base = "Inviluppo |impulso|"
        else:
            base = "Risposta all'impulso" if ts.response == "impulse" else "Risposta al gradino"
        return base + (" [dB]" if ts.db else "")

    def format_x(self, x: float) -> str:
        return fmt_eng(x, "m" if self.ts.use_distance else "s")

    # ------------------------------------------------------------------ viste
    def _auto_x(self, series):
        if not series:
            return 0.0, 1.0
        lo = min(s.x[0] for s in series)
        hi = max(s.x[-1] for s in series)
        span = hi - lo
        a, b = -0.02 * span, 0.5 * span
        ts = self.ts
        conv = (lambda v: float(td.time_to_distance(v, ts.vf))) if ts.use_distance else float
        if ts.tmin is not None:
            a = conv(ts.tmin)
        if ts.tmax is not None:
            b = conv(ts.tmax)
        if b <= a:
            b = a + span * 0.1
        return max(a, lo), min(b, hi)

    def _auto_y(self, series, xr):
        lo, hi = super()._auto_y(series, xr)
        if self.ts.db:
            hi = min(hi, 20)
            lo = max(lo, hi - 120)
        return lo, hi

    # ---------------------------------------------------------------- gate
    def _t2x(self, t: float) -> float:
        return float(td.time_to_distance(t, self.ts.vf)) if self.ts.use_distance else t

    def _x2t(self, x: float) -> float:
        return float(td.distance_to_time(x, self.ts.vf)) if self.ts.use_distance else x

    def draw_overlay(self, p: QPainter, plot: QRectF, xr, yr, th: Theme, interactive: bool) -> None:
        if self.error:
            p.setPen(qcolor("#FF0000"))
            p.setFont(D.font(11))
            p.drawText(QPointF(plot.left() + 10, plot.bottom() - 10), self.error)
        if not self.ts.gate_show:
            return
        a = self.x2px(self._t2x(self.ts.gate_start), plot, xr)
        b = self.x2px(self._t2x(self.ts.gate_stop), plot, xr)
        col = qcolor(th.accent)
        fill = qcolor(th.accent)
        fill.setAlpha(36 if not self.ts.gate_notch else 20)
        p.fillRect(QRectF(a, plot.top(), b - a, plot.height()), fill)
        p.setPen(QPen(col, 1.6, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(a, plot.top()), QPointF(a, plot.bottom()))
        p.drawLine(QPointF(b, plot.top()), QPointF(b, plot.bottom()))
        p.setFont(D.font(10, bold=True))
        p.setPen(col)
        p.drawText(QPointF(a + 4, plot.top() + 14), "GATE" + (" (notch)" if self.ts.gate_notch else ""))

    def _gate_edge_at(self, pos: QPointF, tol: float = 6.0) -> Optional[str]:
        if not self.ts.gate_show:
            return None
        plot, xr, yr, _ = self.geometry()
        a = float(self.x2px(self._t2x(self.ts.gate_start), plot, xr))
        b = float(self.x2px(self._t2x(self.ts.gate_stop), plot, xr))
        if abs(pos.x() - a) <= tol:
            return "start"
        if abs(pos.x() - b) <= tol:
            return "stop"
        return None

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            edge = self._gate_edge_at(ev.position())
            if edge:
                self._gate_drag = edge
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._gate_drag:
            plot, xr, yr, _ = self.geometry()
            t = self._x2t(self.px2x(ev.position().x(), plot, xr))
            if self._gate_drag == "start":
                self.ts.gate_start = min(t, self.ts.gate_stop - 1e-12)
            else:
                self.ts.gate_stop = max(t, self.ts.gate_start + 1e-12)
            self.ts.gateMoved.emit()
            self.update_overlay()
            return
        if self._gate_edge_at(ev.position()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        if self._gate_drag:
            self._gate_drag = None
            return
        super().mouseReleaseEvent(ev)
