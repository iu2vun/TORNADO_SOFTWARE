"""Decoder di protocollo: Modbus RTU, NMEA 0183, comandi AT. Estendibili con plugin Python."""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Optional, Union

from .at import AtLines
from .auto import AutoDetect
from .base import Decoder, DecoderEngine, Message
from .grbl import Grbl
from .mavlink import Mavlink
from .modbus import ModbusAscii, ModbusRtu, append_crc, crc16, crc_ok
from .nmea import Nmea, checksum, with_checksum
from .sbus import Sbus
from .ubx import Ubx

DECODERS: dict[str, type[Decoder]] = {c.name: c for c in (AutoDetect, ModbusRtu, ModbusAscii, Nmea, AtLines, Ubx, Mavlink, Grbl, Sbus)}
PLUGIN_DIR = Path.home() / ".serialsniffer" / "decoders"


def create(name: str, **options) -> Decoder:
    try:
        cls = DECODERS[name]
    except KeyError:
        raise ValueError(f"decoder sconosciuto: {name!r} (disponibili: {', '.join(sorted(DECODERS))})") from None
    return cls(**options)


def register(cls: type[Decoder]) -> type[Decoder]:
    """Registra un decoder (utilizzabile anche come decoratore nei plugin)."""
    if not (inspect.isclass(cls) and issubclass(cls, Decoder)) or not cls.name:
        raise ValueError("un decoder deve derivare da Decoder e definire `name`")
    DECODERS[cls.name] = cls
    return cls


def load_plugin(path: Union[str, Path]) -> list[str]:
    """Carica un file .py ed esegue la registrazione di ogni sottoclasse di Decoder con `name`.
    Il file è codice Python eseguito con i privilegi dell'utente: caricare solo file propri o fidati."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"ssn_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"impossibile caricare {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    added = []
    for _, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, Decoder) and obj is not Decoder and obj.__module__ == mod.__name__ and obj.name:
            DECODERS[obj.name] = obj
            added.append(obj.name)
    if not added:
        raise ValueError(f"{path.name}: nessuna sottoclasse di Decoder con `name`")
    return added


def load_plugin_dir(directory: Optional[Union[str, Path]] = None) -> tuple[list[str], list[str]]:
    """Carica tutti i *.py della cartella plugin. Restituisce (decoder aggiunti, errori)."""
    d = Path(directory) if directory else PLUGIN_DIR
    added: list[str] = []
    errors: list[str] = []
    if d.is_dir():
        for f in sorted(d.glob("*.py")):
            try:
                added += load_plugin(f)
            except Exception as exc:  # un plugin difettoso non deve impedire l'avvio
                errors.append(f"{f.name}: {exc}")
    return added, errors


__all__ = [
    "AtLines", "AutoDetect", "DECODERS", "Decoder", "DecoderEngine", "Grbl", "Mavlink", "Message", "ModbusAscii", "ModbusRtu", "Nmea", "Sbus", "Ubx", "append_crc", "checksum",
    "crc16", "crc_ok", "create", "load_plugin", "load_plugin_dir", "register", "with_checksum",
]
