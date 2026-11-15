"""Rendering e parsing dei byte in tutti i formati di visualizzazione."""
from __future__ import annotations

import codecs
import re

NUMERIC_FORMATS = ("hex", "dec", "oct", "bin")
TEXT_FORMATS = ("ascii", "escaped", "utf-8", "latin-1", "cp437", "cp1252", "utf-16-le", "utf-16-be")
FORMATS = NUMERIC_FORMATS + TEXT_FORMATS

_ESC_OUT = {0x0D: "\\r", 0x0A: "\\n", 0x09: "\\t", 0x5C: "\\\\"}
_ESC_IN = {
    "r": 13, "n": 10, "t": 9, "0": 0, "a": 7, "b": 8, "f": 12, "v": 11,
    "\\": 92, '"': 34, "'": 39,
}
_ESC_RE = re.compile(r"\\(x[0-9A-Fa-f]{2}|[rnt0abfv\\\"'])")


def _canon_codec(fmt: str) -> str:
    try:
        return codecs.lookup(fmt).name
    except LookupError:
        raise ValueError(f"formato sconosciuto: {fmt!r}") from None


def _ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _escaped(data: bytes) -> str:
    out = []
    for b in data:
        if b in _ESC_OUT:
            out.append(_ESC_OUT[b])
        elif 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


def render(data: bytes, fmt: str = "ascii", sep: str = " ") -> str:
    """Byte -> testo. fmt: hex, dec, oct, bin, ascii (non stampabili='.'),
    escaped (\\r \\n \\xNN, reversibile con parse_payload) o un qualunque codec Python."""
    f = fmt.lower()
    if f == "hex":
        return sep.join(f"{b:02X}" for b in data)
    if f == "dec":
        return sep.join(f"{b:03d}" for b in data)
    if f == "oct":
        return sep.join(f"{b:03o}" for b in data)
    if f == "bin":
        return sep.join(f"{b:08b}" for b in data)
    if f == "ascii":
        return _ascii(data)
    if f == "escaped":
        return _escaped(data)
    return data.decode(_canon_codec(f), "replace")


class StreamDecoder:
    """Decodifica incrementale: sequenze multibyte spezzate fra chunk diversi restano intatte."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self._dec = codecs.getincrementaldecoder(_canon_codec(encoding))(errors="replace")

    def feed(self, data: bytes, final: bool = False) -> str:
        return self._dec.decode(data, final)

    def reset(self) -> None:
        self._dec.reset()


def hexdump(data: bytes, offset: int = 0, width: int = 16) -> str:
    """Hexdump classico: offset, byte esadecimali, colonna ASCII."""
    lines = []
    for i in range(0, len(data), width):
        row = data[i : i + width]
        hx = " ".join(f"{b:02X}" for b in row).ljust(width * 3 - 1)
        lines.append(f"{offset + i:08X}  {hx}  |{_ascii(row)}|")
    return "\n".join(lines)


def _unescape(text: str, encoding: str) -> bytes:
    out = bytearray()
    pos = 0
    for m in _ESC_RE.finditer(text):
        out += text[pos : m.start()].encode(encoding)
        g = m.group(1)
        out.append(int(g[1:], 16) if g[0] == "x" else _ESC_IN[g])
        pos = m.end()
    out += text[pos:].encode(encoding)
    return bytes(out)


def parse_payload(text: str, fmt: str = "hex") -> bytes:
    """Testo digitato dall'utente -> byte da inviare. Inverso di render()."""
    f = fmt.lower()
    if f == "hex":
        digits = re.sub(r"(?i)0x|[\s,;:_-]", "", text)
        if len(digits) % 2:
            raise ValueError("numero dispari di cifre esadecimali")
        try:
            return bytes.fromhex(digits)
        except ValueError:
            raise ValueError("cifre esadecimali non valide") from None
    if f in ("dec", "oct", "bin"):
        base = {"dec": 10, "oct": 8, "bin": 2}[f]
        out = bytearray()
        for tok in re.split(r"[\s,;]+", text.strip()):
            if not tok:
                continue
            try:
                v = int(tok, base)
            except ValueError:
                raise ValueError(f"valore {f} non valido: {tok!r}") from None
            if not 0 <= v <= 255:
                raise ValueError(f"valore fuori intervallo 0..255: {tok!r}")
            out.append(v)
        return bytes(out)
    codec = "utf-8" if f in ("ascii", "escaped", "text") else _canon_codec(f)
    return _unescape(text, codec)
