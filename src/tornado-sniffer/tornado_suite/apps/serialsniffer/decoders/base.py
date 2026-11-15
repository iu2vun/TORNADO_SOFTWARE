"""Interfaccia dei decoder di protocollo e motore di decodifica."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Optional

from ..chunk import GLOBAL, Chunk

StreamKey = tuple[int, int]  # (canale, direzione)


@dataclass(frozen=True)
class Message:
    """Un'unità di protocollo decodificata, con il timestamp del primo byte che la compone."""

    ts_ns: int
    channel: int
    direction: int
    protocol: str
    summary: str
    fields: tuple[tuple[str, str], ...] = ()
    raw: bytes = b""
    ok: bool = True          # False: checksum/CRC errato o struttura invalida
    note: str = ""           # motivo dell'errore o annotazione (es. "orfana", "senza terminatore")
    kind: str = ""           # classe del messaggio: request, response, exception, command, urc, sentence...
    pair_ns: Optional[int] = None  # intervallo richiesta→risposta, se noto
    key: str = ""            # identità del tipo di messaggio, per le statistiche (es. "@1 FC3", "GPGGA", "AT+CSQ")

    def field_dict(self) -> dict[str, str]:
        return dict(self.fields)

    def to_dict(self) -> dict:
        return {
            "ts_ns": self.ts_ns, "channel": self.channel, "direction": self.direction,
            "protocol": self.protocol, "kind": self.kind, "summary": self.summary, "ok": self.ok,
            "note": self.note, "fields": dict(self.fields), "raw": self.raw.hex(),
            "latency_ms": None if self.pair_ns is None else self.pair_ns / 1e6, "key": self.key,
        }


class Decoder(ABC):
    """Decoder con stato. Riceve i messaggi (chunk) di tutti i flussi, già raggruppati per gap idle,
    nell'ordine temporale, e ne restituisce le unità di protocollo.

    Sottoclassi: impostare `name` (identificatore breve) e `label` (testo mostrato nella GUI).
    `options` descrive le opzioni accettate dal costruttore (nome -> descrizione)."""

    name = ""
    label = ""
    options: dict[str, str] = {}

    @abstractmethod
    def feed(self, chunk: Chunk) -> list[Message]:
        ...

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        """Emette ciò che è rimasto in sospeso (es. riga senza terminatore). `now_ns` None = fine dati."""
        return []

    def reset(self) -> None:
        pass


class DecoderEngine:
    """Applica un decoder ai chunk dati e salta gli eventi."""

    def __init__(self, decoder: Decoder) -> None:
        self.decoder = decoder

    def feed(self, chunk: Chunk) -> list[Message]:
        if chunk.is_event or chunk.channel == GLOBAL:
            return []
        return self.decoder.feed(chunk)

    def feed_many(self, chunks: Iterable[Chunk]) -> list[Message]:
        out: list[Message] = []
        for c in chunks:
            out.extend(self.feed(c))
        return out

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        return self.decoder.flush(now_ns)

    def reset(self) -> None:
        self.decoder.reset()
