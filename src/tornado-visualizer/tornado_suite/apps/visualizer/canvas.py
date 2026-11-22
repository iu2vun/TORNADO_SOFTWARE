"""Tela di disegno 2D con zoom/pan per PCB e schemi Altium (QPainter, Y verso l'alto)."""
from __future__ import annotations
import math
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from ...core.theme import P
from ..altium_core import layer_name

# colori layer PCB (distinguibili su fondo nero, tema HP 9845C: 8 colori puri)
LAYER_COL = {1: "#FF0000", 32: "#00FFFF", 33: "#FFFF00", 34: "#FFFF00", 35: "#FF0000", 36: "#1010FF",
             37: "#1010FF", 38: "#1010FF", 56: "#FF00FF", 74: "#FFFFFF", 55: "#FFFFFF", 73: "#FFFFFF"}


def layer_color(n: int) -> QColor:
    if n in LAYER_COL:
        return QColor(LAYER_COL[n])
    if 2 <= n <= 31:
        return QColor("#00FF00")
    if 39 <= n <= 54:
        return QColor("#FFFF00")
    if 57 <= n <= 72:
        return QColor("#FFFFFF")
    return QColor("#FFFFFF")


def _arc_pts(cx, cy, r, a0, a1, n=None):
    if a1 <= a0:
        a1 += 360.0
    n = n or max(8, int((a1 - a0) / 6))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


class Canvas(QWidget):
    cursor_moved = Signal(float, float)
    net_picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mode = None            # "pcb" | "sch"
        self.pcb = None
        self.sch = None
        self.visible: set[int] = set()
        self.all_layers: list[int] = []
        self.mirror = False
        self.show_pads = self.show_vias = self.show_fills = self.show_text = True
        self.scale = 0.1            # pixel per mil
        self.cx = self.cy = 0.0     # centro vista (mil)
        self._drag = None
        self.hl_net = -1
        self.measure_mode = False
        self.meas: list = []
        self.unit = "mm"
        self.measured = None
        self._auto = True

    # --- dati
    def set_pcb(self, doc):
        self.mode, self.pcb, self.sch = "pcb", doc, None
        self.hl_net = -1
        used = {t.layer for t in doc.tracks} | {a.layer for a in doc.arcs} | {f.layer for f in doc.fills} \
            | {r.layer for r in doc.regions} | {p.layer for p in doc.pads}
        self.visible = {n for n in used if n in (1, 32, 33, 34, 56, 74) or 2 <= n <= 31 or 39 <= n <= 54}
        if not self.visible:
            self.visible = set(used)
        self.all_layers = sorted(used, key=lambda n: (n not in (1, 32), n))
        self.fit()

    def set_sch(self, doc):
        self.mode, self.sch, self.pcb = "sch", doc, None
        self.fit()

    def bounds(self) -> QRectF:
        if self.mode == "pcb" and self.pcb:
            x0, y0, x1, y1 = self.pcb.bbox()
            return QRectF(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
        if self.mode == "sch" and self.sch:
            xs = [q[0] for pr in self.sch.prims for q in pr.pts]
            ys = [q[1] for pr in self.sch.prims for q in pr.pts]
            if xs:
                x0, x1, y0, y1 = max(min(xs), 0), min(max(xs), self.sch.sheet[0]), max(min(ys), 0), min(max(ys), self.sch.sheet[1])
                if x1 > x0 and y1 > y0:
                    return QRectF(x0 - 600, y0 - 600, x1 - x0 + 1200, y1 - y0 + 1200)
            return QRectF(-100, -100, self.sch.sheet[0] + 200, self.sch.sheet[1] + 200)
        return QRectF(0, 0, 1000, 1000)

    def fit(self):
        self._auto = True
        b = self.bounds()
        self.cx, self.cy = b.center().x(), b.center().y()
        self.scale = 0.92 * min(self.width() / b.width(), self.height() / b.height()) if self.width() > 10 else 0.1
        self.update()

    # --- trasformazioni
    def _xf(self, w=None, h=None) -> QTransform:
        t = QTransform()
        t.translate((w or self.width()) / 2, (h or self.height()) / 2)
        t.scale(-self.scale if self.mirror else self.scale, -self.scale)
        t.translate(-self.cx, -self.cy)
        return t

    def to_world(self, p: QPointF) -> QPointF:
        inv, _ = self._xf().inverted()
        return inv.map(p)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto and self.mode:
            self.fit()
            self._auto = True

    def wheelEvent(self, e):
        self._auto = False
        w0 = self.to_world(e.position())
        k = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        self.scale = min(max(self.scale * k, 0.005), 50)
        w1 = self.to_world(e.position())
        self.cx += w0.x() - w1.x()
        self.cy += w0.y() - w1.y()
        self.update()

    def mousePressEvent(self, e):
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._drag = (e.position(), self.cx, self.cy)
            self._auto = False

    def mouseReleaseEvent(self, e):
        moved = self._drag is not None and (e.position() - self._drag[0]).manhattanLength() > 4
        self._drag = None
        if e.button() == Qt.MouseButton.LeftButton and not moved:
            w = self.to_world(e.position())
            if self.measure_mode:
                self.meas = ([] if len(self.meas) >= 2 else self.meas) + [(w.x(), w.y())]
                if len(self.meas) == 2:
                    (x1, y1), (x2, y2) = self.meas
                    self.net_picked.emit(f"Distanza {self.meas_text()}   ΔX {self.to_unit(x2 - x1):.3f}  ΔY {self.to_unit(y2 - y1):.3f} {self.unit}")
                self.update()
            elif self.mode == "pcb" and self.pcb:
                self.pick_net(w)

    def pick_net(self, w: QPointF):
        """Seleziona la rete dell'elemento più vicino al punto (coordinate mil); clic nel vuoto = deseleziona."""
        tol = 6.0 / self.scale
        best, bd = -1, tol
        d = self.pcb
        vis = self.visible

        def seg(t):
            ax, ay, bx, by = t.x1, t.y1, t.x2, t.y2
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            k = 0 if L == 0 else max(0, min(1, ((w.x() - ax) * dx + (w.y() - ay) * dy) / L))
            return math.hypot(w.x() - (ax + k * dx), w.y() - (ay + k * dy)) - t.width / 2
        for t in d.tracks:
            if t.net >= 0 and t.layer in vis:
                v = seg(t)
                if v < bd: best, bd = t.net, v
        for p in d.pads:
            if p.net >= 0:
                v = math.hypot(w.x() - p.x, w.y() - p.y) - max(p.sx, p.sy) / 2
                if v < bd: best, bd = p.net, v
        for v_ in d.vias:
            if v_.net >= 0:
                v = math.hypot(w.x() - v_.x, w.y() - v_.y) - v_.dia / 2
                if v < bd: best, bd = v_.net, v
        self.hl_net = best
        name = d.nets[best] if 0 <= best < len(d.nets) else ""
        self.net_picked.emit(f"Rete: {name}" if name else "")
        self.update()

    def mouseMoveEvent(self, e):
        w = self.to_world(e.position())
        self.cursor_moved.emit(w.x(), w.y())
        if self._drag:
            p0, cx, cy = self._drag
            d = e.position() - p0
            self.cx = cx + (d.x() / self.scale) * (1 if self.mirror else -1)
            self.cy = cy + d.y() / self.scale
            self.update()

    def mouseDoubleClickEvent(self, e):
        self.fit()

    # --- disegno
    def paintEvent(self, _):
        p = QPainter(self)
        self._draw(p, self.width(), self.height(), self.scale)
        p.end()

    def _draw(self, p: QPainter, w: int, h: int, scale: float):
        old = self.scale
        self.scale = scale
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(0, 0, w, h, QColor("#000000" if self.mode in ("pcb", "gbr") else P.canvas))
            if self.mode in ("pcb", "sch", "gbr"):
                p.setTransform(self._xf(w, h))
                if self.mode == "pcb" and self.pcb:
                    self._paint_pcb(p)
                elif self.mode == "sch" and self.sch:
                    self._paint_sch(p)
                elif self.mode == "gbr":
                    self._paint_extra(p)
                self._paint_measure(p)
            else:
                p.setPen(QColor(P.text_dim))
                p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "Apri un file")
        finally:
            self.scale = old

    def _paint_extra(self, p):
        pass

    def _paint_measure(self, p: QPainter):
        pts = self.meas
        if not pts:
            return
        mpx = 1.0 / self.scale
        p.setPen(self._pen(QColor("#00FFFF"), 1.5 * mpx))
        for q in pts:
            p.drawEllipse(QPointF(*q), 4 * mpx, 4 * mpx)
        if len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
            p.setPen(QColor("#00FFFF"))
            self._text(p, (x1 + x2) / 2 + 6 * mpx, (y1 + y2) / 2 + 6 * mpx, self.meas_text(), 13 * mpx)

    def meas_text(self) -> str:
        if len(self.meas) != 2:
            return ""
        (x1, y1), (x2, y2) = self.meas
        d = math.hypot(x2 - x1, y2 - y1)
        return f"{self.to_unit(d):.3f} {self.unit}"

    def to_unit(self, v: float) -> float:
        """v nell'unità interna del documento -> unità di misura mostrata."""
        return v * 0.0254 if self.unit == "mm" else v

    # --- esportazione
    def export_png(self, path: str, w: int = 3000) -> bool:
        from PySide6.QtGui import QImage
        h = max(1, int(w * self.height() / max(1, self.width())))
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        p = QPainter(img)
        self._draw(p, w, h, self.scale * w / max(1, self.width()))
        p.end()
        return img.save(path)

    def export_pdf(self, path: str) -> bool:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
        pw = QPdfWriter(path)
        pw.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        pw.setPageOrientation(QPageLayout.Orientation.Landscape if self.width() >= self.height() else QPageLayout.Orientation.Portrait)
        pw.setPageMargins(QMarginsF(8, 8, 8, 8), QPageLayout.Unit.Millimeter)
        pw.setResolution(300)
        w, h = pw.width(), pw.height()
        p = QPainter(pw)
        self._draw(p, w, h, self.scale * min(w / max(1, self.width()), h / max(1, self.height())))
        p.end()
        return True

    _PENS: dict = {}

    @classmethod
    def _pen(cls, col, w, round_cap=True):
        w = max(float(w), 0.0)
        key = (QColor(col).rgba(), round(w, 2), round_cap)
        pen = cls._PENS.get(key)
        if pen is None:
            cap = Qt.PenCapStyle.RoundCap if round_cap else Qt.PenCapStyle.FlatCap
            pen = QPen(QBrush(QColor(col)), w, Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin)
            if len(cls._PENS) > 4000:
                cls._PENS.clear()
            cls._PENS[key] = pen
        return pen

    def _paint_pcb(self, p: QPainter):
        d = self.pcb
        vis = self.visible
        mpx = 1.0 / self.scale
        order = sorted(vis, key=lambda n: (0 if n in (32,) else 1 if 2 <= n <= 31 else 2 if n == 1 else 3 + (n in (33, 34))))
        # contorno scheda
        if d.outline:
            p.setPen(self._pen(QColor("#FFFFFF"), 8 * mpx if 8 * mpx > 8 else 8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            poly = QPolygonF([QPointF(x, y) for x, y in d.outline] + [QPointF(*d.outline[0])])
            p.drawPolyline(poly)
        for n in order:
            col = layer_color(n)
            if self.show_fills:
                fc = QColor(col); fc.setAlpha(110)
                for r in d.regions:
                    if r.layer == n and r.kind == 0 and len(r.pts) >= 3:
                        p.setPen(Qt.PenStyle.NoPen); p.setBrush(fc)
                        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in r.pts]))
                for f in d.fills:
                    if f.layer == n:
                        p.save(); p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
                        p.translate((f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2); p.rotate(f.rot)
                        p.drawRect(QRectF(-abs(f.x2 - f.x1) / 2, -abs(f.y2 - f.y1) / 2, abs(f.x2 - f.x1), abs(f.y2 - f.y1)))
                        p.restore()
            for t in d.tracks:
                if t.layer == n:
                    p.setPen(self._pen(col, t.width)); p.drawLine(QPointF(t.x1, t.y1), QPointF(t.x2, t.y2))
            for a in d.arcs:
                if a.layer == n:
                    pts = _arc_pts(a.cx, a.cy, a.r, a.a0, a.a1)
                    p.setPen(self._pen(col, a.width)); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPolyline(QPolygonF([QPointF(x, y) for x, y in pts]))
        if self.show_pads:
            for pd in d.pads:
                if pd.layer in (1, 32):
                    if pd.layer not in vis:
                        continue
                    col = layer_color(pd.layer)
                elif pd.layer == 74 or pd.layer in vis:
                    if not ({1, 32} & vis) and pd.layer not in vis:
                        continue
                    col = layer_color(1) if 1 in vis else layer_color(32)
                else:
                    continue
                self._pad(p, pd, col)
        if self.show_vias:
            for v in d.vias:
                if not ({1, 32} & vis) and not (vis & set(range(2, 32))):
                    continue
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#FFFFFF"))
                p.drawEllipse(QPointF(v.x, v.y), v.dia / 2, v.dia / 2)
                p.setBrush(QColor("#000000"))
                p.drawEllipse(QPointF(v.x, v.y), v.hole / 2, v.hole / 2)
        if self.hl_net >= 0:
            hc = QColor("#FFFFFF")
            for t in d.tracks:
                if t.net == self.hl_net and t.layer in vis:
                    p.setPen(self._pen(hc, t.width)); p.drawLine(QPointF(t.x1, t.y1), QPointF(t.x2, t.y2))
            for pd in d.pads:
                if pd.net == self.hl_net:
                    self._pad(p, pd, hc)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(hc)
            for v in d.vias:
                if v.net == self.hl_net:
                    p.drawEllipse(QPointF(v.x, v.y), v.dia / 2, v.dia / 2)
        if self.show_text:
            p.setFont(QFont("Consolas", 1)); f = p.font(); f.setPixelSize(40); p.setFont(f)
            p.setPen(QColor(layer_color(33)))
            if 33 in vis:
                for c in d.components:
                    if c.designator and c.layer.upper().startswith("TOP"):
                        self._text(p, c.x, c.y, c.designator, 28)
            if 34 in vis:
                p.setPen(QColor(layer_color(34)))
                for c in d.components:
                    if c.designator and c.layer.upper().startswith("BOTTOM"):
                        self._text(p, c.x, c.y, c.designator, 28)

    def _tw(self, s, h) -> float:
        from PySide6.QtGui import QFontMetricsF
        f = QFont("Consolas"); f.setPixelSize(max(1, int(h)))
        return QFontMetricsF(f).horizontalAdvance(s)

    def _text(self, p, x, y, s, h, rot=0, mirror_ok=True):
        p.save()
        p.translate(x, y)
        p.scale(-1 if self.mirror else 1, -1)
        p.rotate(-rot)
        f = p.font(); f.setPixelSize(max(1, int(h))); p.setFont(f)
        p.drawText(QPointF(0, 0), s)
        p.restore()

    def _pad(self, p, pd, col):
        p.save()
        p.translate(pd.x, pd.y); p.rotate(pd.rot)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(col)
        w, h = pd.sx, pd.sy
        r = QRectF(-w / 2, -h / 2, w, h)
        if pd.shape == 1:
            p.drawEllipse(r) if abs(w - h) < 0.01 else p.drawRoundedRect(r, min(w, h) / 2, min(w, h) / 2)
        elif pd.shape == 3:
            k = min(w, h) * 0.29
            p.drawPolygon(QPolygonF([QPointF(-w / 2 + k, -h / 2), QPointF(w / 2 - k, -h / 2), QPointF(w / 2, -h / 2 + k),
                                     QPointF(w / 2, h / 2 - k), QPointF(w / 2 - k, h / 2), QPointF(-w / 2 + k, h / 2),
                                     QPointF(-w / 2, h / 2 - k), QPointF(-w / 2, -h / 2 + k)]))
        elif pd.shape == 4:
            p.drawRoundedRect(r, min(w, h) * 0.25, min(w, h) * 0.25)
        else:
            p.drawRect(r)
        if pd.hole > 0:
            p.setBrush(QColor("#000000")); p.drawEllipse(QPointF(0, 0), pd.hole / 2, pd.hole / 2)
        p.restore()

    # ---- schemi
    def _paint_sch(self, p: QPainter):
        d = self.sch
        W, H = d.sheet
        p.setPen(self._pen(QColor(P.border), 8)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0, 0, W, H))
        TXT, DIM = QColor(P.text), QColor(P.text_dim)
        WIRE, BODY, PEAK = QColor("#00FF00"), QColor(P.peak), QColor(P.accent_hover)
        font = QFont("Consolas"); p.setFont(font)
        for pr in d.prims:
            k, pts = pr.kind, pr.pts
            if k == "wire":
                p.setPen(self._pen(WIRE, 8 * max(1, pr.width * 0.6)))
                p.drawPolyline(QPolygonF([QPointF(*q) for q in pts]))
            elif k == "line":
                p.setPen(self._pen(BODY, 6 * pr.width, False))
                p.drawPolyline(QPolygonF([QPointF(*q) for q in pts]))
            elif k == "poly":
                p.setPen(self._pen(BODY, 6)); p.setBrush(QColor(255, 176, 0, 70) if pr.extra.get("filled") else Qt.BrushStyle.NoBrush)
                p.drawPolygon(QPolygonF([QPointF(*q) for q in pts]))
            elif k == "rect":
                (x0, y0), (x1, y1) = pts
                p.setPen(self._pen(BODY, 6, False))
                p.setBrush(QColor(255, 176, 0, 45) if pr.extra.get("filled") else Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
            elif k == "junction":
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(WIRE); p.drawEllipse(QPointF(*pts[0]), 14, 14)
            elif k == "pin":
                (x0, y0), (x1, y1) = pts
                if pr.extra.get("hidden"):
                    continue
                p.setPen(self._pen(BODY, 6, False)); p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
                rot = pr.extra.get("rot", 0)
                dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][rot]
                if pr.extra.get("show_num") and pr.extra.get("num"):
                    p.setPen(DIM)
                    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                    nw = self._tw(pr.extra["num"], 50)
                    if dx:
                        self._text(p, mx - nw / 2, my + 14, pr.extra["num"], 50)
                    else:
                        self._text(p, mx - 14, my - nw / 2, pr.extra["num"], 50, 90)
                if pr.extra.get("show_name") and pr.text:
                    p.setPen(TXT)
                    tw = self._tw(pr.text, 60)
                    if rot == 0:
                        self._text(p, x1 + 20, y1 - 18, pr.text, 60)
                    elif rot == 2:
                        self._text(p, x1 - 20 - tw, y1 - 18, pr.text, 60)
                    elif rot == 1:
                        self._text(p, x1 - 18, y1 + 20, pr.text, 60, 90)
                    else:
                        self._text(p, x1 - 18, y1 - 20 - tw, pr.text, 60, 90)
            elif k in ("designator", "param", "label", "netlabel"):
                if not pr.text:
                    continue
                p.setPen(PEAK if k == "designator" else (QColor("#00FF00") if k == "netlabel" else (DIM if k == "param" else TXT)))
                self._text(p, pts[0][0], pts[0][1], pr.text, 70, pr.extra.get("rot", 0) * 90)
            elif k == "power":
                x, y = pts[0]
                rot = pr.extra.get("rot", 0)
                dx, dy = [(0, 1), (-1, 0), (0, -1), (1, 0)][rot % 4]
                p.setPen(self._pen(QColor("#FF0000"), 8, False))
                ex, ey = x + dx * 100, y + dy * 100
                p.drawLine(QPointF(x, y), QPointF(ex, ey))
                if pr.extra.get("style", 1) in (1, 2, 3, 0, 4):
                    px, py = -dy, dx
                    p.drawLine(QPointF(ex + px * 50, ey + py * 50), QPointF(ex - px * 50, ey - py * 50))
                p.setPen(QColor("#FF0000"))
                self._text(p, ex + dx * 20 + (20 if dx else -30), ey + dy * 20 + (-15 if dx else 0), pr.text, 60)
            elif k == "port":
                x, y = pts[0]
                w, h = pr.extra.get("w", 200), pr.extra.get("h", 100)
                p.setPen(self._pen(QColor("#FF0000"), 6, False)); p.setBrush(QColor(255, 106, 61, 40))
                p.drawRect(QRectF(x, y - h / 2, w, h))
                p.setPen(TXT); self._text(p, x + 15, y - 20, pr.text, 60)
            elif k in ("arc", "ellipse"):
                e = pr.extra; x, y = pts[0]
                p.setPen(self._pen(BODY, 6)); p.setBrush(QColor(255, 176, 0, 45) if e.get("filled") else Qt.BrushStyle.NoBrush)
                if k == "ellipse":
                    p.drawEllipse(QPointF(x, y), e["r"], e["r2"])
                else:
                    a0, a1 = e["a0"], e["a1"]
                    pp = _arc_pts(x, y, e["r"], a0, a1)
                    p.drawPolyline(QPolygonF([QPointF(*q) for q in pp]))
