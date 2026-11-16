"""flash_core: programmazione/lettura flash di microcontrollori, senza GUI (usato dal pannello Flash dell'IDE)."""
from __future__ import annotations

from .backend import Backend, run_streaming
from .avrdude_backend import AvrdudeBackend, PROGRAMMERS
from .detect import candidates, describe, list_ports, scan
from .esptool_backend import EsptoolBackend
from .models import (Cancelled, DeviceInfo, FlashError, ImagePart, PortInfo, RunContext, WriteRequest)

__all__ = ["Backend", "run_streaming", "candidates", "describe", "list_ports", "scan", "EsptoolBackend", "AvrdudeBackend", "PROGRAMMERS", "Cancelled",
           "DeviceInfo", "FlashError", "ImagePart", "PortInfo", "RunContext", "WriteRequest", "default_backends"]


def default_backends() -> dict[str, Backend]:
    """Backend disponibili, per id. Nuovi backend (avrdude, pymcuprog, pyocd) si aggiungono qui."""
    return {b.id: b for b in (EsptoolBackend(), AvrdudeBackend())}
