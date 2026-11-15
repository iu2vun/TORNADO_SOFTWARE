"""Marchio Tornado Laboratori: logo completo e icona (solo fulmine), arancione su nero."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

from .logo_data import BOLT, LOGO_H, LOGO_W, TEXT

ORANGE = "#ff8a1f"
BLACK = "#000000"


def _path(polys) -> QPainterPath:
    p = QPainterPath()
    p.setFillRule(Qt.OddEvenFill)
    for poly in polys:
        p.moveTo(*poly[0])
        for x, y in poly[1:]:
            p.lineTo(x, y)
        p.closeSubpath()
    return p


def _fit(painter, path: QPainterPath, w: int, h: int, margin: float):
    b = path.boundingRect()
    s = min((w * (1 - 2 * margin)) / b.width(), (h * (1 - 2 * margin)) / b.height())
    painter.translate(w / 2 - b.center().x() * s, h / 2 - b.center().y() * s)
    painter.scale(s, s)


def bolt_pixmap(size: int = 256, bg: str = BLACK, fg: str = ORANGE, radius: float = 0.18) -> QPixmap:
    """Icona dell'app: solo il fulmine, arancione su nero (angoli arrotondati)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(bg))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * radius, size * radius)
    p.setBrush(QColor(fg))
    _fit(p, _path([BOLT]), size, size, 0.17)
    p.drawPath(_path([BOLT]))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(bolt_pixmap(s))
    return ic


def logo_pixmap(width: int = 360, bg: str = BLACK, fg: str = ORANGE) -> QPixmap:
    """Logo completo (fulmine + TORNADO + LABORATORI), arancione su nero."""
    h = int(width * LOGO_H / LOGO_W) + int(width * 0.12)
    pm = QPixmap(width, h)
    pm.fill(QColor(bg))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(fg))
    full = _path([BOLT] + TEXT)
    _fit(p, full, width, h, 0.06)
    p.drawPath(_path([BOLT]))
    p.drawPath(_path(TEXT))
    p.end()
    return pm
