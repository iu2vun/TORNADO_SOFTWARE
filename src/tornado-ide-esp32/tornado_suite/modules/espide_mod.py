"""IDE ESP32 / Arduino con motore PlatformIO (progetti, compila/carica, errori cliccabili, monitor seriale)."""
from ..apps.pio_ide import icons_ext  # noqa: F401  (registra le icone prima che la dashboard le usi)
from ..core.registry import ModuleSpec, register


def _factory():
    from ..apps.pio_ide.window import EspIde
    return EspIde()


register(ModuleSpec("espide", "IDE ESP32/Arduino", "Progetti PlatformIO per ESP32 e Arduino: compila, carica, errori cliccabili, monitor seriale",
                    "chip", "Sviluppo", _factory, (".ino",), multi=False, ordine=12))
