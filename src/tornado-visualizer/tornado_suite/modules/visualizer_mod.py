from ..core.registry import ModuleSpec, register
from .base import embed


def _factory():
    from ..apps.visualizer.mainwindow import MainWindow
    return embed(MainWindow())


register(ModuleSpec("visualizer", "Tornado Visualizer", "Visualizzatore di PCB e schemi Altium (.PcbDoc/.SchDoc) e di Gerber/Excellon: layer, reti, misure, PNG/PDF",
                    "pic", "Strumenti RF", _factory,
                    (".pcbdoc", ".schdoc", ".gbr", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp", ".gm1", ".gko", ".drl", ".xln"),
                    multi=True, ordine=45))
