"""Tema, colori e font condivisi dalle viste."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

CHANNEL_COLORS = ["#ffb000", "#ffe08a", "#b87800", "#ff9a00", "#ffd060", "#cc8a00", "#ffc933", "#fff4d6"]
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
        "rx": QColor("#ffb000") if dark else QColor("#1b7a2f"),
        "tx": QColor("#ffe08a") if dark else QColor("#0b57b8"),
        "event": QColor("#b87800") if dark else QColor("#6b7075"),
        "warn": QColor("#ffd060") if dark else QColor("#b45f00"),
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
        QPalette.ColorRole.Window: "#2b2d30", QPalette.ColorRole.WindowText: "#dfe1e5",
        QPalette.ColorRole.Base: "#1e1f22", QPalette.ColorRole.AlternateBase: "#2b2d30",
        QPalette.ColorRole.Text: "#dfe1e5", QPalette.ColorRole.Button: "#393b40",
        QPalette.ColorRole.ButtonText: "#dfe1e5", QPalette.ColorRole.Highlight: "#2f65ca",
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.ToolTipBase: "#393b40",
        QPalette.ColorRole.ToolTipText: "#dfe1e5", QPalette.ColorRole.Mid: "#4a4d51",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    return pal


def apply_theme(app: QApplication, mode: str) -> None:
    """mode: 'system' | 'light' | 'dark'."""
    app.setStyle("Fusion")
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        scheme = {"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(mode, Qt.ColorScheme.Unknown)
        hints.setColorScheme(scheme)
    elif mode == "dark":
        app.setPalette(_dark_palette())
    else:
        app.setPalette(app.style().standardPalette())
