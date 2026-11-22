"""Effetto CRT del tema HP 9845C: sfocatura, alone, scanline e vignettatura sopra la finestra.

``CrtOverlay(parent)`` e' un widget trasparente al mouse. A intervalli regolari cattura
l'immagine del genitore (``grab``), ne ricava due copie sfocate a bassa risoluzione e le
ricompone sopra l'interfaccia nitida:

* sfocatura: copia leggermente sfocata sovrapposta (sfumatura dei pixel di fosforo);
* alone: copia molto sfocata in somma (``Plus``), che fa brillare le parti luminose;
* scanline: righe scure orizzontali;
* vignettatura: scurimento radiale verso gli angoli.

Valori di default scelti da Riccardo il 22/11/2026: sfocatura 0,35 - alone 0,9 - scanline 0,0
(spente) - angoli 0,5. Chiavi in ``settings()``: crt/on, crt/blur (0..1,5), crt/glow (0..1),
crt/scan (0..1), crt/vignette (0..1), crt/fps.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

DEFAULTS = {"crt/on": True, "crt/blur": 0.35, "crt/glow": 0.9, "crt/scan": 0.0, "crt/vignette": 0.5, "crt/fps": 12}


def _cfg(key: str):
    d = DEFAULTS[key]
    try:
        from .settings import settings
        v = settings().value(key, d)
        if v in (None, ""):
            return d
        if isinstance(d, bool):
            return str(v).lower() in ("1", "true", "yes")
        return type(d)(float(v)) if not isinstance(d, bool) else v
    except Exception:  # noqa: BLE001
        return d


class CrtOverlay(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._grabbing = False
        self._soft: QPixmap | None = None
        self._bloom: QPixmap | None = None
        self.on = True
        self.blur = 0.35
        self.glow = 0.9
        self.scan = 0.0
        self.vignette = 0.5
        self.reload()
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.raise_()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(int(1000 / max(1, int(_cfg("crt/fps")))))

    # --- parametri ---
    def reload(self) -> None:
        self.on = bool(_cfg("crt/on"))
        self.blur = float(_cfg("crt/blur"))
        self.glow = float(_cfg("crt/glow"))
        self.scan = float(_cfg("crt/scan"))
        self.vignette = float(_cfg("crt/vignette"))
        self.setVisible(self.on)
        self.update()

    def set_params(self, *, blur=None, glow=None, scan=None, vignette=None, on=None) -> None:
        if blur is not None:
            self.blur = float(blur)
        if glow is not None:
            self.glow = float(glow)
        if scan is not None:
            self.scan = float(scan)
        if vignette is not None:
            self.vignette = float(vignette)
        if on is not None:
            self.on = bool(on)
            self.setVisible(self.on)
        self.update()

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.parent() and ev.type().name in ("Resize", "Show", "LayoutRequest", "ChildAdded"):
            self.setGeometry(self.parent().rect())
            self.raise_()
        return False

    # --- cattura e sfocatura ---
    def _refresh(self) -> None:
        par = self.parent()
        if not self.on or not par.isVisible() or par.width() < 8 or par.height() < 8:
            return
        if self.blur <= 0 and self.glow <= 0:
            self._soft = self._bloom = None
            return
        self._grabbing = True
        try:
            img = par.grab().toImage()
        finally:
            self._grabbing = False
        w, h = img.width(), img.height()
        smooth = Qt.TransformationMode.SmoothTransformation
        ign = Qt.AspectRatioMode.IgnoreAspectRatio
        self._soft = QPixmap.fromImage(img.scaled(max(1, w // 2), max(1, h // 2), ign, smooth)) if self.blur > 0 else None
        self._bloom = QPixmap.fromImage(img.scaled(max(1, w // 6), max(1, h // 6), ign, smooth)) if self.glow > 0 else None
        self.update()

    # --- disegno ---
    def paintEvent(self, _):  # noqa: N802
        if self._grabbing or not self.on:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = QRectF(self.rect())
        if self._soft is not None and self.blur > 0:
            p.setOpacity(min(0.9, self.blur))
            p.drawPixmap(r, self._soft, QRectF(self._soft.rect()))
        if self._bloom is not None and self.glow > 0:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.setOpacity(min(1.0, self.glow * 0.6))
            p.drawPixmap(r, self._bloom, QRectF(self._bloom.rect()))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setOpacity(1.0)
        if self.scan > 0:
            p.setPen(QColor(0, 0, 0, int(min(1.0, self.scan) * 150)))
            for y in range(1, self.height(), 3):
                p.drawLine(0, y, self.width(), y)
        if self.vignette > 0:
            g = QRadialGradient(self.rect().center(), max(self.width(), self.height()) * 0.72)
            g.setColorAt(0.55, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, int(min(1.0, self.vignette) * 200)))
            p.fillRect(self.rect(), g)
        p.end()


def glow(widget: QWidget, blur: int = 8) -> None:
    """Alone giallo su titoli, valori e marker (da usare con parsimonia)."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    e = QGraphicsDropShadowEffect(widget)
    e.setBlurRadius(blur)
    e.setOffset(0, 0)
    e.setColor(QColor("#FFFF00"))
    widget.setGraphicsEffect(e)
