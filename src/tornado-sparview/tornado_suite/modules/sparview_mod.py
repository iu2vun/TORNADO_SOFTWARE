from ..core.registry import ModuleSpec, register
from .base import embed


def _factory():
    from ..apps.sparview.gui.mainwindow import MainWindow

    class SParViewModulo(MainWindow):
        def apri_file(self, path: str) -> None:
            self.open_paths([path])

    return embed(SParViewModulo())


register(ModuleSpec("sparview", "SParView", "Visualizzatore e analisi di S-parametri (S1P…S4P, CTI, CSV)", "sparam",
                    "Strumenti RF", _factory, (".s1p", ".s2p", ".s3p", ".s4p", ".s5p", ".s6p", ".cti"), multi=False, ordine=40))
