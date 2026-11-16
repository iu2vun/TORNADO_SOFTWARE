"""Tela per Gerber/Excellon (estende la tela comune)."""
from __future__ import annotations
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF

from .canvas import Canvas

ROLE_COL = {"top": "#ff5a36", "bottom": "#3d8bff", "inner": "#7acb54", "mask_t": "#2fa84f", "mask_b": "#2fa84f",
            "silk_t": "#ffd060", "silk_b": "#c9a24a", "paste_t": "#9a9a9a", "paste_b": "#9a9a9a",
            "outline": "#e8e8e8", "drill": "#ffffff", "other": "#ffb000"}
ORDER = ["bottom", "paste_b", "silk_b", "mask_b", "inner", "top", "mask_t", "paste_t", "silk_t", "outline", "drill", "other"]
DEFAULT_OFF = {"mask_t", "mask_b", "paste_t", "paste_b"}
EXTS = (".gbr", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp", ".gm1", ".gko", ".gml", ".drl", ".xln", ".exc",
        ".ger", ".art", ".pho", ".cmp", ".sol", ".stc", ".sts", ".plc", ".pls")


class GerberCanvas(Canvas):
    def __init__(self):
        super().__init__()
        self.mode = "gbr"
        self.layers: list[list] = []          # [GerberLayer, QColor, visible]
        self._ap_cache: dict = {}

    def set_layers(self, layers):
        self.mode = "gbr"
        self.layers = sorted(layers, key=lambda L: ORDER.index(L[0].role) if L[0].role in ORDER else 99)
        self._ap_cache.clear()
        self.fit()

    def bounds(self) -> QRectF:
        if self.mode != "gbr":
            return super().bounds()
        xs0 = ys0 = 1e18; xs1 = ys1 = -1e18
        for lay, _, vis in self.layers:
            b = lay.bbox()
            if b and vis:
                xs0, ys0, xs1, ys1 = min(xs0, b[0]), min(ys0, b[1]), max(xs1, b[2]), max(ys1, b[3])
        if xs0 > xs1:
            for lay, _, _ in self.layers:
                b = lay.bbox()
                if b:
                    xs0, ys0, xs1, ys1 = min(xs0, b[0]), min(ys0, b[1]), max(xs1, b[2]), max(ys1, b[3])
        if xs0 > xs1:
            return QRectF(0, 0, 100, 100)
        return QRectF(xs0, ys0, max(1.0, xs1 - xs0), max(1.0, ys1 - ys0))

    def to_unit(self, v):
        if self.mode != "gbr":          # PCB/schemi Altium: unità interna mil
            return super().to_unit(v)
        return v / 0.0254 if self.unit == "mil" else v   # Gerber: unità interna mm

    def _ap_shapes(self, ap):
        """Poligoni (esposizione, punti) in coordinate locali dell'apertura, memorizzati."""
        key = id(ap)
        if key in self._ap_cache:
            return self._ap_cache[key]
        shapes = []
        if ap.kind == "R" and len(ap.p) >= 2:
            w, h = ap.p[0] / 2, ap.p[1] / 2
            shapes.append((1, [(-w, -h), (w, -h), (w, h), (-w, h)]))
        elif ap.kind == "O" and len(ap.p) >= 2:
            w, h = ap.p[0], ap.p[1]
            r = min(w, h) / 2
            pts = []
            if w >= h:
                c0, c1 = (-(w / 2 - r), 0), ((w / 2 - r), 0)
                for i in range(0, 19):
                    a = -math.pi / 2 + math.pi * i / 18; pts.append((c1[0] + r * math.cos(a), r * math.sin(a)))
                for i in range(0, 19):
                    a = math.pi / 2 + math.pi * i / 18; pts.append((c0[0] + r * math.cos(a), r * math.sin(a)))
            else:
                c0, c1 = (0, -(h / 2 - r)), (0, (h / 2 - r))
                for i in range(0, 19):
                    a = math.pi * i / 18; pts.append((r * math.cos(a), c1[1] + r * math.sin(a)))
                for i in range(0, 19):
                    a = math.pi + math.pi * i / 18; pts.append((r * math.cos(a), c0[1] + r * math.sin(a)))
            shapes.append((1, pts))
        elif ap.kind == "P" and len(ap.p) >= 2:
            d, n = ap.p[0], max(3, int(ap.p[1])); rot = ap.p[2] if len(ap.p) > 2 else 0
            shapes.append((1, [(d / 2 * math.cos(math.radians(rot) + 2 * math.pi * i / n),
                                d / 2 * math.sin(math.radians(rot) + 2 * math.pi * i / n)) for i in range(n)]))
        elif ap.kind == "M":
            shapes = list(ap.polys)
        self._ap_cache[key] = shapes
        return shapes

    def _paint_extra(self, p: QPainter):
        bg = QColor("#000000")
        mpx = 1.0 / self.scale
        for lay, col, vis in self.layers:
            if not vis:
                continue
            fill = QColor(col)
            if lay.role in ("mask_t", "mask_b", "paste_t", "paste_b"):
                fill.setAlpha(120)
            for o in lay.ops:
                c = fill if o[-1] else bg
                if o[0] == "s":
                    p.setPen(self._pen(c, max(o[5], 0.0))); p.drawLine(QPointF(o[1], o[2]), QPointF(o[3], o[4]))
                elif o[0] == "r":
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(c)
                    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in o[1]]))
                else:
                    ap = lay.apertures.get(o[3])
                    if ap is None:
                        continue
                    p.setPen(Qt.PenStyle.NoPen)
                    if ap.kind == "C":
                        p.setBrush(c); r = ap.p[0] / 2 if ap.p else 0
                        p.drawEllipse(QPointF(o[1], o[2]), r, r)
                        if len(ap.p) > 1 and ap.p[1] > 0:
                            p.setBrush(bg); p.drawEllipse(QPointF(o[1], o[2]), ap.p[1] / 2, ap.p[1] / 2)
                    else:
                        for ex, pts in self._ap_shapes(ap):
                            p.setBrush(c if ex else bg)
                            p.drawPolygon(QPolygonF([QPointF(o[1] + x, o[2] + y) for x, y in pts]))
            if lay.holes or lay.slots:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(col))
                for x, y, d in lay.holes:
                    p.drawEllipse(QPointF(x, y), d / 2, d / 2)
                for x1, y1, x2, y2, d in lay.slots:
                    p.setPen(self._pen(QColor(col), d)); p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


