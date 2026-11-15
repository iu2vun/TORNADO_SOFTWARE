"""Tipi base condivisi da acquisizione, registrazione, riproduzione ed export."""
from __future__ import annotations

from dataclasses import dataclass

RX = 0  # byte letti dalla porta
TX = 1  # byte scritti sulla porta (invio manuale o replay)

KIND_DATA = 0
KIND_EVENT = 1  # payload = testo UTF-8: apertura/chiusura, errori, marker, linee modem

GLOBAL = 255  # "canale" degli eventi non legati a una porta (marker, REC START/STOP)
MAX_CHANNELS = 254

DIR_NAMES = {RX: "RX", TX: "TX"}


@dataclass(frozen=True, slots=True)
class Chunk:
    """Blocco di byte (o evento) con timestamp in ns relativo all'origine dei tempi."""

    ts_ns: int
    channel: int
    direction: int
    data: bytes
    kind: int = KIND_DATA

    @property
    def is_event(self) -> bool:
        return self.kind == KIND_EVENT

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")
