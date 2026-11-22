"""Tema, colori e font condivisi dalle viste."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

CHANNEL_COLORS = ["#FFFFFF", "#FFFF00", "#00FFFF", "#FFFFFF", "#FFFF00", "#00FFFF", "#FFFF00", "#FFFF00"]
_MONO_FAMILIES = ("VT323", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo", "Liberation Mono")


def mono_font(point_size: int = 0) -> QFont:
    available = set(QFontDatabase.families())
    for fam in _MONO_FAMILIES:
        if fam in available:
            f = QFont(fam)
            break
    else:
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setStyleHint(QFont.StyleHint.Monospace)
    if point_size:
        f.setPointSize(point_size)
    elif f.family() == "VT323":
        f.setPixelSize(19)
    return f


def is_dark(pal: QPalette) -> bool:
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def colors_for(pal: QPalette) -> dict[str, QColor]:
    dark = is_dark(pal)
    return {
        "rx": QColor("#FFFFFF") if dark else QColor("#00FF00"),
        "tx": QColor("#FFFF00") if dark else QColor("#1010FF"),
        "event": QColor("#00FFFF") if dark else QColor("#FFFFFF"),
        "warn": QColor("#FFFF00") if dark else QColor("#FF0000"),
        "grid": QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 40),
        "lane": QColor(255, 255, 255, 10) if dark else QColor(0, 0, 0, 10),
        "text": pal.color(QPalette.ColorRole.Text),
        "base": pal.color(QPalette.ColorRole.Base),
    }


def channel_color(idx: int) -> QColor:
    return QColor(CHANNEL_COLORS[idx % len(CHANNEL_COLORS)])


def _dark_palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#000000", QPalette.ColorRole.WindowText: "#FFFFFF",
        QPalette.ColorRole.Base: "#000000", QPalette.ColorRole.AlternateBase: "#000000",
        QPalette.ColorRole.Text: "#FFFFFF", QPalette.ColorRole.Button: "#000000",
        QPalette.ColorRole.ButtonText: "#FFFFFF", QPalette.ColorRole.Highlight: "#1010FF",
        QPalette.ColorRole.HighlightedText: "#FFFFFF", QPalette.ColorRole.ToolTipBase: "#000000",
        QPalette.ColorRole.ToolTipText: "#FFFFFF", QPalette.ColorRole.Mid: "#000000",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    return pal


def apply_theme(app: QApplication, mode: str = "dark") -> None:
    """Tema HP 9845C: stile e tavolozza arrivano dal tema della suite; da solo (senza suite) palette scura Fusion."""
    app.setStyle("Fusion")
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        hints.setColorScheme(Qt.ColorScheme.Dark)
    app.setPalette(_dark_palette())
