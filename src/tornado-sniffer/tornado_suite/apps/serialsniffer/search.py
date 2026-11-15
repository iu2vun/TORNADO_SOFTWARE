"""Ricerca di pattern nei byte: testo, esadecimale con jolly `??`, espressioni regolari.

La corrispondenza avviene all'interno di un singolo chunk (il coalescing per gap di inattività raggruppa già i
messaggi); un pattern spezzato tra due chunk non viene trovato.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .chunk import Chunk

MODES = ("text", "hex", "regex")
MODE_LABELS = {"text": "Testo", "hex": "Hex (?? = jolly)", "regex": "Regex (byte)"}


class PatternError(ValueError):
    pass


def compile_pattern(text: str, mode: str = "text", ignore_case: bool = False) -> "re.Pattern[bytes]":
    if not text:
        raise PatternError("pattern vuoto")
    flags = re.DOTALL | (re.IGNORECASE if ignore_case else 0)
    try:
        if mode == "text":
            return re.compile(re.escape(_unescape(text)), flags)
        if mode == "hex":
            toks = text.replace(",", " ").replace("0x", " ").split()
            if len(toks) == 1 and len(toks[0]) > 2:  # "0A0D??FF" senza spazi
                t = toks[0]
                if len(t) % 2:
                    raise PatternError("numero dispari di cifre esadecimali")
                toks = [t[i:i + 2] for i in range(0, len(t), 2)]
            out = b""
            for t in toks:
                if t in ("??", "**", "?"):
                    out += b"."
                elif re.fullmatch(r"[0-9A-Fa-f]{1,2}", t):
                    out += re.escape(bytes([int(t, 16)]))
                else:
                    raise PatternError(f"token esadecimale non valido: {t!r}")
            return re.compile(out, re.DOTALL)
        if mode == "regex":
            return re.compile(text.encode("latin-1", "replace"), flags)
    except re.error as exc:
        raise PatternError(f"regex non valida: {exc}") from exc
    raise PatternError(f"modo sconosciuto: {mode}")


def _unescape(text: str) -> bytes:
    """Testo con sequenze \\r \\n \\t \\xNN \\\\ → byte."""
    out = bytearray()
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n in "rnt0\\":
                out.append({"r": 13, "n": 10, "t": 9, "0": 0, "\\": 92}[n])
                i += 2
                continue
            if n == "x" and re.fullmatch(r"[0-9A-Fa-f]{2}", text[i + 2:i + 4]):
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
        out += ch.encode("utf-8")
        i += 1
    return bytes(out)


@dataclass(frozen=True)
class Hit:
    ts_ns: int
    channel: int
    direction: int
    offset: int
    length: int
    context: bytes  # byte intorno al match, per l'anteprima


def find_in_chunk(pat: "re.Pattern[bytes]", c: Chunk, context: int = 8, limit: int = 50) -> list[Hit]:
    if c.is_event or not c.data:
        return []
    hits = []
    for m in pat.finditer(c.data):
        if m.end() == m.start():
            continue
        a, b = max(0, m.start() - context), min(len(c.data), m.end() + context)
        hits.append(Hit(c.ts_ns, c.channel, c.direction, m.start(), m.end() - m.start(), c.data[a:b]))
        if len(hits) >= limit:
            break
    return hits


def search_chunks(chunks, pat: "re.Pattern[bytes]", channel: Optional[int] = None, direction: Optional[int] = None,
                  limit: int = 100_000) -> list[Hit]:
    out: list[Hit] = []
    for c in chunks:
        if channel is not None and c.channel != channel:
            continue
        if direction is not None and c.direction != direction:
            continue
        out += find_in_chunk(pat, c)
        if len(out) >= limit:
            break
    return out[:limit]
