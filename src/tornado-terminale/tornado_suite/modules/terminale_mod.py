from ..core.registry import ModuleSpec, register
from .base import embed


def _factory():
    from ..apps.tornado_term.app import MainWindow
    return embed(MainWindow())


register(ModuleSpec("terminale", "Terminale", "Terminale seriale ANSI/VT100 a due sessioni (Tornado-Term)", "terminale",
                    "Strumenti RF", _factory, (), multi=True, ordine=42))
