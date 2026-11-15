"""Primitive di disegno condivise dai grafici (font, legenda, marker, formattazione valori)."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF

from ...core.traces import KINDS
from ...core.units import fmt_eng
from ..theme import Theme, qcolor


def font(px: int = 11, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont("Consolas" if mono else "Segoe UI")
    f.setStyleHint(QFont.StyleHint.Monospace if mono else QFont.StyleHint.SansSerif)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def clamp_px(v: np.ndarray, lim: float = 1e6) -> np.ndarray:
    return np.clip(v, -lim, lim)


_FMT = {
    "db": ("{:.3f} dB", "{:+.3f} dB"), "rl": ("{:.3f} dB", "{:+.3f} dB"), "il": ("{:.3f} dB", "{:+.3f} dB"),
    "mag": ("{:.5f}", "{:+.5f}"), "re": ("{:.5f}", "{:+.5f}"), "im": ("{:.5f}", "{:+.5f}"),
    "phase": ("{:.3f}°", "{:+.3f}°"), "phase_unwrap": ("{:.3f}°", "{:+.3f}°"),
    "gd": ("{:.4f} ns", "{:+.4f} ns"), "vswr": ("{:.4f}", "{:+.4f}"),
    "z_re": ("{:.3f} Ω", "{:+.3f} Ω"), "z_im": ("{:.3f} Ω", "{:+.3f} Ω"), "z_mag": ("{:.3f} Ω", "{:+.3f} Ω"),
    "y_re": ("{:.4f} mS", "{:+.4f} mS"), "y_im": ("{:.4f} mS", "{:+.4f} mS"), "y_mag": ("{:.4f} mS", "{:+.4f} mS"),
    "td_db": ("{:.2f} dB", "{:+.2f} dB"), "td_lin": ("{:.5f}", "{:+.5f}"),
    "ls": ("{:.4f} nH", "{:+.4f} nH"), "lp": ("{:.4f} nH", "{:+.4f} nH"),
    "cs": ("{:.4f} pF", "{:+.4f} pF"), "cp": ("{:.4f} pF", "{:+.4f} pF"), "q": ("{:.3f}", "{:+.3f}"),
}


def format_value(kind: str, v: float, delta: bool = False) -> str:
    if v is None or not np.isfinite(v):
        return "—"
    fm = _FMT.get(kind)
    if fm is None:
        return f"{v:+.5g}" if delta else f"{v:.5g}"
    return fm[1 if delta else 0].format(v)


def readouts_rect(plot: QRectF, lines: list) -> Optional[QRectF]:
    if not lines:
        return None
    fm = QFontMetricsF(font(11, mono=True))
    w = max(fm.horizontalAdvance(t) for _, t in lines) + 20
    return QRectF(plot.left() + 8, plot.top() + 8, w, len(lines) * 16 + 8)


def draw_legend(p: QPainter, plot: QRectF, items: list[tuple[str, str]], th: Theme,
                max_items: int = 10, avoid: Optional[QRectF] = None) -> None:
    if not items:
        return
    f = font(11)
    fm = QFontMetricsF(f)
    shown = items[:max_items]
    extra = len(items) - len(shown)
    labels = [fm.elidedText(l, Qt.TextElideMode.ElideMiddle, 220) for _, l in shown]
    if extra > 0:
        labels.append(f"… +{extra} altre")
    w = max(fm.horizontalAdvance(t) for t in labels) + 34
    h = len(labels) * 16 + 8
    box = QRectF(plot.right() - w - 8, plot.top() + 8, w, h)
    if avoid is not None and box.intersects(avoid.adjusted(-6, -6, 6, 6)):
        box.moveTop(avoid.bottom() + 8)
        if box.bottom() > plot.bottom():
            box.moveLeft(max(plot.left() + 8, avoid.right() + 8))
            box.moveTop(plot.top() + 8)
    p.save()
    p.setPen(QPen(qcolor(th.grid), 1))
    p.setBrush(qcolor(th.legend_bg))
    p.drawRoundedRect(box, 4, 4)
    p.setFont(f)
    for k, t in enumerate(labels):
        y = box.top() + 4 + k * 16 + 8
        if k < len(shown):
            p.setPen(QPen(qcolor(shown[k][0]), 2.4))
            p.drawLine(QPointF(box.left() + 7, y), QPointF(box.left() + 23, y))
        p.setPen(qcolor(th.text))
        p.drawText(QPointF(box.left() + 29, y + 4), t)
    p.restore()


def draw_marker_glyph(p: QPainter, x: float, y: float, label: str, color: str, th: Theme,
                      active: bool = False, delta: bool = False) -> None:
    p.save()
    col = qcolor(color)
    outline = qcolor(th.text)
    s = 6.5 if active else 5.0
    tri = QPolygonF([QPointF(x - s, y - 7 - 2 * s), QPointF(x + s, y - 7 - 2 * s), QPointF(x, y - 7)])
    p.setPen(QPen(outline, 1.3))
    p.setBrush(col if not delta else qcolor(th.accent))
    p.drawPolygon(tri)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(outline, 1.4))
    p.drawEllipse(QPointF(x, y), 3.6, 3.6)
    p.setFont(font(11, bold=True))
    p.setPen(qcolor(th.text))
    fm = QFontMetricsF(p.font())
    tw = fm.horizontalAdvance(label)
    p.drawText(QPointF(x - tw / 2, y - 12 - 2 * s), label)
    p.restore()


def draw_readouts(p: QPainter, plot: QRectF, lines: list[tuple[str, str]], th: Theme) -> None:
    """Riquadro di letture marker in alto a sinistra: lista di (colore, testo)."""
    if not lines:
        return
    f = font(11, mono=True)
    fm = QFontMetricsF(f)
    w = max(fm.horizontalAdvance(t) for _, t in lines) + 20
    h = len(lines) * 16 + 8
    box = QRectF(plot.left() + 8, plot.top() + 8, w, h)
    p.save()
    p.setPen(QPen(qcolor(th.grid), 1))
    p.setBrush(qcolor(th.legend_bg))
    p.drawRoundedRect(box, 4, 4)
    p.setFont(f)
    for k, (c, t) in enumerate(lines):
        p.setPen(qcolor(c))
        p.drawText(QPointF(box.left() + 10, box.top() + 4 + k * 16 + 12), t)
    p.restore()


def draw_rubber(p: QPainter, r: QRectF, th: Theme) -> None:
    p.save()
    p.setPen(QPen(qcolor(th.accent), 1, Qt.PenStyle.DashLine))
    p.setBrush(qcolor(th.selection))
    p.drawRect(r.normalized())
    p.restore()


def fmt_axis_value(v: float, digits: int = 6) -> str:
    return fmt_eng(v, "", digits)
