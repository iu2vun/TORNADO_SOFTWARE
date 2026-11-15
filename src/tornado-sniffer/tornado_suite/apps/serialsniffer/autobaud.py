"""Auto-detect euristico del baud rate su flussi di testo.

Apre la porta a ciascun baud candidato, ascolta per qualche istante e valuta la quota di byte
stampabili: con il baud sbagliato i dati diventano rumore (0x00, 0xFF, 0x80...). Funziona solo
se la linea trasmette mentre si misura; su protocolli binari il punteggio non è affidabile.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import serial

from .config import PortConfig

COMMON_BAUDS = (
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800,
    115200, 230400, 250000, 460800, 500000, 921600, 1000000, 1500000, 2000000,
)


@dataclass(frozen=True)
class BaudResult:
    baudrate: int
    score: float  # 0..1
    nbytes: int
    error: Optional[str] = None


def printable_score(data: bytes) -> float:
    """Frazione di byte che sono ASCII stampabile o TAB/CR/LF."""
    if not data:
        return 0.0
    ok = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return ok / len(data)


def detect_baud(
    url: str,
    candidates: Iterable[int] = COMMON_BAUDS,
    *,
    framing: str = "8N1",
    sample_s: float = 1.0,
    min_bytes: int = 4,
    stop: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[BaudResult], None]] = None,
) -> list[BaudResult]:
    """Risultati ordinati dal più probabile. Il punteggio è pesato sul numero di byte ricevuti
    (meno di min_bytes => affidabilità ridotta)."""
    results: list[BaudResult] = []
    for baud in candidates:
        if stop is not None and stop.is_set():
            break
        try:
            cfg = PortConfig.parse(f"{baud} {framing}")
            ser = serial.serial_for_url(url, do_not_open=True, **cfg.serial_kwargs())
            ser.timeout = 0.05
            ser.open()
        except (serial.SerialException, OSError, ValueError) as exc:
            res = BaudResult(baud, 0.0, 0, str(exc))
        else:
            buf = bytearray()
            try:
                ser.reset_input_buffer()
                deadline = time.monotonic() + sample_s
                while time.monotonic() < deadline and not (stop is not None and stop.is_set()):
                    buf += ser.read(max(1, ser.in_waiting))
            except (serial.SerialException, OSError) as exc:
                res = BaudResult(baud, 0.0, len(buf), str(exc))
            else:
                score = printable_score(bytes(buf)) * min(1.0, len(buf) / min_bytes)
                res = BaudResult(baud, score, len(buf))
            finally:
                ser.close()
        results.append(res)
        if on_progress is not None:
            on_progress(res)
    results.sort(key=lambda r: (r.score, r.nbytes), reverse=True)
    return results
