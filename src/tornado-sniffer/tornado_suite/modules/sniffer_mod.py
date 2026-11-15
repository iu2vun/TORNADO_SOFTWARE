from ..core.registry import ModuleSpec, register
from .base import embed


def _factory():
    from ..apps.serialsniffer.gui.main_window import MainWindow
    return embed(MainWindow())


register(ModuleSpec("sniffer", "Serial Sniffer", "Sniffer seriale a mosaico: registrazione, riproduzione, decodifica", "sniffer",
                    "Strumenti RF", _factory, (".ssn",), multi=False, ordine=41))
