"""Classe base dei grafici: pittura su QWidget e su dispositivi di esportazione (PNG/SVG/PDF)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QMarginsF, QRect, QRectF, QSize, QSizeF, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPixmap
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
        self._static: Optional[QPixmap] = None      # strato statico (griglia + tracce) in cache
        self._static_dirty = True
        # qualità adattiva: durante pan/zoom/ridimensionamento (raffica di aggiornamenti) si disegna senza
        # antialiasing e con meno vertici; a raffica finita arriva un ridisegno a piena qualità
        self._last_update = 0.0
        self._fast_until = 0.0
        self._fast_render = False        # vero solo mentre si costruisce lo strato statico in modo veloce
        self._was_fast = False
        self._last_render_s = 0.0        # durata dell'ultimo ridisegno dello strato statico
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(180)
        self._settle.timeout.connect(self._settled)

    # ------------------------------------------------------------ API comune
    def refresh(self) -> None:
        self.invalidate()
        self.update()

    def invalidate(self) -> None:
        """Le sottoclassi svuotano le cache dei dati."""

    def update(self, *args) -> None:
        """Invalida lo strato statico (griglia, tracce) e ridisegna tutto."""
        now = time.monotonic()
        gap = 0.25 + self._last_render_s           # se il ridisegno e' lento, gli eventi arrivano piu' radi
        if now - self._last_update < gap:
            self._fast_until = now + gap
        self._last_update = now
        self._static_dirty = True
        super().update(*args)

    def _settled(self) -> None:
        if self._was_fast:
            self._fast_until = 0.0
            self._static_dirty = True
            super().update()

    def update_overlay(self) -> None:
        """Ridisegna solo lo strato dinamico (marker, cursore, legenda): niente ricalcolo delle tracce."""
        super().update()

    def set_theme(self, th: Theme) -> None:
        self.theme = th
        self.update()

    def draw_static(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        """Sfondo, griglia, tracce: cambia solo con dati/vista/tema (viene messo in cache)."""
        raise NotImplementedError

    def draw_dynamic(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        """Marker, cursore, selezione, legenda: ridisegnato a ogni evento, deve essere leggero."""

    def draw(self, p: QPainter, rect: QRectF, th: Theme, interactive: bool = True) -> None:
        self.draw_static(p, rect, th, interactive)
        self.draw_dynamic(p, rect, th, interactive)

    def _static_layer(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        w, h = self.width(), self.height()
        pw, ph = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
        pm = self._static
        resized = pm is not None and (pm.width() != pw or pm.height() != ph)
        if pm is None or self._static_dirty or resized:
            fast = resized or time.monotonic() < self._fast_until
            pm = QPixmap(pw, ph)
            pm.setDevicePixelRatio(dpr)
            pm.fill(qcolor(self.theme.bg))
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing, not fast)
            q.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            self._fast_render = fast
            t_start = time.monotonic()
            try:
                self.draw_static(q, QRectF(0, 0, w, h), self.theme, True)
            finally:
                self._fast_render = False
                q.end()
            self._static = pm
            self._static_dirty = False
            self._was_fast = fast
            if not fast:
                self._last_render_s = time.monotonic() - t_start
            if fast:
                self._settle.start()
        return pm

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static_layer())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.draw_dynamic(p, QRectF(0, 0, self.width(), self.height()), self.theme, True)
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
