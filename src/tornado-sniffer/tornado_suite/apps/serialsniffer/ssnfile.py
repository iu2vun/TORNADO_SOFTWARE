"""Formato di registrazione .ssn (streaming, robusto a file troncati).

Layout:
    "SSN1" | u32 lunghezza header | header JSON UTF-8
    poi record ripetuti:  u8 kind | u8 canale | u8 direzione | u64 ts_ns | u32 lunghezza | payload
Little-endian. I timestamp sono in ns dall'inizio della registrazione.
Un file interrotto (crash, disco pieno) resta leggibile fino all'ultimo record completo.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator, Optional, Union

from .chunk import DIR_NAMES, GLOBAL, KIND_DATA, KIND_EVENT, Chunk

MAGIC = b"SSN1"
VERSION = 1
FORMAT_NAME = "serialsniffer"
MAX_HEADER = 16 << 20
MAX_PAYLOAD = 64 << 20

_LEN = struct.Struct("<I")
_REC = struct.Struct("<BBBQI")


class SsnError(Exception):
    pass


def make_header(channels: list[dict], t0_wall_ns: int, notes: str = "") -> dict:
    return {
        "format": FORMAT_NAME,
        "version": VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "t0_wall_ns": int(t0_wall_ns),
        "channels": list(channels),
        "notes": notes,
    }


class SsnWriter:
    def __init__(self, path: Union[str, Path], header: dict) -> None:
        self.path = Path(path)
        self._f: Optional[BinaryIO] = open(self.path, "wb")
        blob = json.dumps(header, ensure_ascii=False).encode("utf-8")
        self._f.write(MAGIC + _LEN.pack(len(blob)) + blob)
        self._f.flush()

    def write(self, chunk: Chunk, ts_ns: Optional[int] = None) -> None:
        ts = chunk.ts_ns if ts_ns is None else ts_ns
        self._f.write(_REC.pack(chunk.kind, chunk.channel, chunk.direction, ts, len(chunk.data)) + chunk.data)

    def flush(self) -> None:
        if self._f is not None:
            self._f.flush()

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


@dataclass
class Recording:
    header: dict
    chunks: list[Chunk] = field(default_factory=list)
    truncated: bool = False
    error: Optional[str] = None

    @property
    def duration_ns(self) -> int:
        return self.chunks[-1].ts_ns if self.chunks else 0

    @property
    def t0_wall_ns(self) -> int:
        return int(self.header.get("t0_wall_ns", 0))

    def channel_info(self) -> dict[int, dict]:
        return {int(c["index"]): c for c in self.header.get("channels", [])}

    def channel_name(self, idx: int) -> str:
        if idx == GLOBAL:
            return "*"
        info = self.channel_info().get(idx)
        return str(info["name"]) if info and info.get("name") else f"CH{idx}"

    def resolve_channel(self, token: Union[int, str]) -> int:
        """Indice o nome canale -> indice."""
        if isinstance(token, int):
            return token
        for idx, info in self.channel_info().items():
            if info.get("name") == token:
                return idx
        if token.isdigit():
            return int(token)
        raise ValueError(f"canale sconosciuto: {token!r}")

    def utc_iso(self, ts_ns: int) -> str:
        sec, ns = divmod(self.t0_wall_ns + ts_ns, 1_000_000_000)
        base = datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return f"{base}.{ns:09d}Z"

    def summary(self) -> dict:
        per: dict[int, dict] = {}
        for c in self.chunks:
            s = per.setdefault(c.channel, {"rx_bytes": 0, "tx_bytes": 0, "chunks": 0, "events": 0})
            if c.is_event:
                s["events"] += 1
            else:
                s["chunks"] += 1
                s["tx_bytes" if c.direction == 1 else "rx_bytes"] += len(c.data)
        return {
            "duration_s": self.duration_ns / 1e9,
            "records": len(self.chunks),
            "truncated": self.truncated,
            "error": self.error,
            "channels": {self.channel_name(i): s for i, s in sorted(per.items())},
        }


def select_chunks(
    rec: Recording,
    channels: Optional[Iterable[int]] = None,
    directions: Optional[Iterable[int]] = None,
    include_events: bool = True,
) -> Iterator[Chunk]:
    """Filtra per canale/direzione. Gli eventi GLOBAL (marker) passano sempre se include_events."""
    ch = None if channels is None else set(channels)
    dr = None if directions is None else set(directions)
    for c in rec.chunks:
        if c.kind == KIND_EVENT:
            if include_events and (ch is None or c.channel in ch or c.channel == GLOBAL):
                yield c
            continue
        if ch is not None and c.channel not in ch:
            continue
        if dr is not None and c.direction not in dr:
            continue
        yield c


def read_ssn(path: Union[str, Path]) -> Recording:
    """Carica un file .ssn in memoria. Un file troncato/corrotto restituisce i record validi
    con truncated=True e il motivo in error."""
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise SsnError("non è un file .ssn (firma errata)")
        raw = f.read(4)
        if len(raw) < 4:
            raise SsnError("header troncato")
        (n,) = _LEN.unpack(raw)
        if n > MAX_HEADER:
            raise SsnError("header corrotto")
        blob = f.read(n)
        if len(blob) < n:
            raise SsnError("header troncato")
        try:
            header = json.loads(blob.decode("utf-8"))
        except ValueError:
            raise SsnError("header JSON non valido") from None

        rec = Recording(header=header)
        while True:
            raw = f.read(_REC.size)
            if not raw:
                break
            if len(raw) < _REC.size:
                rec.truncated, rec.error = True, "ultimo record troncato"
                break
            kind, ch, direction, ts, ln = _REC.unpack(raw)
            if kind not in (KIND_DATA, KIND_EVENT) or direction not in DIR_NAMES or ln > MAX_PAYLOAD:
                rec.truncated, rec.error = True, "record corrotto"
                break
            payload = f.read(ln)
            if len(payload) < ln:
                rec.truncated, rec.error = True, "payload dell'ultimo record troncato"
                break
            rec.chunks.append(Chunk(ts, ch, direction, payload, kind))
    rec.chunks.sort(key=lambda c: c.ts_ns)  # stabile: i thread possono invertire di pochi µs
    return rec
