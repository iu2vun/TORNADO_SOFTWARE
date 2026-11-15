"""Canale seriale: lettura (RX) con raggruppamento a gap idle e scrittura (TX) su thread dedicati."""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Optional

import serial

from .chunk import KIND_EVENT, RX, TX, Chunk
from .config import PortConfig

Emit = Callable[[Chunk], None]
Clock = Callable[[], int]
RawTap = Callable[[int, bytes], None]

_PORT_ERRORS = (serial.SerialException, OSError, TypeError)

_BUSY_MARKERS = ("PermissionError", "Access is denied", "Accesso negato", "WinError 5", "(13,", "busy", "occupat")


def busy_hint(exc: BaseException) -> str:
    """Se l'errore indica una porta già aperta da un altro programma, spiega cosa fare."""
    t = str(exc)
    if any(m.lower() in t.lower() for m in _BUSY_MARKERS):
        return (" — la porta è già aperta da un altro programma (su Windows una COM è ad accesso esclusivo): per intercettarla usa "
                "«Intercetta porta…» (coppia virtuale com0com) oppure un tap passivo con due adattatori")
    return ""


class Channel:
    """Una porta (COMx, /dev/ttyX, loop://, socket://, rfc2217://).

    Raggruppamento (idle_gap_s): i byte ricevuti vengono uniti in un unico chunk finché fra due
    letture non passa più di `gap`. Il timestamp del chunk è quello del PRIMO byte. Il chunk viene
    comunque chiuso dopo max_hold_s o max_frame byte, così i flussi continui non restano in attesa.
      None -> auto: max(20 ms, 3.5 tempi di carattere);  0 -> disattivato (un chunk per lettura);
      >0   -> secondi.
    Il tap `raw` riceve invece ogni lettura subito, senza raggruppamento (usato dal bridge).
    Il timestamp include la latenza dell'adattatore USB (FTDI: latency timer fino a 16 ms).
    """

    READ_TIMEOUT_S = 0.02
    LINES_POLL_S = 0.02
    AUTO_MIN_GAP_S = 0.020
    AUTO_CHAR_TIMES = 3.5

    def __init__(
        self,
        index: int,
        name: str,
        url: str,
        config: PortConfig,
        emit: Emit,
        clock: Clock,
        *,
        reconnect: bool = True,
        retry_s: float = 1.0,
        monitor_lines: bool = False,
        idle_gap_s: Optional[float] = None,
        max_frame: int = 8192,
        max_hold_s: float = 0.5,
        raw: Optional[RawTap] = None,
    ) -> None:
        self.index = index
        self.name = name
        self.url = url
        self.config = config
        self.reconnect = reconnect
        self.retry_s = retry_s
        self.monitor_lines = monitor_lines
        self.idle_gap_s = idle_gap_s
        self.max_frame = max(1, int(max_frame))
        self.max_hold_s = max_hold_s
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.rx_chunks = 0
        self._emit = emit
        self._clock = clock
        self._raw = raw
        self._ser: Optional[serial.SerialBase] = None
        self._stop = threading.Event()
        self._tx_q: "queue.Queue[tuple[bytes, bool]]" = queue.Queue()
        self._rd: Optional[threading.Thread] = None
        self._wr: Optional[threading.Thread] = None
        self._lines_prev: Optional[tuple] = None
        self._lines_t = 0.0
        self._buf = bytearray()
        self._t_first = 0
        self._t_last = 0

    # ------------------------------------------------------------------ API
    @property
    def char_time_s(self) -> float:
        c = self.config
        bits = 1 + c.bytesize + (0 if c.parity == "N" else 1) + c.stopbits
        return bits / c.baudrate

    @property
    def effective_gap_s(self) -> float:
        if self.idle_gap_s is None:
            return max(self.AUTO_MIN_GAP_S, self.AUTO_CHAR_TIMES * self.char_time_s)
        return max(0.0, float(self.idle_gap_s))

    @property
    def is_open(self) -> bool:
        ser = self._ser
        return bool(ser is not None and ser.is_open)

    def start(self) -> None:
        """Apre la porta. Con reconnect=False un errore di apertura viene sollevato;
        con reconnect=True il thread riprova ogni retry_s secondi."""
        if self._rd is not None:
            return
        self._stop.clear()
        try:
            self._open()
        except (serial.SerialException, OSError) as exc:
            if not self.reconnect:
                raise
            self._event(f"OPEN FAILED {self.url}: {exc}{busy_hint(exc)} (nuovo tentativo ogni {self.retry_s:g}s)")
        else:
            self._event(f"OPEN {self.url} {self.config}")
        self._rd = threading.Thread(target=self._read_loop, name=f"rx-{self.name}", daemon=True)
        self._wr = threading.Thread(target=self._write_loop, name=f"tx-{self.name}", daemon=True)
        self._rd.start()
        self._wr.start()

    def stop(self) -> None:
        if self._rd is None:
            return
        self._stop.set()
        for t in (self._rd, self._wr):
            if t is not None and t is not threading.current_thread():
                t.join(timeout=2.0)
        self._rd = self._wr = None
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._event("CLOSED")

    def write(self, data: bytes, record: bool = True) -> None:
        """Accoda byte da trasmettere. record=False: non registrare (inoltro bridge)."""
        if data:
            self._tx_q.put((bytes(data), record))

    # ------------------------------------------------------------- internals
    def _event(self, text: str) -> None:
        self._emit(Chunk(self._clock(), self.index, RX, text.encode("utf-8"), KIND_EVENT))

    def _poll_timeout_s(self) -> float:
        gap = self.effective_gap_s
        if gap <= 0:
            return self.READ_TIMEOUT_S
        return max(0.001, min(self.READ_TIMEOUT_S, gap / 2))

    def _open(self) -> None:
        ser = serial.serial_for_url(self.url, do_not_open=True, **self.config.serial_kwargs())
        ser.timeout = self._poll_timeout_s()
        ser.write_timeout = 1.0
        ser.open()
        self._ser = ser
        self._lines_prev = None

    def _lost(self, exc: BaseException) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._event(f"DISCONNECTED {self.url}: {exc}")

    def _poll_lines(self, ser: serial.SerialBase) -> None:
        now_t = time.perf_counter()
        if now_t - self._lines_t < self.LINES_POLL_S:
            return
        self._lines_t = now_t
        try:
            now = (ser.cts, ser.dsr, ser.ri, ser.cd)
        except (serial.SerialException, OSError, NotImplementedError) as exc:
            self.monitor_lines = False
            self._event(f"LINES monitor non supportato: {exc}")
            return
        if now != self._lines_prev:
            self._lines_prev = now
            cts, dsr, ri, cd = (int(v) for v in now)
            self._event(f"LINES cts={cts} dsr={dsr} ri={ri} cd={cd}")

    def _flush(self) -> None:
        if self._buf:
            data, self._buf = bytes(self._buf), bytearray()
            self.rx_chunks += 1
            self._emit(Chunk(self._t_first, self.index, RX, data))

    def _read_loop(self) -> None:
        gap_ns = int(self.effective_gap_s * 1e9)
        hold_ns = int(self.max_hold_s * 1e9)
        try:
            while not self._stop.is_set():
                ser = self._ser
                if ser is None:
                    if not self.reconnect:
                        return
                    if self._stop.wait(self.retry_s):
                        return
                    try:
                        self._open()
                    except (serial.SerialException, OSError):
                        continue
                    self._event(f"RECONNECTED {self.url}")
                    continue
                try:
                    waiting = ser.in_waiting
                    data = ser.read(waiting if waiting > 0 else 1)
                    if self.monitor_lines:
                        self._poll_lines(ser)
                except _PORT_ERRORS as exc:
                    self._flush()
                    if self._stop.is_set():
                        return
                    self._lost(exc)
                    if not self.reconnect:
                        return
                    continue
                now = self._clock()
                if data:
                    self.rx_bytes += len(data)
                    if self._raw is not None:
                        self._raw(self.index, data)  # inoltro immediato, indipendente dal raggruppamento
                    if gap_ns == 0:
                        self.rx_chunks += 1
                        self._emit(Chunk(now, self.index, RX, data))
                        continue
                    if not self._buf:
                        self._t_first = now
                    self._buf += data
                    self._t_last = now
                    if len(self._buf) >= self.max_frame or now - self._t_first >= hold_ns:
                        self._flush()
                elif self._buf and now - self._t_last >= gap_ns:
                    self._flush()
        finally:
            self._flush()

    def _write_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, record = self._tx_q.get(timeout=0.1)
            except queue.Empty:
                continue
            ser = self._ser
            if ser is None:
                self._event(f"TX DROPPED {len(data)}B: porta non aperta")
                continue
            try:
                ser.write(data)
            except (serial.SerialException, OSError) as exc:
                self._event(f"TX ERROR {len(data)}B: {exc}")
                continue
            ts = self._clock()
            self.tx_bytes += len(data)
            if record:
                self._emit(Chunk(ts, self.index, TX, data))
