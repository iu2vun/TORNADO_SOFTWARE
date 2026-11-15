"""Stile dei grafici coerente col tema (valori ripresi da SParView, il riferimento per i plot).

    from tornado_ui.plot_style import TRACE_COLORS, PLOT, apply_matplotlib_style, style_pyqtgraph
"""
from __future__ import annotations

from .theme import P

# Colori delle tracce, in ordine d'uso: la prima è sempre l'accento della suite.
TRACE_COLORS = (
    "#ff8a1f", "#35c6e8", "#a6e22e", "#f55bb4", "#ffd23f", "#5b8def",
    "#ff5555", "#5fe3a1", "#b69cff", "#e8e8e8", "#c98a4b", "#7fd6ff",
)

PLOT = {
    "bg": P.canvas,          # fondo area di tracciamento
    "figure_bg": P.panel,    # fondo attorno al grafico
    "grid": "#2a2f37",
    "grid_minor": "#1c2026",
    "axis": "#6b7280",
    "text": P.text,
    "text_dim": P.text_dim,
    "legend_bg": "#171a1fdd",
}


def apply_matplotlib_style() -> None:
    """Imposta rcParams di matplotlib sul tema scuro/arancione."""
    import matplotlib as mpl
    from cycler import cycler

    mpl.rcParams.update({
        "figure.facecolor": PLOT["figure_bg"], "axes.facecolor": PLOT["bg"],
        "axes.edgecolor": PLOT["axis"], "axes.labelcolor": PLOT["text"],
        "axes.prop_cycle": cycler(color=list(TRACE_COLORS)),
        "axes.grid": True, "grid.color": PLOT["grid"], "grid.linewidth": 0.6,
        "xtick.color": PLOT["text_dim"], "ytick.color": PLOT["text_dim"],
        "text.color": PLOT["text"], "legend.facecolor": PLOT["figure_bg"],
        "legend.edgecolor": PLOT["grid"], "savefig.facecolor": PLOT["figure_bg"],
        "font.family": ["Bahnschrift", "Segoe UI", "DejaVu Sans"],
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
