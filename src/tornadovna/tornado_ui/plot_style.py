"""Stile dei grafici coerente col tema (valori ripresi da SParView, il riferimento per i plot).

    from tornado_ui.plot_style import TRACE_COLORS, PLOT, apply_matplotlib_style, style_pyqtgraph
"""
from __future__ import annotations

import os

from .theme import P

# Colori delle tracce, in ordine d'uso: la prima è sempre l'accento della suite.
TRACE_COLORS = (
    "#ffb000", "#ffe08a", "#b87800", "#ff9a00", "#ffd060", "#8a5a00",
    "#ffc933", "#ffefb8", "#cc8a00", "#e69500", "#fff4d6", "#996600",
)

PLOT = {
    "bg": P.canvas,          # fondo area di tracciamento
    "figure_bg": P.panel,    # fondo attorno al grafico
    "grid": "#2e1d00",
    "grid_minor": "#1a1100",
    "axis": "#b87800",
    "text": P.text,
    "text_dim": P.text_dim,
    "legend_bg": "#0f0802dd",
}


def apply_matplotlib_style() -> None:
    """Imposta rcParams di matplotlib sul tema scuro/arancione."""
    import matplotlib as mpl
    from cycler import cycler
    try:
        import glob
        from matplotlib import font_manager
        from .theme import _assets_dir
        for f in glob.glob(os.path.join(_assets_dir(), "fonts", "*.ttf")):
            font_manager.fontManager.addfont(f)
    except Exception:  # noqa: BLE001
        pass

    mpl.rcParams.update({
        "figure.facecolor": PLOT["figure_bg"], "axes.facecolor": PLOT["bg"],
        "axes.edgecolor": PLOT["axis"], "axes.labelcolor": PLOT["text"],
        "axes.prop_cycle": cycler(color=list(TRACE_COLORS)),
        "axes.grid": True, "grid.color": PLOT["grid"], "grid.linewidth": 0.6,
        "xtick.color": PLOT["text_dim"], "ytick.color": PLOT["text_dim"],
        "text.color": PLOT["text"], "legend.facecolor": PLOT["figure_bg"],
        "legend.edgecolor": PLOT["grid"], "savefig.facecolor": PLOT["figure_bg"],
        "font.family": ["VT323", "DejaVu Sans"], "font.size": 14,
    })


def style_pyqtgraph(plot_widget) -> None:
    """Applica il tema a un pyqtgraph.PlotWidget."""
    plot_widget.setBackground(PLOT["bg"])
    item = plot_widget.getPlotItem()
    for ax in ("left", "bottom", "right", "top"):
        a = item.getAxis(ax)
        a.setPen(PLOT["axis"])
        a.setTextPen(PLOT["text_dim"])
    item.showGrid(x=True, y=True, alpha=0.25)
