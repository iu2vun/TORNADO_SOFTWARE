"""Stile dei grafici del tema HP 9845C: fondo nero, graticola blu, cornice bianca, tracce a colori puri.

    from tornado_ui.plot_style import TRACE_COLORS, PLOT, apply_matplotlib_style, style_pyqtgraph
"""
from __future__ import annotations

from .theme import P

# Colori delle tracce, in ordine d'uso (solo gli 8 colori puri, nessuna sfumatura).
TRACE_COLORS = (P.yellow, P.green, P.cyan, P.magenta, P.white, P.red)

PLOT = {
    "bg": P.canvas,
    "figure_bg": P.bg,
    "grid": P.blue,
    "grid_minor": P.blue,
    "axis": P.white,
    "text": P.white,
    "text_dim": P.cyan,
    "legend_bg": P.bg,
    "marker": P.white,
}


def apply_matplotlib_style() -> None:
    """Imposta rcParams di matplotlib sul tema HP 9845C."""
    import os

    import matplotlib as mpl
    from cycler import cycler
    from matplotlib import font_manager
    import glob
    from .theme import _assets_dir
    for fp in glob.glob(os.path.join(os.path.dirname(__file__), "fonts", "*.ttf")) + \
            glob.glob(os.path.join(_assets_dir(), "fonts", "*.ttf")):
        font_manager.fontManager.addfont(fp)

    mpl.rcParams.update({
        "figure.facecolor": PLOT["figure_bg"], "axes.facecolor": PLOT["bg"],
        "axes.edgecolor": PLOT["axis"], "axes.labelcolor": PLOT["text"],
        "axes.prop_cycle": cycler(color=list(TRACE_COLORS)),
        "axes.grid": True, "grid.color": PLOT["grid"], "grid.linestyle": (0, (2, 4)), "grid.linewidth": 0.8,
        "lines.linewidth": 1.4,
        "xtick.color": PLOT["text_dim"], "ytick.color": PLOT["text_dim"],
        "text.color": PLOT["text"], "legend.facecolor": PLOT["figure_bg"],
        "legend.edgecolor": PLOT["axis"], "savefig.facecolor": PLOT["figure_bg"],
        "font.family": ["VT323", "Courier New", "DejaVu Sans Mono"],
    })


def style_pyqtgraph(plot_widget) -> None:
    """Applica il tema a un pyqtgraph.PlotWidget."""
    plot_widget.setBackground(PLOT["bg"])
    item = plot_widget.getPlotItem()
    for ax in ("left", "bottom", "right", "top"):
        a = item.getAxis(ax)
        a.setPen(PLOT["axis"])
        a.setTextPen(PLOT["text_dim"])
    item.showGrid(x=True, y=True, alpha=0.6)
