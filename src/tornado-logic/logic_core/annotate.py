"""Annotazioni (riquadri con testo sulla forma d'onda) ricavate dai risultati dei decoder."""
from __future__ import annotations

from dataclasses import dataclass

from .decoders import I2cTransaction, SpiTransfer, UartFrame

_ESCAPES = {0x0D: "\\r", 0x0A: "\\n", 0x09: "\\t", 0x00: "NUL", 0x20: "SP"}


@dataclass
class Annotation:
    start: int            # indice di campione
    end: int
    text: str
    detail: str = ""
    error: bool = False


@dataclass
class Lane:
    """Riga di annotazioni sotto i canali (una per decoder/direzione)."""
    name: str
    annotations: list[Annotation]


def uart_annotations(frames: list[UartFrame]) -> list[Annotation]:
    out: list[Annotation] = []
    for f in frames:
        v = f.value
        if v in _ESCAPES:
            glyph = _ESCAPES[v]
        elif 32 < v < 127:
            glyph = chr(v)
        else:
            glyph = ""
        errs = []
        if f.framing_error:
            errs.append("errore di framing")
        if f.parity_error:
            errs.append("errore di parità")
        text = f"{v:02X}" + (f" {glyph}" if glyph else "") + ("!" if errs else "")
        out.append(Annotation(f.start, f.end, text, ", ".join(errs) or f"0x{v:02X} ({v})", bool(errs)))
    return out


def spi_annotations(transfers: list[SpiTransfer], which: str, bits: int = 8) -> list[Annotation]:
    if which not in ("mosi", "miso"):
        raise ValueError("which deve essere 'mosi' o 'miso'")
    width = (bits + 3) // 4
    out: list[Annotation] = []
    for tr in transfers:
        for w in tr.words:
            val = w.mosi if which == "mosi" else w.miso
            if val is None:
                continue
            out.append(Annotation(w.index, max(w.end, w.index + 1), f"{val:0{width}X}",
                                  f"{which.upper()} 0x{val:0{width}X} ({val})"))
    return out


def i2c_annotations(transactions: list[I2cTransaction]) -> list[Annotation]:
    out: list[Annotation] = []
    for t in transactions:
        if t.address is not None:
            rw = "R" if t.read else "W"
            text = f"{t.address:02X} {rw}" + ("" if t.addr_ack else " NACK")
            detail = f"Indirizzo 0x{t.address:02X}, {'lettura' if t.read else 'scrittura'}, " \
                     f"{'ACK' if t.addr_ack else 'NACK'}"
            out.append(Annotation(t.start, max(t.addr_end, t.start + 1), text, detail, not t.addr_ack))
        for k, b in enumerate(t.data):
            last = k == len(t.data) - 1
            # in lettura il NACK finale del master è normale, non un errore
            error = (not b.ack) and not (t.read and last)
            out.append(Annotation(b.index, max(b.end, b.index + 1),
                                  f"{b.value:02X}" + ("" if b.ack else " NACK"),
                                  f"Dato 0x{b.value:02X} ({b.value}), {'ACK' if b.ack else 'NACK'}", error))
    return out
