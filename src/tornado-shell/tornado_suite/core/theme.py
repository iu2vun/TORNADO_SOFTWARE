"""Tema Tornado HP 9845C: palette a 8 colori puri + foglio di stile QSS globale.

Riferimento: schermo dell'HP 9845C (1980). Fondo nero, otto colori puri (nero, bianco,
giallo, verde, ciano, rosso, blu, magenta), niente grigi, sfumature o trasparenze nei
widget. Gli stati si distinguono per colore e per video inverso (sfondo bianco, testo nero).
Tutti i moduli attingono da qui (``P``) invece di hardcodare colori.

I nomi storici dei campi (accent, panel, border, ...) restano, cosi' i moduli scritti per il
vecchio tema continuano a funzionare con il nuovo aspetto.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

BLACK = "#000000"
WHITE = "#FFFFFF"
YELLOW = "#FFFF00"
GREEN = "#00FF00"
CYAN = "#00FFFF"
RED = "#FF0000"
BLUE = "#1010FF"
MAGENTA = "#FF00FF"
COLORS8 = (BLACK, WHITE, YELLOW, GREEN, CYAN, RED, BLUE, MAGENTA)


@dataclass(frozen=True)
class Palette:
    """Tema HP 9845C. Le tonalita' sono solo gli 8 colori puri dell'hardware."""
    # --- colori puri ---
    black: str = BLACK
    white: str = WHITE
    yellow: str = YELLOW
    green: str = GREEN
    cyan: str = CYAN
    red: str = RED
    blue: str = BLUE
    magenta: str = MAGENTA
    # --- ruoli (compatibili con il vecchio tema) ---
    bg: str = BLACK              # fondo
    panel: str = BLACK
    panel_alt: str = BLACK
    raised: str = BLACK
    border: str = BLUE           # divisori e griglie
    border_strong: str = WHITE   # cornici
    text: str = WHITE
    text_dim: str = CYAN         # etichette
    text_faint: str = BLUE
    peak: str = YELLOW           # valori, picchi
    accent: str = WHITE          # selezione = video inverso
    accent_hover: str = YELLOW
    accent_press: str = CYAN
    on_accent: str = BLACK       # testo su video inverso
    selection: str = WHITE
    danger: str = RED
    ok: str = GREEN
    page: str = WHITE            # eccezione: pagina bianca per anteprime di stampa
    page_text: str = BLACK
    canvas: str = BLACK


P = Palette()
FONT_FAMILY = '"VT323", "Courier New", monospace'
MONO_FAMILY = '"VT323", "Courier New", monospace'


def qcolor(c: str) -> QColor:
    if c.startswith("rgba("):
        r, g, b, a = [float(x) for x in c[5:-1].split(",")]
        col = QColor(int(r), int(g), int(b))
        col.setAlpha(int(a if a > 1 else a * 255))
        return col
    return QColor(c)


def _icon_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "tornado_ui_icons")
    os.makedirs(d, exist_ok=True)
    return d


def _make_indicators() -> dict[str, str]:
    """PNG per spunte e frecce usate dal QSS (che non puo' disegnarle da se')."""
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
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)   # pixel netti, come sul CRT
        return pm, p

    pm, p = canvas()
    p.setPen(QPen(QColor(P.on_accent), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
    p.drawPolyline(QPolygonF([QPointF(7, 17), QPointF(13.5, 23), QPointF(25, 9)]))
    p.end()
    save("check", pm)

    for name, col, up in (("down", P.cyan, False), ("up", P.cyan, True)):
        pm, p = canvas()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(col))
        pts = [QPointF(8, 20), QPointF(24, 20), QPointF(16, 11)] if up else [QPointF(8, 12), QPointF(24, 12), QPointF(16, 21)]
        p.drawPolygon(QPolygonF(pts))
        p.end()
        save("arrow_" + name, pm)
    for name, col in (("close", P.cyan), ("close_hover", P.yellow)):
        pm, p = canvas()
        p.setPen(QPen(QColor(col), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        p.drawLine(QPointF(10, 10), QPointF(22, 22))
        p.drawLine(QPointF(22, 10), QPointF(10, 22))
        p.end()
        save(name, pm)
    return out


def _cset(k: str, d):
    try:
        from .settings import settings
        v = settings().value(k, d)
        return d if v in (None, "") else v
    except Exception:  # noqa: BLE001
        return d


def _font_family() -> str:
    f = str(_cset("ui/font", ""))
    return f'"{f}", {FONT_FAMILY}' if f else FONT_FAMILY


def _font_px() -> int:
    return int(_cset("ui/font_px", 19))


def _clock_family() -> str:
    f = str(_cset("ui/clock_font", ""))
    return (f'"{f}", ' if f else "") + '"VT323", "Courier New", monospace'


def _clock_px() -> int:
    return int(_cset("ui/clock_px", 68))


def build_qss() -> str:
    ic = _make_indicators()
    return f"""
* {{ outline: 0; }}
QWidget {{
    background: {P.bg}; color: {P.text};
    font-family: {_font_family()}; font-size: {_font_px()}px;
}}
QMainWindow, QDialog {{ background: {P.bg}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {P.bg}; color: {P.yellow}; border: 1px solid {P.white}; padding: 2px 6px; }}

/* --- menu --- */
QMenuBar {{ background: {P.bg}; border-bottom: 1px solid {P.white}; }}
QMenuBar::item {{ padding: 3px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {P.white}; color: {P.black}; }}
QMenu {{ background: {P.bg}; border: 1px solid {P.white}; padding: 2px; }}
QMenu::item {{ padding: 3px 26px 3px 18px; }}
QMenu::item:selected {{ background: {P.white}; color: {P.black}; }}
QMenu::item:disabled {{ color: {P.blue}; }}
QMenu::separator {{ height: 1px; background: {P.blue}; margin: 3px 4px; }}

/* --- toolbar / bottoni --- */
QToolBar {{ background: {P.bg}; border: none; border-bottom: 1px solid {P.blue}; padding: 2px 4px; spacing: 4px; }}
QToolBar::separator {{ width: 1px; background: {P.blue}; margin: 3px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; padding: 3px 7px; }}
QToolButton:hover {{ border-color: {P.white}; }}
QToolButton:pressed {{ background: {P.white}; color: {P.black}; }}
QToolButton:checked {{ background: {P.blue}; border: 1px solid {P.white}; }}
QToolButton:disabled {{ color: {P.blue}; }}
QToolButton::menu-indicator {{ image: url({ic['arrow_down']}); subcontrol-position: right bottom; width: 10px; }}
QPushButton {{ background: {P.bg}; border: 1px solid {P.white}; padding: 3px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {P.white}; color: {P.black}; }}
QPushButton:pressed {{ background: {P.yellow}; color: {P.black}; border-color: {P.yellow}; }}
QPushButton:disabled {{ color: {P.blue}; border-color: {P.blue}; }}
QPushButton[primary="true"] {{ background: {P.white}; color: {P.black}; }}
QPushButton[primary="true"]:hover {{ background: {P.yellow}; border-color: {P.yellow}; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[flat="true"]:hover {{ background: {P.white}; color: {P.black}; }}
QPushButton#SoftKey {{ background: {P.white}; color: {P.black}; border: 1px solid {P.black}; padding: 1px 2px; min-height: 28px; }}
QPushButton#SoftKey:hover {{ background: {P.yellow}; }}
QPushButton#SoftKey:pressed {{ background: {P.cyan}; }}
QPushButton#SoftKey:disabled {{ background: {P.bg}; color: {P.bg}; border: 1px solid {P.bg}; }}

/* --- input --- */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {P.bg}; border: 1px solid {P.blue}; padding: 2px 6px; color: {P.yellow};
    selection-background-color: {P.white}; selection-color: {P.black};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {P.white}; }}
QPlainTextEdit, QTextEdit, QTextBrowser {{ padding: 4px; color: {P.green}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({ic['arrow_down']}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {P.bg}; border: 1px solid {P.white}; selection-background-color: {P.white}; selection-color: {P.black}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({ic['arrow_up']}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({ic['arrow_down']}); width: 10px; height: 10px; }}
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {P.white}; background: {P.bg}; }}
QCheckBox::indicator:hover {{ border-color: {P.yellow}; }}
QCheckBox::indicator:checked {{ background: {P.white}; image: url({ic['check']}); }}
QRadioButton::indicator {{ width: 13px; height: 13px; border: 1px solid {P.white}; border-radius: 7px; background: {P.bg}; }}
QRadioButton::indicator:checked {{ background: {P.white}; }}
QGroupBox {{ border: 1px solid {P.blue}; margin-top: 12px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {P.cyan}; }}

/* --- slider / progress --- */
QSlider::groove:horizontal {{ height: 1px; background: {P.blue}; }}
QSlider::sub-page:horizontal {{ background: {P.cyan}; }}
QSlider::handle:horizontal {{ background: {P.white}; width: 8px; height: 16px; margin: -8px 0; }}
QSlider::handle:horizontal:hover {{ background: {P.yellow}; }}
QProgressBar {{ background: {P.bg}; border: 1px solid {P.white}; text-align: center; color: {P.white}; }}
QProgressBar::chunk {{ background: {P.blue}; }}

/* --- viste --- */
QTreeView, QListView, QListWidget, QTableView, QTableWidget, QTreeWidget {{
    background: {P.bg}; alternate-background-color: {P.bg}; border: 1px solid {P.blue};
    gridline-color: {P.blue}; selection-background-color: {P.white}; selection-color: {P.black};
}}
QTreeView::item, QListView::item {{ padding: 1px 2px; }}
QTreeView::item:selected, QListView::item:selected {{ background: {P.white}; color: {P.black}; }}
QTreeView::item:hover, QListView::item:hover {{ color: {P.yellow}; }}
QHeaderView::section {{ background: {P.bg}; color: {P.cyan}; border: none; border-right: 1px solid {P.blue}; border-bottom: 1px solid {P.white}; padding: 2px 8px; }}
QTableCornerButton::section {{ background: {P.bg}; border: none; }}

/* --- scrollbar --- */
QScrollBar:vertical {{ background: {P.bg}; width: 10px; margin: 0; border-left: 1px solid {P.blue}; }}
QScrollBar:horizontal {{ background: {P.bg}; height: 10px; margin: 0; border-top: 1px solid {P.blue}; }}
QScrollBar::handle {{ background: {P.white}; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {P.yellow}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --- tab --- */
QTabWidget::pane {{ border: 1px solid {P.blue}; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {P.cyan}; padding: 3px 14px; border: none; }}
QTabBar::tab:hover {{ color: {P.yellow}; }}
QTabBar::tab:selected {{ background: {P.white}; color: {P.black}; }}
QTabBar#DocTabs::tab {{ background: {P.bg}; border: 1px solid {P.blue}; border-bottom: none; margin-right: 2px; padding: 3px 12px; min-width: 90px; }}
QTabBar#DocTabs::tab:selected {{ background: {P.white}; color: {P.black}; }}
QTabBar::close-button {{ image: url({ic['close']}); subcontrol-position: right; margin: 2px; }}
QTabBar::close-button:hover {{ image: url({ic['close_hover']}); }}

/* --- splitter / dock / status --- */
QSplitter::handle {{ background: {P.blue}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {P.white}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {P.bg}; color: {P.cyan}; padding: 3px 8px; border-bottom: 1px solid {P.white}; }}
QStatusBar {{ background: {P.bg}; border-top: 1px solid {P.white}; color: {P.green}; }}
QStatusBar::item {{ border: none; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {P.blue}; }}

/* --- componenti suite --- */
QFrame#Card {{ background: {P.bg}; border: 1px solid {P.blue}; }}
QFrame#Card:hover {{ border-color: {P.white}; }}
QFrame#Panel {{ background: {P.bg}; border: 1px solid {P.blue}; }}
QLabel#H1 {{ font-size: 26px; color: {P.white}; }}
QLabel#H2 {{ font-size: 19px; color: {P.yellow}; }}
QLabel#Dim {{ color: {P.cyan}; }}
QLabel#Value {{ color: {P.yellow}; }}
QLabel#Ok {{ color: {P.green}; }}
QLabel#Err {{ color: {P.red}; }}
QLabel#Clock {{ font-family: {_clock_family()}; font-size: {_clock_px()}px; color: {P.yellow}; }}
QLabel#ClockSec {{ font-size: 22px; color: {P.white}; }}
QWidget#TopBar {{ background: {P.bg}; border-bottom: 1px solid {P.white}; }}
QWidget#Rail {{ background: {P.bg}; border-left: 1px solid {P.blue}; }}
QToolButton#RailBtn {{ padding: 8px; }}
QToolButton#RailBtn:checked {{ background: {P.blue}; border: 1px solid {P.white}; }}
QWidget#SoftKeyBar {{ background: {P.bg}; border-top: 1px solid {P.white}; }}
QWidget#Ribbon {{ background: {P.bg}; border-bottom: 1px solid {P.blue}; }}
QTabBar#RibbonTabs {{ background: {P.bg}; }}
QTabBar#RibbonTabs::tab {{ padding: 3px 12px; color: {P.cyan}; }}
QTabBar#RibbonTabs::tab:selected {{ background: {P.white}; color: {P.black}; }}
QFrame#RibbonGroup {{ background: transparent; border: none; border-right: 1px solid {P.blue}; }}
QLabel#RibbonGroupTitle {{ color: {P.cyan}; font-size: 14px; }}
QToolButton#RibbonBtn {{ padding: 3px 5px; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: P.bg, QPalette.ColorRole.WindowText: P.text,
        QPalette.ColorRole.Base: P.bg, QPalette.ColorRole.AlternateBase: P.bg,
        QPalette.ColorRole.Text: P.text, QPalette.ColorRole.Button: P.bg,
        QPalette.ColorRole.ButtonText: P.text, QPalette.ColorRole.Highlight: P.white,
        QPalette.ColorRole.HighlightedText: P.black, QPalette.ColorRole.ToolTipBase: P.bg,
        QPalette.ColorRole.ToolTipText: P.yellow, QPalette.ColorRole.PlaceholderText: P.cyan,
        QPalette.ColorRole.Link: P.cyan, QPalette.ColorRole.BrightText: P.white,
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    load_fonts()
    f = QFont("VT323", 14)
    f.setStyleStrategy(QFont.StyleStrategy.NoAntialias)      # bordi netti da matrice di punti
    app.setFont(f)
    app.setStyleSheet(build_qss())


# ---------------------------------------------------------------- compatibilita' col vecchio tema
def _assets_dir() -> str:
    """Cartella assets dell'app (sorgenti o eseguibile PyInstaller)."""
    import sys
    cands = [os.path.join(getattr(sys, "_MEIPASS", ""), "assets")]
    here = os.path.dirname(os.path.abspath(__file__))
    d = here
    for _ in range(4):
        cands.append(os.path.join(d, "assets"))
        d = os.path.dirname(d)
    for c in cands:
        if os.path.isdir(os.path.join(c, "fonts")):
            return c
    return os.path.join(here, "assets")


def load_fonts() -> bool:
    """Registra VT323 (fonts/ accanto al tema oppure assets/fonts dell'app)."""
    import glob
    from PySide6.QtGui import QFontDatabase
    dirs = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"), os.path.join(_assets_dir(), "fonts")]
    ok = False
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*.ttf")):
            ok = QFontDatabase.addApplicationFont(f) >= 0 or ok
        if ok:
            break
    return ok


def install_crt(host):
    """Effetto CRT sopra `host` (stessa firma del vecchio tema)."""
    from .crt import CrtOverlay
    ov = CrtOverlay(host)
    host._crt_overlay = ov
    return ov
