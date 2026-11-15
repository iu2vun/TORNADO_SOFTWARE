"""Classe base dei grafici: pittura su QWidget e su dispositivi di esportazione (PNG/SVG/PDF)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QMarginsF, QRect, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtWidgets import QWidget

from ..model import Workspace
from ..theme import DARK, LIGHT, Theme, qcolor


class PlotView(QWidget):
    hovered = Signal(str)
    viewChanged = Signal()

    supports_markers = False

    def __init__(self, workspace: Workspace, theme: Theme = DARK, parent=None):
        super().__init__(parent)
        self.ws = workspace
        self.theme = theme
        self.show_legend = True
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(240, 160)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    # ------------------------------------------------------------ API comune
    def refresh(self) -> None:
        self.invalidate()
        self.update()

    def invalidate(self) -> None:
        """Le sottoclassi svuotano le cache dei dati."""

    def set_theme(self, th: Theme) -> None:
        self.theme = th
        self.update()

    def draw(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        raise NotImplementedError

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.draw(p, QRectF(0, 0, self.width(), self.height()), self.theme, True)
        p.end()

    # ----------------------------------------------------------- esportazione
    def render_image(self, w: int, h: int, th: Theme = LIGHT, scale: float = 2.0) -> QImage:
        img = QImage(int(w * scale), int(h * scale), QImage.Format.Format_ARGB32)
        img.fill(qcolor(th.bg))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.scale(scale, scale)
        self.draw(p, QRectF(0, 0, w, h), th, False)
        p.end()
        return img

    def export_to(self, path: str, w: Optional[int] = None, h: Optional[int] = None,
                  th: Theme = LIGHT, scale: float = 2.0) -> str:
        w = w or max(self.width(), 900)
        h = h or max(self.height(), 600)
        suf = Path(path).suffix.lower()
        if suf == ".svg":
            from PySide6.QtSvg import QSvgGenerator
            g = QSvgGenerator()
            g.setFileName(path)
            g.setSize(QSize(w, h))
            g.setViewBox(QRect(0, 0, w, h))
            g.setTitle("SParView")
            p = QPainter(g)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(QRectF(0, 0, w, h), qcolor(th.bg))
            self.draw(p, QRectF(0, 0, w, h), th, False)
            p.end()
        elif suf == ".pdf":
            wr = QPdfWriter(path)
            wr.setResolution(96)
            wr.setPageSize(QPageSize(QSizeF(w / 96 * 25.4, h / 96 * 25.4), QPageSize.Unit.Millimeter))
            wr.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
            p = QPainter(wr)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(QRectF(0, 0, w, h), qcolor(th.bg))
            self.draw(p, QRectF(0, 0, w, h), th, False)
            p.end()
        else:
            img = self.render_image(w, h, th, scale)
            if not img.save(path):
                raise OSError(f"impossibile salvare {path}")
        return path
