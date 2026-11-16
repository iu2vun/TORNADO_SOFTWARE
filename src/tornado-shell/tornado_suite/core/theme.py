"""Tema Sodium Vapor della suite: monitor ambra monocromatico (stile HP 8563 / IFR COM120).

Palette unica + foglio di stile QSS globale + font VT323 incorporato + overlay
scanline/vignettatura (``install_crt``). Tutti i moduli attingono da qui (``P``)
invece di hardcodare colori. Backup del tema precedente: theme.py.bak_pre_sodium.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QPainter, QPalette, QPen, QPixmap, QPolygonF,
                           QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class Palette:
    bg: str = "#0a0600"          # fondo applicazione (nero fosforo)
    panel: str = "#0f0802"       # pannelli
    panel_alt: str = "#140b02"   # righe alternate / ribbon
    raised: str = "#1b1004"      # controlli in rilievo
    border: str = "#4a2f00"
    text: str = "#ffb000"        # fosforo ambra
    text_dim: str = "#b87800"    # etichette
    accent: str = "#ffb000"      # accento = fosforo pieno (video inverso)
    accent_hover: str = "#ffd060"
    accent_press: str = "#b87800"
    on_accent: str = "#0a0600"
    selection: str = "rgba(255,176,0,60)"
    peak: str = "#ffd060"        # picchi: titoli, valori, marker
    danger: str = "#ffd060"      # monocromatico: l'errore si legge dal testo (ERR, !)
    ok: str = "#ffb000"
    page: str = "#ffffff"        # foglio documento (Word/PDF)
    page_text: str = "#1b1b1b"
    canvas: str = "#050300"      # fondo area di lavoro (dietro ai fogli)


P = Palette()
FONT_FAMILY = '"VT323", "Courier New", "Consolas", monospace'
MONO_FAMILY = '"Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace'


def qcolor(c: str) -> QColor:
    if c.startswith("rgba("):
        r, g, b, a = [float(x) for x in c[5:-1].split(",")]
        col = QColor(int(r), int(g), int(b))
        col.setAlpha(int(a if a > 1 else a * 255))
        return col
    return QColor(c)


def _icon_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "tornado_suite_icons")
    os.makedirs(d, exist_ok=True)
    return d


def _make_indicators() -> dict[str, str]:
    """PNG per spunte e frecce usate dal QSS (che non può disegnarle da sé)."""
    d = _icon_dir()
    out: dict[str, str] = {}
    s = 32

    def save(name: str, pm: QPixmap) -> None:
        p = os.path.join(d, name + ".png")
        pm.save(p, "PNG")
        out[name] = p.replace("\\", "/")

    def canvas() -> tuple[QPixmap, QPainter]:
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        return pm, p

    pm, p = canvas()
    p.setPen(QPen(QColor(P.on_accent), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline(QPolygonF([QPointF(7, 17), QPointF(13.5, 23), QPointF(25, 9)]))
    p.end()
    save("check", pm)

    for name, col, up in (("down", P.text_dim, False), ("up", P.text_dim, True)):
        pm, p = canvas()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        pts = [QPointF(8, 20), QPointF(24, 20), QPointF(16, 11)] if up else [QPointF(8, 12), QPointF(24, 12), QPointF(16, 21)]
        p.drawPolygon(QPolygonF(pts))
        p.end()
        save("arrow_" + name, pm)
    for name, col in (("close", P.text_dim), ("close_hover", P.accent)):
        pm, p = canvas()
        p.setPen(QPen(QColor(col), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(10, 10), QPointF(22, 22))
        p.drawLine(QPointF(22, 10), QPointF(10, 22))
        p.end()
        save(name, pm)
    return out


def _font_family() -> str:
    try:
        from .registry import settings
        f = str(settings().value("ui/font", "") or "")
        if f:
            return f'"{f}", {FONT_FAMILY}'
    except Exception:  # noqa: BLE001
        pass
    return FONT_FAMILY


def _font_px() -> int:
    try:
        from .registry import settings
        return int(settings().value("ui/font_px", 19))
    except Exception:  # noqa: BLE001
        return 19


def _cset(k: str, d):
    try:
        from .registry import settings
        v = settings().value(k, d)
        return d if v in (None, "") else v
    except Exception:  # noqa: BLE001
        return d


def _clock_family() -> str:
    f = str(_cset("ui/clock_font", ""))
    return (f'"{f}", ' if f else "") + '"VT323", "Courier New", monospace'


def _clock_px() -> int:
    return int(_cset("ui/clock_px", 96))


def _clock_weight() -> int:
    return int(_cset("ui/clock_weight", 400))


def _square(qss: str) -> str:
    """Strumento da banco: spigoli vivi (esclusi radio e maniglie dello slider, che restano tondi)."""
    out = []
    for ln in qss.split("\n"):
        if "QRadioButton::indicator" not in ln and "QSlider::handle" not in ln:
            ln = re.sub(r"border-(top-left-|top-right-|bottom-left-|bottom-right-)?radius:\s*\d+px", lambda m: m.group(0).split(":")[0] + ": 0", ln)
        out.append(ln)
    return "\n".join(out)


def build_qss() -> str:
    ic = _make_indicators()
    return _square(f"""
* {{ outline: 0; }}
QWidget {{
    background: {P.bg}; color: {P.text};
    font-family: {_font_family()}; font-size: {_font_px()}px;
}}
QMainWindow, QDialog {{ background: {P.bg}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {P.raised}; color: {P.text}; border: 1px solid {P.accent}; padding: 4px 6px; }}

/* --- menu --- */
QMenuBar {{ background: {P.panel}; border-bottom: 1px solid {P.border}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {P.raised}; color: {P.accent}; }}
QMenu {{ background: {P.panel}; border: 1px solid {P.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {P.accent}; color: {P.on_accent}; }}
QMenu::item:disabled {{ color: {P.text_dim}; }}
QMenu::separator {{ height: 1px; background: {P.border}; margin: 4px 8px; }}

/* --- toolbar / bottoni --- */
QToolBar {{ background: {P.panel}; border: none; border-bottom: 1px solid {P.border}; padding: 3px 6px; spacing: 4px; }}
QToolBar::separator {{ width: 1px; background: {P.border}; margin: 4px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 4px 7px; }}
QToolButton:hover {{ background: {P.raised}; border-color: {P.border}; }}
QToolButton:pressed, QToolButton:checked {{ background: {P.selection}; border-color: {P.accent}; color: {P.accent}; }}
QToolButton:disabled {{ color: {P.text_dim}; }}
QToolButton::menu-indicator {{ image: url({ic['arrow_down']}); subcontrol-position: right bottom; width: 10px; }}
QPushButton {{ background: {P.raised}; border: 1px solid {P.border}; border-radius: 5px; padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {P.accent}; }}
QPushButton:pressed {{ background: {P.accent_press}; color: {P.on_accent}; }}
QPushButton:disabled {{ color: {P.text_dim}; background: {P.panel}; }}
QPushButton[primary="true"] {{ background: {P.accent}; color: {P.on_accent}; border-color: {P.accent}; font-weight: 400; }}
QPushButton[primary="true"]:hover {{ background: {P.accent_hover}; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[flat="true"]:hover {{ background: {P.raised}; }}

/* --- input --- */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {P.panel}; border: 1px solid {P.border}; border-radius: 5px; padding: 4px 7px;
    selection-background-color: {P.accent}; selection-color: {P.on_accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {P.accent}; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: 6px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({ic['arrow_down']}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {P.panel}; border: 1px solid {P.border}; selection-background-color: {P.accent}; selection-color: {P.on_accent}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({ic['arrow_up']}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({ic['arrow_down']}); width: 10px; height: 10px; }}
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {P.text_dim}; border-radius: 3px; background: {P.panel}; }}
QCheckBox::indicator:hover {{ border-color: {P.accent}; }}
QCheckBox::indicator:checked {{ background: {P.accent}; border-color: {P.accent}; image: url({ic['check']}); }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {P.text_dim}; border-radius: 8px; background: {P.panel}; }}
QRadioButton::indicator:checked {{ background: {P.accent}; border-color: {P.accent}; }}
QGroupBox {{ border: 1px solid {P.border}; border-radius: 6px; margin-top: 12px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {P.peak}; }}

/* --- slider / progress --- */
QSlider::groove:horizontal {{ height: 3px; background: {P.border}; border-radius: 1px; }}
QSlider::sub-page:horizontal {{ background: {P.accent}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {P.text}; width: 13px; height: 13px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {P.accent}; }}
QProgressBar {{ background: {P.panel}; border: 1px solid {P.border}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {P.accent}; border-radius: 3px; }}

/* --- viste --- */
QTreeView, QListView, QListWidget, QTableView, QTableWidget, QTreeWidget {{
    background: {P.panel}; alternate-background-color: {P.panel_alt}; border: 1px solid {P.border};
    gridline-color: {P.border}; selection-background-color: {P.selection}; selection-color: {P.text};
}}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:selected, QListView::item:selected {{ background: {P.selection}; color: {P.accent}; }}
QTreeView::item:hover, QListView::item:hover {{ background: {P.raised}; }}
QHeaderView::section {{ background: {P.panel_alt}; color: {P.text_dim}; border: none; border-right: 1px solid {P.border}; border-bottom: 1px solid {P.border}; padding: 4px 8px; }}
QTableCornerButton::section {{ background: {P.panel_alt}; border: none; }}

/* --- scrollbar --- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: {P.border}; border-radius: 5px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {P.text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --- tab --- */
QTabWidget::pane {{ border: 1px solid {P.border}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {P.text_dim}; padding: 7px 14px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {P.text}; }}
QTabBar::tab:selected {{ color: {P.accent}; border-bottom: 2px solid {P.accent}; }}
QTabBar#DocTabs::tab {{ background: {P.panel}; border: 1px solid {P.border}; border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px; margin-right: 2px; padding: 6px 12px; min-width: 90px; }}
QTabBar#DocTabs::tab:selected {{ background: {P.bg}; color: {P.text}; border-top: 2px solid {P.accent}; }}
QTabBar::close-button {{ image: url({ic['close']}); subcontrol-position: right; margin: 2px; }}
QTabBar::close-button:hover {{ image: url({ic['close_hover']}); }}

/* --- splitter / dock / status --- */
QSplitter::handle {{ background: {P.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {P.accent}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {P.panel}; padding: 6px 8px; border-bottom: 1px solid {P.border}; }}
QStatusBar {{ background: {P.panel}; border-top: 1px solid {P.border}; color: {P.text_dim}; }}
QStatusBar::item {{ border: none; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {P.border}; }}

/* --- componenti suite --- */
QFrame#Card {{ background: {P.panel}; border: 1px solid {P.border}; border-radius: 10px; }}
QFrame#Card:hover {{ border-color: {P.accent}; }}
QFrame#Panel {{ background: {P.panel}; border: 1px solid {P.border}; border-radius: 10px; }}
QLabel#H1 {{ font-size: 32px; font-weight: 400; color: {P.peak}; }}
QLabel#H2 {{ font-size: 22px; font-weight: 400; color: {P.peak}; letter-spacing: 1px; }}
QLabel#Dim {{ color: {P.text_dim}; }}
QLabel#Clock {{ font-family: {_clock_family()}; font-size: {_clock_px()}px; font-weight: {_clock_weight()}; color: {P.text}; }}
QLabel#ClockSec {{ font-size: 30px; color: {P.accent}; }}
QWidget#TopBar {{ background: {P.panel}; border-bottom: 1px solid {P.border}; }}
QWidget#Rail {{ background: {P.panel}; border-left: 1px solid {P.border}; }}
QToolButton#RailBtn {{ border-radius: 8px; padding: 8px; }}
QToolButton#RailBtn:checked {{ background: {P.selection}; border: 1px solid {P.accent}; }}
QWidget#Ribbon {{ background: {P.panel_alt}; border-bottom: 1px solid {P.border}; }}
QTabBar#RibbonTabs {{ background: {P.panel_alt}; }}
QTabBar#RibbonTabs::tab {{ padding: 6px 12px; color: {P.text}; }}
QTabBar#RibbonTabs::tab:selected {{ color: {P.accent}; border-bottom: 2px solid {P.accent}; }}
QFrame#RibbonGroup {{ background: transparent; border: none; border-right: 1px solid {P.border}; }}
QLabel#RibbonGroupTitle {{ color: {P.text_dim}; font-size: 16px; }}
QToolButton#RibbonBtn {{ padding: 3px 5px; border-radius: 4px; }}
""")


def _assets_dir() -> str:
    """Cartella assets: nell'exe PyInstaller sta in _MEIPASS, da sorgenti si risale fino a trovare assets/fonts."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets")
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        if os.path.isdir(os.path.join(d, "assets", "fonts")):
            return os.path.join(d, "assets")
        d = os.path.dirname(d)
    return os.path.join(d, "assets")


def load_fonts() -> bool:
    """Registra i font incorporati (assets/fonts/*.ttf). Mai scaricati dal web."""
    d = os.path.join(_assets_dir(), "fonts")
    ok = False
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".ttf", ".otf")):
                ok |= QFontDatabase.addApplicationFont(os.path.join(d, f)) >= 0
    return ok


class CrtOverlay(QWidget):
    """Scanline (riga scura ogni 3 px) + vignettatura sopra la finestra. Trasparente al mouse."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        tile = QPixmap(1, 3)
        tile.fill(Qt.GlobalColor.transparent)
        p = QPainter(tile)
        p.fillRect(0, 2, 1, 1, QColor(0, 0, 0, 72))
        p.end()
        self._tile = tile

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.drawTiledPixmap(self.rect(), self._tile)
        g = QRadialGradient(self.rect().center(), max(self.width(), self.height()) * 0.75)
        g.setColorAt(0.0, QColor(0, 0, 0, 0))
        g.setColorAt(0.7, QColor(0, 0, 0, 0))
        g.setColorAt(1.0, QColor(0, 0, 0, 150))
        p.fillRect(self.rect(), g)
        p.end()


class _CrtFilter(QObject):
    def __init__(self, host: QWidget, ov: CrtOverlay) -> None:
        super().__init__(host)
        self.host, self.ov = host, ov

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.host and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.ChildAdded,
                                              QEvent.Type.LayoutRequest):
            self.ov.setGeometry(self.host.rect())
            self.ov.raise_()
        return False


def install_crt(host: QWidget) -> CrtOverlay:
    """Applica scanline e vignettatura a una finestra/widget (sempre attive: fa parte del tema)."""
    ov = CrtOverlay(host)
    ov.setGeometry(host.rect())
    host.installEventFilter(_CrtFilter(host, ov))
    ov.raise_()
    ov.show()
    return ov


def apply_theme(app: QApplication) -> None:
    load_fonts()
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: P.bg, QPalette.ColorRole.WindowText: P.text,
        QPalette.ColorRole.Base: P.panel, QPalette.ColorRole.AlternateBase: P.panel_alt,
        QPalette.ColorRole.Text: P.text, QPalette.ColorRole.Button: P.raised,
        QPalette.ColorRole.ButtonText: P.text, QPalette.ColorRole.Highlight: P.accent,
        QPalette.ColorRole.HighlightedText: P.on_accent, QPalette.ColorRole.ToolTipBase: P.raised,
        QPalette.ColorRole.ToolTipText: P.text, QPalette.ColorRole.PlaceholderText: P.text_dim,
        QPalette.ColorRole.Link: P.accent, QPalette.ColorRole.BrightText: P.peak,
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    f = QFont("VT323")
    f.setPixelSize(19)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(f)
    app.setStyleSheet(build_qss())
