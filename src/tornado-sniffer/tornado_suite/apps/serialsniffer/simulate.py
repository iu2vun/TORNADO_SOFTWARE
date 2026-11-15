"""Simulatore di slave Modbus RTU: risponde su una porta seriale. Utile per provare lo sniffer senza dispositivi.

FC supportate: 1, 2, 3, 4, 5, 6, 15, 16. Errori come da specifica (eccezioni 01/02/03).
"""
from __future__ import annotations

import struct
import threading
import time
from typing import Callable, Optional

from .decoders.modbus import append_crc, crc_ok

ILLEGAL_FUNCTION, ILLEGAL_ADDRESS, ILLEGAL_VALUE = 1, 2, 3


class ModbusSlave:
    def __init__(self, addr: int = 1, size: int = 100) -> None:
        if not 1 <= addr <= 247:
            raise ValueError("indirizzo slave 1..247")
        if not 1 <= size <= 65536:
            raise ValueError("dimensione tabelle 1..65536")
        self.addr = addr
        self.size = size
        self.coils = [bool(i % 2 == 0) for i in range(size)]
        self.discrete = [bool(i % 3 == 0) for i in range(size)]
        self.holding = [i & 0xFFFF for i in range(size)]
        self.input = [(i * 10) & 0xFFFF for i in range(size)]
        self.requests = 0
        self.lock = threading.Lock()

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _exc(fc: int, code: int) -> bytes:
        return bytes([fc | 0x80, code])

    @staticmethod
    def _pack_bits(bits: list[bool]) -> bytes:
        out = bytearray((len(bits) + 7) // 8)
        for i, b in enumerate(bits):
            if b:
                out[i // 8] |= 1 << (i % 8)
        return bytes(out)

    def _range_ok(self, start: int, qty: int) -> bool:
        return start + qty <= self.size

    # ------------------------------------------------------------------- core
    def process_pdu(self, pdu: bytes) -> bytes:
        fc = pdu[0]
        with self.lock:
            if fc in (1, 2, 3, 4):
                if len(pdu) != 5:
                    return self._exc(fc, ILLEGAL_VALUE)
                start, qty = struct.unpack(">HH", pdu[1:5])
                limit = 2000 if fc in (1, 2) else 125
                if not 1 <= qty <= limit:
                    return self._exc(fc, ILLEGAL_VALUE)
                if not self._range_ok(start, qty):
                    return self._exc(fc, ILLEGAL_ADDRESS)
                if fc in (1, 2):
                    bits = (self.coils if fc == 1 else self.discrete)[start:start + qty]
                    data = self._pack_bits(bits)
                else:
                    regs = (self.holding if fc == 3 else self.input)[start:start + qty]
                    data = struct.pack(f">{qty}H", *regs)
                return bytes([fc, len(data)]) + data
            if fc == 5:
                if len(pdu) != 5:
                    return self._exc(fc, ILLEGAL_VALUE)
                addr, val = struct.unpack(">HH", pdu[1:5])
                if val not in (0x0000, 0xFF00):
                    return self._exc(fc, ILLEGAL_VALUE)
                if addr >= self.size:
                    return self._exc(fc, ILLEGAL_ADDRESS)
                self.coils[addr] = val == 0xFF00
                return pdu
            if fc == 6:
                if len(pdu) != 5:
                    return self._exc(fc, ILLEGAL_VALUE)
                addr, val = struct.unpack(">HH", pdu[1:5])
                if addr >= self.size:
                    return self._exc(fc, ILLEGAL_ADDRESS)
                self.holding[addr] = val
                return pdu
            if fc == 15:
                if len(pdu) < 6:
                    return self._exc(fc, ILLEGAL_VALUE)
                start, qty, nbytes = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= qty <= 1968 or nbytes != (qty + 7) // 8 or len(pdu) != 6 + nbytes:
                    return self._exc(fc, ILLEGAL_VALUE)
                if not self._range_ok(start, qty):
                    return self._exc(fc, ILLEGAL_ADDRESS)
                for i in range(qty):
                    self.coils[start + i] = bool(pdu[6 + i // 8] >> (i % 8) & 1)
                return pdu[:5]
            if fc == 16:
                if len(pdu) < 6:
                    return self._exc(fc, ILLEGAL_VALUE)
                start, qty, nbytes = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= qty <= 123 or nbytes != 2 * qty or len(pdu) != 6 + nbytes:
                    return self._exc(fc, ILLEGAL_VALUE)
                if not self._range_ok(start, qty):
                    return self._exc(fc, ILLEGAL_ADDRESS)
                self.holding[start:start + qty] = list(struct.unpack(f">{qty}H", pdu[6:]))
                return pdu[:5]
        return self._exc(fc, ILLEGAL_FUNCTION)

    def handle_request(self, frame: bytes) -> Optional[bytes]:
        """Frame RTU completo (con CRC) → risposta RTU, oppure None (CRC errato, altro slave, broadcast)."""
        if len(frame) < 4 or not crc_ok(frame):
            return None
        addr, pdu = frame[0], frame[1:-2]
        if addr not in (self.addr, 0):
            return None
        self.requests += 1
        resp = self.process_pdu(pdu)
        if addr == 0:  # broadcast: eseguito, nessuna risposta
            return None
        return append_crc(bytes([self.addr]) + resp)


def serve(ser, slave: ModbusSlave, stop: threading.Event, baud: int = 9600,
          on_exchange: Optional[Callable[[bytes, Optional[bytes]], None]] = None) -> None:
    """Loop di servizio su un oggetto seriale già aperto (timeout di lettura breve). Il frame termina dopo un silenzio ≥ 3.5 caratteri."""
    gap = max(3.5 * 11 / max(baud, 1), 0.004)
    buf = b""
    last = time.monotonic()
    while not stop.is_set():
        data = ser.read(256)
        now = time.monotonic()
        if data:
            buf += data
            last = now
        elif buf and now - last >= gap:
            frame, buf = buf, b""
            resp = slave.handle_request(frame)
            if resp:
                ser.write(resp)
                ser.flush()
            if on_exchange:
                on_exchange(frame, resp)
