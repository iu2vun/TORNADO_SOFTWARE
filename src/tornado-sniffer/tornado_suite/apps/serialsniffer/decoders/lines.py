"""Riassemblaggio di righe di testo per flusso, condiviso da NMEA e AT."""
from __future__ import annotations

from typing import Optional

from ..chunk import Chunk
from .base import StreamKey

HOLD_NS = 200_000_000  # un residuo senza terminatore più vecchio di così viene emesso come riga parziale


class LineSplitter:
    """Accumula byte per flusso e restituisce (ts_primo_byte, riga, completa)."""

    def __init__(self, hold_ns: int = HOLD_NS, max_line: int = 4096) -> None:
        self.hold_ns = hold_ns
        self.max_line = max_line
        self._buf: dict[StreamKey, tuple[int, bytearray]] = {}
        self._cr: set[StreamKey] = set()

    def reset(self) -> None:
        self._buf.clear()
        self._cr.clear()

    def feed(self, c: Chunk) -> list[tuple[StreamKey, int, bytes, bool]]:
        key = (c.channel, c.direction)
        out: list[tuple[StreamKey, int, bytes, bool]] = []
        ts, buf = self._buf.get(key, (c.ts_ns, bytearray()))
        if buf and c.ts_ns - ts > self.hold_ns:
            out.append((key, ts, bytes(buf), False))
            ts, buf = c.ts_ns, bytearray()
        if not buf:
            ts = c.ts_ns
        buf += c.data
        if key in self._cr and buf[:1] == b"\n":  # \n di un \r\n spezzato fra due chunk
            del buf[:1]
        self._cr.discard(key)
        while True:
            idx = next((i for i, b in enumerate(buf) if b in (10, 13)), -1)
            if idx < 0:
                break
            line = bytes(buf[:idx])
            end = idx + 1
            if buf[idx] == 13:
                if end < len(buf) and buf[end] == 10:
                    end += 1
                elif end == len(buf):
                    self._cr.add(key)
            del buf[:end]
            if line:
                out.append((key, ts, line, True))
            ts = c.ts_ns
        if len(buf) > self.max_line:
            out.append((key, ts, bytes(buf), False))
            buf.clear()
        if buf:
            self._buf[key] = (ts, buf)
        else:
            self._buf.pop(key, None)
        return out

    def flush(self, now_ns: Optional[int] = None) -> list[tuple[StreamKey, int, bytes, bool]]:
        out = []
        for key, (ts, buf) in list(self._buf.items()):
            if now_ns is None or now_ns - ts > self.hold_ns:
                out.append((key, ts, bytes(buf), False))
                del self._buf[key]
        return out
