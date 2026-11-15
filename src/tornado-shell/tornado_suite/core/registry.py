"""Registro dei moduli (plugin) e dei comandi globali, impostazioni e percorsi dati."""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWidget

# --------------------------------------------------------------------- percorsi


def data_dir() -> Path:
    """Cartella dati: accanto all'exe se portatile (marker 'portable' o frozen), altrimenti per-utente."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "portable").exists():             # modalità portatile: dati accanto all'exe
            base = exe_dir / "TornadoSuite_data"
        else:
            base = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "TornadoSuite"
    else:
        here = Path(__file__).resolve().parents[2]
        if (here / "portable").exists():
            base = here / "TornadoSuite_data"
        else:
            root = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
            base = Path(root) / "TornadoSuite"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings() -> QSettings:
    return QSettings(str(data_dir() / "settings.ini"), QSettings.Format.IniFormat)


# ----------------------------------------------------------------------- moduli


@dataclass
class ModuleSpec:
    id: str
    nome: str
    descrizione: str
    icona: str
    categoria: str                       # "Documenti" | "Strumenti RF" | "Sviluppo" | "Sistema"
    factory: Callable[[], QWidget]
    estensioni: tuple[str, ...] = ()     # estensioni che il modulo sa aprire (".pdf", ...)
    multi: bool = False                  # più istanze (una scheda per documento)
    ordine: int = 100


MODULES: dict[str, ModuleSpec] = {}
LOAD_ERRORS: dict[str, str] = {}


def register(spec: ModuleSpec) -> ModuleSpec:
    MODULES[spec.id] = spec
    return spec


def discover() -> None:
    """Importa tutti i moduli di ``tornado_suite.modules``; ognuno chiama register()."""
    from .. import modules as pkg

    from ..modules._manifest import MODULE_NAMES
    names = list(MODULE_NAMES)
    try:                                                  # moduli aggiunti a mano e non ancora in manifest (sviluppo)
        names += [i.name for i in pkgutil.iter_modules(pkg.__path__) if i.name not in names]
    except Exception:  # noqa: BLE001
        pass
    for short in names:
        if short.startswith("_") or short in ("base", "editor", "office_common", "excel_engine", "word_docx"):
            continue
        name = f"{pkg.__name__}.{short}"
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - un modulo rotto non deve fermare la suite
            LOAD_ERRORS[short] = traceback.format_exc()


def modules_sorted() -> list[ModuleSpec]:
    return sorted(MODULES.values(), key=lambda m: (m.ordine, m.nome))


def module_for_path(path: str) -> Optional[ModuleSpec]:
    ext = os.path.splitext(path)[1].lower()
    for m in modules_sorted():
        if ext in m.estensioni:
            return m
    return None


# ---------------------------------------------------------------------- comandi


@dataclass
class Command:
    id: str
    titolo: str
    callback: Callable[[], None]
    categoria: str = ""
    scorciatoia: str = ""
    icona: str = ""


class CommandRegistry:
    """Comandi globali (palette Ctrl+K). I moduli possono aggiungerne propri a runtime."""

    def __init__(self) -> None:
        self._cmds: dict[str, Command] = {}

    def add(self, cmd: Command) -> None:
        self._cmds[cmd.id] = cmd

    def remove_prefix(self, prefix: str) -> None:
        for k in [k for k in self._cmds if k.startswith(prefix)]:
            del self._cmds[k]

    def all(self) -> list[Command]:
        return list(self._cmds.values())


COMMANDS = CommandRegistry()
