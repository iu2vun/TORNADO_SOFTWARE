"""tornado_ui: tema e componenti grafici della Tornado Suite, riusabili in qualunque app PySide6.

Copiare la cartella ``tornado_ui`` accanto al progetto. Uso minimo:

    from tornado_ui import P, apply_theme, icon, Card, h2, dim, Ribbon, run_app
"""
from .app import run_app
from .commands import COMMANDS, Command
from .icons import accent_icon, icon, pixmap, register_icons
from .plot_style import PLOT, TRACE_COLORS, apply_matplotlib_style, style_pyqtgraph
from .ribbon import Ribbon, RibbonGroup, RibbonPage, placeholder_group
from .settings import set_settings_provider, settings
from .theme import FONT_FAMILY, MONO_FAMILY, P, apply_theme, build_qss, qcolor
from .widgets import Card, CommandPalette, IconRail, ZoomBar, dim, h2, shortcut

__all__ = [
    "run_app", "COMMANDS", "Command", "accent_icon", "icon", "pixmap", "register_icons",
    "PLOT", "TRACE_COLORS", "apply_matplotlib_style", "style_pyqtgraph",
    "Ribbon", "RibbonGroup", "RibbonPage", "placeholder_group", "set_settings_provider", "settings",
    "FONT_FAMILY", "MONO_FAMILY", "P", "apply_theme", "build_qss", "qcolor",
    "Card", "CommandPalette", "IconRail", "ZoomBar", "dim", "h2", "shortcut",
]
