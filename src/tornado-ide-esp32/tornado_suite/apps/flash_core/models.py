"""Modelli e tipi base di flash_core (Python puro: nessuna dipendenza da Qt)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class FlashError(Exception):
    """Errore di programmazione/lettura con messaggio leggibile dall'utente."""


class Cancelled(FlashError):
    """Operazione annullata dall'utente."""


@dataclass(frozen=True)
class PortInfo:
    device: str
    vid: int | None = None
    pid: int | None = None
    description: str = ""
    serial_number: str = ""
    manufacturer: str = ""

    @property
    def vidpid(self) -> tuple[int, int] | None:
        return (self.vid, self.pid) if self.vid is not None and self.pid is not None else None


@dataclass
class DeviceInfo:
    port: str
    backend: str
    chip: str = ""          # nome esatto riportato dal tool, es. "ESP32-D0WD-V3"
    family: str = ""        # famiglia normalizzata, es. "ESP32", "ESP32-S3"
    details: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ImagePart:
    offset: int
    path: Path


@dataclass
class WriteRequest:
    port: str
    parts: list[ImagePart]
    baud: int = 0           # 0 = predefinito del backend/programmatore
    erase_all: bool = False
    verify: bool = True
    chip: str = "auto"


@dataclass
class RunContext:
    """Canali verso la GUI: log testuale, avanzamento 0..100, annullamento. Tutti opzionali."""
    log: Callable[[str], None] = lambda s: None
    progress: Callable[[int], None] = lambda p: None
    cancel: threading.Event = field(default_factory=threading.Event)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled("Operazione annullata")
