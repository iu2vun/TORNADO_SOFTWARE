"""Tema HP 9845C (nero, 8 colori puri) + tema chiaro per le esportazioni (QSS + palette grafici) e icone generate a runtime."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    panel: str
    panel_alt: str
    border: str
    text: str
    text_dim: str
    accent: str
    accent_hover: str
    accent_press: str
    on_accent: str
    plot_bg: str
    grid: str
    grid_minor: str
    axis: str
    legend_bg: str
    selection: str
    traces: tuple = field(default_factory=tuple)


TRACE_COLORS_DARK = (
    "#FFFF00", "#00FFFF", "#FFFFFF", "#00FF00", "#FF00FF", "#FF0000",
    "#FFFF00", "#00FFFF", "#FFFFFF", "#00FF00", "#FF00FF", "#FF0000",
)
TRACE_COLORS_LIGHT = (
    "#d95f00", "#0077b6", "#4d8f00", "#c2185b", "#b58900", "#3a56c8",
    "#c62828", "#00875a", "#6a4fc4", "#303030", "#8a5a2b", "#0097a7",
)

DARK = Theme(
    name="dark", bg="#000000", panel="#000000", panel_alt="#000000", border="#1010FF",
    text="#FFFFFF", text_dim="#00FFFF", accent="#FFFFFF", accent_hover="#FFFF00",
    accent_press="#00FFFF", on_accent="#000000", plot_bg="#000000", grid="#1010FF",
    grid_minor="#1010FF", axis="#00FFFF", legend_bg="#000000dd", selection="#FFFF0033",
    traces=TRACE_COLORS_DARK,
)

LIGHT = Theme(
    name="light", bg="#ffffff", panel="#f4f4f4", panel_alt="#ececec", border="#bdbdbd",
    text="#202020", text_dim="#606060", accent="#d95f00", accent_hover="#f07000",
    accent_press="#b84f00", on_accent="#ffffff", plot_bg="#ffffff", grid="#d0d0d0",
    grid_minor="#ececec", axis="#303030", legend_bg="#ffffffe0", selection="#d95f0033",
    traces=TRACE_COLORS_LIGHT,
)


def qcolor(c: str) -> QColor:
    """Colore da '#rrggbb' o '#rrggbbaa' (alpha in coda, come CSS)."""
    if len(c) == 9 and c.startswith("#"):
        col = QColor(c[:7])
        col.setAlpha(int(c[7:9], 16))
        return col
    return QColor(c)


def _rgba(c: str) -> str:
    """'#rrggbbaa' -> 'rgba(r,g,b,a)' (il QSS interpreta #8 cifre come aarrggbb)."""
    if len(c) == 9 and c.startswith("#"):
        return f"rgba({int(c[1:3], 16)},{int(c[3:5], 16)},{int(c[5:7], 16)},{int(c[7:9], 16)})"
    return c


def _icon_dir() -> str:
    d = os.path.join(tempfile.gettempdir(), "sparview_icons")
    os.makedirs(d, exist_ok=True)
    return d


def _make_icons(th: Theme) -> dict[str, str]:
    """Genera PNG per indicatori (spunta, frecce) e ritorna i percorsi (slash '/')."""
    d = _icon_dir()
    out: dict[str, str] = {}

    def save(name: str, pm: QPixmap) -> None:
        p = os.path.join(d, f"{th.name}_{name}.png")
        pm.save(p, "PNG")
        out[name] = p.replace("\\", "/")

    s = 32
    # spunta su accento
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(th.on_accent), 4.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(7, 17), QPointF(13.5, 23), QPointF(25, 9)]))
    p.end()
    save("check", pm)

    def arrow(name: str, color: str, up: bool):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        if up:
            poly = QPolygonF([QPointF(8, 20), QPointF(24, 20), QPointF(16, 10)])
        else:
            poly = QPolygonF([QPointF(8, 12), QPointF(24, 12), QPointF(16, 22)])
        p.drawPolygon(poly)
        p.end()
        save(name, pm)

    arrow("down", th.text_dim, False)
    arrow("down_hot", th.accent, False)
    arrow("up", th.text_dim, True)
    arrow("up_hot", th.accent, True)

    def branch(name: str, color: str, open_: bool):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        if open_:
            poly = QPolygonF([QPointF(8, 12), QPointF(24, 12), QPointF(16, 22)])
        else:
            poly = QPolygonF([QPointF(12, 8), QPointF(12, 24), QPointF(22, 16)])
        p.drawPolygon(poly)
        p.end()
        save(name, pm)

    branch("branch_closed", th.text_dim, False)
    branch("branch_open", th.accent, True)
    return out


def build_qss(th: Theme = DARK) -> str:
    ic = _make_icons(th)
    return f"""
* {{ outline: 0; }}
QWidget {{ background: {th.bg}; color: {th.text}; font-size: 9pt; }}
QMainWindow, QDialog {{ background: {th.bg}; }}
QToolTip {{ background: {th.panel_alt}; color: {th.text}; border: 1px solid {th.accent}; padding: 4px; }}
QLabel {{ background: transparent; }}
QLabel[dim="true"] {{ color: {th.text_dim}; }}
QLabel[title="true"] {{ color: {th.accent}; font-weight: 600; }}

QMenuBar {{ background: {th.panel}; border-bottom: 1px solid {th.border}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {th.panel_alt}; color: {th.accent}; }}
QMenu {{ background: {th.panel}; border: 1px solid {th.border}; padding: 4px; }}
QMenu::item {{ padding: 5px 26px 5px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: {th.accent}; color: {th.on_accent}; }}
QMenu::item:disabled {{ color: {th.text_dim}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {th.border}; margin: 4px 6px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; border: 1px solid {th.border}; border-radius: 3px; background: {th.panel_alt}; }}
QMenu::indicator:checked {{ background: {th.accent}; image: url({ic['check']}); border-color: {th.accent}; }}

QToolBar {{ background: {th.panel}; border: none; border-bottom: 1px solid {th.border}; spacing: 4px; padding: 3px; }}
QToolBar::separator {{ width: 1px; background: {th.border}; margin: 4px 4px; }}
QStatusBar {{ background: {th.panel}; border-top: 1px solid {th.border}; color: {th.text_dim}; }}
QStatusBar::item {{ border: none; }}

QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {th.panel}; padding: 6px 8px; border-bottom: 2px solid {th.accent}; text-align: left; font-weight: 600; }}
QMainWindow::separator {{ background: {th.border}; width: 2px; height: 2px; }}
QMainWindow::separator:hover {{ background: {th.accent}; }}
QSplitter::handle {{ background: {th.border}; }}
QSplitter::handle:hover {{ background: {th.accent}; }}

QTabWidget::pane {{ border: 1px solid {th.border}; background: {th.panel}; top: -1px; }}
QTabBar::tab {{ background: {th.bg}; color: {th.text_dim}; padding: 6px 12px; border: 1px solid {th.border}; border-bottom: none; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; }}
QTabBar::tab:selected {{ background: {th.panel}; color: {th.accent}; border-bottom: 2px solid {th.accent}; }}
QTabBar::tab:hover:!selected {{ color: {th.text}; background: {th.panel_alt}; }}

QPushButton, QToolButton {{ background: {th.panel_alt}; border: 1px solid {th.border}; border-radius: 4px; padding: 5px 12px; color: {th.text}; }}
QToolButton {{ padding: 4px 8px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {th.accent}; color: {th.accent_hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {th.accent_press}; color: {th.on_accent}; }}
QPushButton:checked, QToolButton:checked {{ background: {th.accent}; color: {th.on_accent}; border-color: {th.accent}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {th.text_dim}; border-color: {th.border}; background: {th.panel}; }}
QPushButton[accent="true"] {{ background: {th.accent}; color: {th.on_accent}; border-color: {th.accent}; font-weight: 600; }}
QPushButton[accent="true"]:hover {{ background: {th.accent_hover}; color: {th.on_accent}; }}
QPushButton[accent="true"]:pressed {{ background: {th.accent_press}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ background: {th.plot_bg}; border: 1px solid {th.border}; border-radius: 4px; padding: 3px 6px; selection-background-color: {th.accent}; selection-color: {th.on_accent}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {th.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {th.text_dim}; background: {th.panel}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 16px; border: none; background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 16px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({ic['up']}); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({ic['down']}); width: 12px; height: 12px; }}
QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{ image: url({ic['up_hot']}); }}
QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{ image: url({ic['down_hot']}); }}
QComboBox {{ padding-right: 20px; min-height: 18px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 20px; border: none; }}
QComboBox::down-arrow {{ image: url({ic['down']}); width: 14px; height: 14px; }}
QComboBox::down-arrow:on, QComboBox::down-arrow:hover {{ image: url({ic['down_hot']}); }}
QComboBox QAbstractItemView {{ background: {th.panel}; border: 1px solid {th.accent}; selection-background-color: {th.accent}; selection-color: {th.on_accent}; outline: 0; padding: 2px; }}

QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid {th.border}; background: {th.plot_bg}; }}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {th.accent}; }}
QCheckBox::indicator:checked {{ background: {th.accent}; border-color: {th.accent}; image: url({ic['check']}); }}
QRadioButton::indicator:checked {{ background: {th.accent}; border: 3px solid {th.plot_bg}; outline: 1px solid {th.accent}; }}
QCheckBox::indicator:disabled {{ background: {th.panel}; }}

QGroupBox {{ border: 1px solid {th.border}; border-radius: 5px; margin-top: 12px; padding: 10px 6px 6px 6px; background: transparent; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {th.accent}; font-weight: 600; }}

QTreeView, QTreeWidget, QTableView, QTableWidget, QListView, QListWidget {{ background: {th.plot_bg}; alternate-background-color: {th.panel}; border: 1px solid {th.border}; gridline-color: {th.border}; selection-background-color: {_rgba(th.selection)}; selection-color: {th.text}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {_rgba(th.selection)}; color: {th.accent_hover}; border-left: 2px solid {th.accent}; }}
QTreeView::item:hover:!selected, QListView::item:hover:!selected {{ background: {th.panel_alt}; }}
QTreeView::branch:has-children:closed {{ image: url({ic['branch_closed']}); }}
QTreeView::branch:has-children:open {{ image: url({ic['branch_open']}); }}
QTreeView::indicator, QListView::indicator, QTableView::indicator {{ width: 14px; height: 14px; border: 1px solid {th.border}; border-radius: 3px; background: {th.plot_bg}; }}
QTreeView::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked {{ background: {th.accent}; border-color: {th.accent}; image: url({ic['check']}); }}
QHeaderView::section {{ background: {th.panel}; color: {th.accent}; border: none; border-right: 1px solid {th.border}; border-bottom: 1px solid {th.border}; padding: 4px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {th.panel}; border: none; }}

QScrollBar:vertical {{ background: {th.bg}; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: {th.bg}; height: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {th.border}; min-height: 28px; border-radius: 5px; margin: 1px; }}
QScrollBar::handle:horizontal {{ background: {th.border}; min-width: 28px; border-radius: 5px; margin: 1px; }}
QScrollBar::handle:hover {{ background: {th.accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {th.border}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {th.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {th.accent}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QProgressBar {{ background: {th.plot_bg}; border: 1px solid {th.border}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {th.accent}; border-radius: 3px; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {th.border}; }}
PlotPanel {{ border: 1px solid {th.border}; }}
"""


def apply_theme(app: QApplication, th: Theme = DARK) -> None:
    app.setStyle("Fusion")
    f = QFont("VT323", 13)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(f)
    app.setStyleSheet(build_qss(th))
