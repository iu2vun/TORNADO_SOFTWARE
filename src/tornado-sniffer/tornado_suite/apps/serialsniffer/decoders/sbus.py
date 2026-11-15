"""SBUS (Futaba/FrSky): frame da 25 byte, 16 canali a 11 bit + 2 digitali, flag di failsafe e frame perso.
Linea fisica: 100000 baud, 8E2, segnale invertito (serve un adattatore/inverter hardware)."""
from __future__ import annotations

from typing import Optional

from ..chunk import Chunk
from .base import Decoder, Message, StreamKey

HEADER = 0x0F
FOOTERS = (0x00, 0x04, 0x14, 0x24, 0x34)  # 0x00 = SBUS1; gli altri sono gli slot di telemetria SBUS2
FRAME_LEN = 25


def channels(data22: bytes) -> list[int]:
    val = int.from_bytes(data22, "little")
    return [(val >> (11 * i)) & 0x7FF for i in range(16)]


def to_us(v: int) -> int:
    """Conversione usuale: 992 -> 1500 µs, 172 -> ~988 µs, 1811 -> ~2012 µs."""
    return round((v - 992) * 0.625 + 1500)


def build(ch: list[int], *, ch17: bool = False, ch18: bool = False, lost: bool = False, failsafe: bool = False) -> bytes:
    if len(ch) != 16 or any(not 0 <= v <= 0x7FF for v in ch):
        raise ValueError("servono 16 canali a 11 bit (0..2047)")
    val = sum(v << (11 * i) for i, v in enumerate(ch))
    flags = ch17 | ch18 << 1 | lost << 2 | failsafe << 3
    return bytes([HEADER]) + val.to_bytes(22, "little") + bytes([flags, 0x00])


class Sbus(Decoder):
    name = "sbus"
    label = "SBUS"

    def __init__(self) -> None:
        self._buf: dict[StreamKey, tuple[int, bytearray]] = {}

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: Chunk) -> list[Message]:
        key = (chunk.channel, chunk.direction)
        ts, buf = self._buf.get(key, (chunk.ts_ns, bytearray()))
        if not buf:
            ts = chunk.ts_ns
        buf += chunk.data
        out: list[Message] = []
        pos = 0
        while len(buf) - pos >= FRAME_LEN:
            if buf[pos] == HEADER and buf[pos + 24] in FOOTERS and buf[pos + 23] & 0xF0 == 0:
                out.append(self._msg(chunk, ts, bytes(buf[pos:pos + FRAME_LEN])))
                pos += FRAME_LEN
                ts = chunk.ts_ns
            else:
                pos += 1
        del buf[:pos]
        if buf:
            self._buf[key] = (ts, buf)
        else:
            self._buf.pop(key, None)
        return out

    def _msg(self, c: Chunk, ts: int, frame: bytes) -> Message:
        ch = channels(frame[1:23])
        flags = frame[23]
        notes = [n for bit, n in ((2, "frame perso"), (3, "FAILSAFE")) if flags >> bit & 1]
        fields = [(f"CH{i + 1}", f"{v} ({to_us(v)} µs)") for i, v in enumerate(ch)]
        fields += [("CH17 (digitale)", str(flags & 1)), ("CH18 (digitale)", str(flags >> 1 & 1)),
                   ("Frame perso", str(flags >> 2 & 1)), ("Failsafe", str(flags >> 3 & 1)),
                   ("Footer", f"0x{frame[24]:02X}" + (" (telemetria SBUS2)" if frame[24] else ""))]
        summary = " ".join(f"{to_us(v)}" for v in ch[:8]) + f" … ({16} ch)" + (f"  [{', '.join(notes)}]" if notes else "")
        return Message(ts, c.channel, c.direction, self.label, summary, tuple(fields), frame, ok=not (flags >> 3 & 1),
                       note=", ".join(notes), kind="frame", key="SBUS")
