"""Trasferimenti per Tornado-Term: invio ASCII con ritardi e XMODEM / XMODEM-CRC / XMODEM-1K / YMODEM.

Il modulo non dipende dalla GUI ne' dal tipo di trasporto: lavora su una funzione write(bytes) e,
per la ricezione, su feed(bytes) chiamato con i dati in arrivo.
"""
from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal

SOH, STX, EOT, ACK, NAK, CAN = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18
CRC_REQ = 0x43  # 'C'
PAD = 0x1A


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        table.append(c)
    return table


_CRC_TABLE = _make_crc_table()


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM (poly 0x1021, init 0)."""
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


# ---------------------------------------------------------------------------
# Invio ASCII con ritardi
# ---------------------------------------------------------------------------
class AsciiSender(QObject):
    """Invia un file di testo con ritardo per carattere e per riga.

    Se i ritardi sono 0 invia una riga per tick, ma non accumula piu' di 2 kB nel buffer di uscita
    (backlog), cosi' l'avanzamento rispecchia i byte realmente partiti e l'annullo e' immediato.
    """

    progress = Signal(int, int)       # byte inviati, totali
    finished = Signal(bool, str)

    BACKLOG_LIMIT = 2048

    def __init__(self, write: Callable[[bytes], object], backlog: Callable[[], int] = lambda: 0, parent=None):
        super().__init__(parent)
        self._write = write
        self._backlog = backlog
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self.active = False
        self._lines: list[bytes] = []
        self._li = self._ci = self._sent = self._total = 0
        self._char_delay = self._line_delay = 0

    def start(self, data: bytes, char_delay_ms: int = 0, line_delay_ms: int = 0,
              eol: bytes | None = b"\r") -> bool:
        """eol=None mantiene i fine riga del file; altrimenti ogni riga termina con eol."""
        if self.active:
            return False
        parts = data.splitlines(keepends=True)
        if eol is not None:
            parts = [re.sub(rb"(\r\n|\r|\n)$", b"", p) + eol for p in parts]
        if not parts:
            self.finished.emit(False, "File vuoto")
            return False
        self._lines = parts
        self._li = self._ci = self._sent = 0
        self._total = sum(len(p) for p in parts)
        self._char_delay = max(0, int(char_delay_ms))
        self._line_delay = max(0, int(line_delay_ms))
        self.active = True
        self.progress.emit(0, self._total)
        self._timer.start(0)
        return True

    def cancel(self):
        if self.active:
            self._finish(False, "Annullato")

    def _finish(self, ok: bool, msg: str):
        self._timer.stop()
        self.active = False
        self.finished.emit(ok, msg)

    def _tick(self):
        if not self.active:
            return
        if self._li >= len(self._lines):
            if self._backlog() > 0:
                self._timer.start(5)
                return
            self._finish(True, "Completato")
            return
        if self._backlog() > self.BACKLOG_LIMIT:
            self._timer.start(2)
            return
        line = self._lines[self._li]
        if self._char_delay > 0:
            self._write(line[self._ci:self._ci + 1])
            self._ci += 1
            self._sent += 1
            if self._ci >= len(line):
                self._li += 1
                self._ci = 0
                delay = self._char_delay + self._line_delay
            else:
                delay = self._char_delay
        else:
            self._write(line)
            self._sent += len(line)
            self._li += 1
            delay = self._line_delay
        self.progress.emit(self._sent, self._total)
        self._timer.start(max(1, delay))


# ---------------------------------------------------------------------------
# XMODEM / YMODEM
# ---------------------------------------------------------------------------
class XYModem(QObject):
    """Mittente e ricevente XMODEM (checksum), XMODEM-CRC, XMODEM-1K e YMODEM (batch).

    Uso: write = funzione di trasmissione; durante un trasferimento attivo i byte in arrivo vanno
    passati a feed(). Termina con finished(ok, messaggio).
    """

    progress = Signal(int, int)           # byte trasferiti, totali (0 se ignoto)
    status = Signal(str)
    finished = Signal(bool, str)
    file_received = Signal(str, bytes)    # nome (vuoto in XMODEM), contenuto

    VARIANTS = ("XMODEM", "XMODEM-CRC", "XMODEM-1K", "YMODEM")

    # tempi in secondi (sovrascrivibili)
    start_timeout = 60.0       # mittente: attesa dell'avvio da parte del ricevente
    ack_timeout = 10.0         # mittente: attesa di ACK
    block_timeout = 10.0       # ricevente: attesa del blocco successivo
    byte_timeout = 3.0         # ricevente: silenzio a meta' blocco
    handshake_interval = 3.0   # ricevente: intervallo tra 'C'/NAK iniziali
    handshake_tries = 20
    purge_ms = 200             # ricevente: silenzio richiesto prima di inviare NAK dopo un errore
    max_retries = 10

    def __init__(self, write: Callable[[bytes], object], parent=None):
        super().__init__(parent)
        self._write = write
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._purge = QTimer(self)
        self._purge.setSingleShot(True)
        self._purge.timeout.connect(self._on_purge)
        self.active = False
        self._role = ""
        self._state = ""
        self._can = 0
        self._retries = 0

    # --- API ----------------------------------------------------------------
    def send(self, files: list[tuple[str, bytes]], variant: str = "XMODEM-CRC") -> bool:
        """files: lista di (nome, contenuto). In XMODEM si invia solo il primo."""
        if self.active or not files or variant not in self.VARIANTS:
            return False
        self._begin("send")
        self._variant = variant
        self._ymodem = variant == "YMODEM"
        self._use_1k = variant in ("XMODEM-1K", "YMODEM")
        self._crc = True
        self._files = list(files) if self._ymodem else list(files[:1])
        self._fi = 0
        self._total = sum(len(d) for _, d in self._files)
        self._done = 0
        self._state = "wait_start"
        self.status.emit("In attesa del ricevitore...")
        self.progress.emit(0, self._total)
        self._arm(self.start_timeout)
        return True

    def receive(self, variant: str = "XMODEM-CRC", strip_padding: bool = False) -> bool:
        if self.active or variant not in self.VARIANTS:
            return False
        self._begin("recv")
        self._variant = variant
        self._ymodem = variant == "YMODEM"
        self._crc = variant != "XMODEM"
        self._strip = strip_padding
        self._rx = bytearray()
        self._want_header = self._ymodem
        self._expect = 1
        self._buf = bytearray()
        self._name = ""
        self._size: int | None = None
        self._eot_seen = False
        self._errors = 0
        self._nfiles = 0
        self._hs_tries = 0
        self._state = "handshake"
        self.status.emit("In attesa del mittente...")
        self._rcv_handshake()
        return True

    def feed(self, data: bytes) -> bool:
        """Da chiamare con i byte ricevuti. Ritorna True se il trasferimento li ha consumati."""
        if not self.active:
            return False
        if self._role == "send":
            for b in data:
                if not self.active:
                    break
                self._snd_byte(b)
        else:
            self._rcv_feed(data)
        return True

    def abort(self):
        if self.active:
            self._write(bytes([CAN]) * 3)
            self._finish(False, "Annullato")

    # --- comuni -------------------------------------------------------------
    def _begin(self, role: str):
        self.active = True
        self._role = role
        self._can = 0
        self._retries = 0

    def _arm(self, seconds: float):
        self._timer.start(max(1, int(seconds * 1000)))

    def _finish(self, ok: bool, msg: str):
        self._timer.stop()
        self._purge.stop()
        self.active = False
        self.finished.emit(ok, msg)

    def _fail_abort(self, msg: str):
        self._write(bytes([CAN]) * 3)
        self._finish(False, msg)

    def _on_timeout(self):
        if not self.active:
            return
        if self._role == "send":
            st = self._state
            if st == "wait_start":
                self._finish(False, "Timeout: il ricevitore non ha avviato il trasferimento")
            elif st == "wait_ack":
                self._snd_resend()
            elif st == "wait_eot_ack":
                self._snd_resend()
            elif st in ("wait_c2", "wait_c3"):
                self._fail_abort("Timeout in attesa del ricevitore")
            elif st == "wait_final_ack":
                self._finish(True, "Completato")
        else:
            self._rcv_timeout()

    # --- mittente -----------------------------------------------------------
    def _build(self, seq: int, data: bytes, size: int, pad: int) -> bytes:
        data = data.ljust(size, bytes([pad]))
        head = bytes([SOH if size == 128 else STX, seq & 0xFF, 0xFF - (seq & 0xFF)])
        tail = crc16(data).to_bytes(2, "big") if self._crc else bytes([sum(data) & 0xFF])
        return head + data + tail

    def _snd_byte(self, b: int):
        if b == CAN:
            self._can += 1
            if self._can >= 2:
                self._finish(False, "Annullato dal ricevitore")
            return
        self._can = 0
        st = self._state
        if st == "wait_start":
            if b == CRC_REQ:
                self._crc = True
                self._snd_begin_file()
            elif b == NAK:
                self._crc = False
                if not self._ymodem:
                    self._use_1k = False
                self._snd_begin_file()
        elif st == "wait_ack":
            if b == ACK:
                self._snd_acked()
            elif b == NAK:
                self._snd_resend()
        elif st == "wait_c2":
            if b == CRC_REQ or b == NAK:
                self._snd_data_block()
        elif st == "wait_eot_ack":
            if b == ACK:
                if self._ymodem:
                    self._state = "wait_c3"
                    self._arm(self.ack_timeout)
                else:
                    self._finish(True, "Completato")
            elif b == NAK:
                self._snd_resend()
        elif st == "wait_c3":
            if b == CRC_REQ:
                self._fi += 1
                if self._fi < len(self._files):
                    self._snd_begin_file()
                else:
                    self._cur_block = self._build(0, b"", 128, 0x00)
                    self._write(self._cur_block)
                    self._state = "wait_final_ack"
                    self._arm(self.ack_timeout)
        elif st == "wait_final_ack":
            if b == ACK:
                self._finish(True, "Completato")

    def _snd_begin_file(self):
        name, data = self._files[self._fi]
        self._data = data
        self._off = 0
        self._seq = 1
        self.status.emit(f"Invio {name}" if name else "Invio")
        if self._ymodem:
            payload = name.encode("utf-8", "replace")[:200] + b"\0" + f"{len(data)} 0".encode()
            size = 128 if len(payload) <= 128 else 1024
            self._cur_block = self._build(0, payload, size, 0x00)
            self._is_header = True
            self._tx_block()
        else:
            self._snd_data_block()

    def _tx_block(self):
        self._write(self._cur_block)
        self._state = "wait_ack"
        self._arm(self.ack_timeout)

    def _snd_data_block(self):
        remaining = len(self._data) - self._off
        if remaining <= 0:
            self._snd_eot()
            return
        size = 1024 if (self._use_1k and remaining > 128) else 128
        chunk = self._data[self._off:self._off + size]
        self._chunk_len = len(chunk)
        self._cur_block = self._build(self._seq & 0xFF, chunk, size, PAD)
        self._is_header = False
        self._tx_block()

    def _snd_acked(self):
        self._retries = 0
        if self._is_header:
            self._state = "wait_c2"
            self._arm(self.ack_timeout)
            return
        self._off += self._chunk_len
        self._seq += 1
        self._done += self._chunk_len
        self.progress.emit(self._done, self._total)
        if self._off >= len(self._data):
            self._snd_eot()
        else:
            self._snd_data_block()

    def _snd_eot(self):
        self._cur_block = bytes([EOT])
        self._write(self._cur_block)
        self._state = "wait_eot_ack"
        self._arm(self.ack_timeout)

    def _snd_resend(self):
        self._retries += 1
        if self._retries > self.max_retries:
            self._fail_abort("Troppi errori di trasmissione")
            return
        self.status.emit(f"Ritrasmissione ({self._retries}/{self.max_retries})")
        self._write(self._cur_block)
        self._arm(self.ack_timeout)

    # --- ricevente ----------------------------------------------------------
    def _hs_char(self) -> bytes:
        return bytes([CRC_REQ if self._crc else NAK])

    def _rcv_handshake(self):
        self._hs_tries += 1
        if self._hs_tries > self.handshake_tries:
            self._finish(False, "Timeout: nessun dato dal mittente")
            return
        self._write(self._hs_char())
        self._arm(self.handshake_interval)

    def _rcv_feed(self, data: bytes):
        if self._state == "purge":
            self._purge.start(self.purge_ms)   # scarta finche' la linea non tace
            return
        self._rx += data
        self._rcv_parse()

    def _rcv_parse(self):
        rx = self._rx
        while rx and self.active and self._state != "purge":
            b = rx[0]
            if b in (SOH, STX):
                size = 128 if b == SOH else 1024
                need = 3 + size + (2 if self._crc else 1)
                if len(rx) < need:
                    self._arm(self.byte_timeout)
                    return
                frame = bytes(rx[:need])
                del rx[:need]
                self._rcv_block(frame, size)
            elif b == EOT:
                del rx[0]
                self._can = 0
                self._rcv_eot()
            elif b == CAN:
                del rx[0]
                self._can += 1
                if self._can >= 2:
                    self._finish(False, "Annullato dal mittente")
                    return
            else:
                del rx[0]
        if self.active and self._state == "recv" and not rx:
            self._arm(self.block_timeout)

    def _rcv_error(self):
        self._errors += 1
        if self._errors > self.max_retries:
            self._fail_abort("Troppi errori di ricezione")
            return
        self._rx.clear()
        self._state = "purge"
        self._timer.stop()
        self._purge.start(self.purge_ms)

    def _on_purge(self):
        if not self.active:
            return
        self._state = "recv"
        self._write(bytes([NAK]))
        self._arm(self.block_timeout)

    def _rcv_block(self, frame: bytes, size: int):
        self._can = 0
        seq, inv = frame[1], frame[2]
        data = frame[3:3 + size]
        tail = frame[3 + size:]
        ok = (seq ^ inv) == 0xFF
        if ok:
            ok = crc16(data) == int.from_bytes(tail, "big") if self._crc else (sum(data) & 0xFF) == tail[0]
        if not ok:
            self._rcv_error()
            return
        if self._state == "handshake":
            self._state = "recv"
        self._eot_seen = False
        exp = 0 if self._want_header else self._expect & 0xFF
        if seq == exp:
            self._errors = 0
            if self._want_header:
                self._rcv_header(data)
            else:
                self._buf += data
                self._expect += 1
                self._write(bytes([ACK]))
                self.progress.emit(len(self._buf), self._size or 0)
        elif seq == (exp - 1) & 0xFF and not self._want_header:
            self._write(bytes([ACK]))               # duplicato: ACK perso, non si risalva il blocco
            if self._ymodem and seq == 0 and not self._buf:
                self._write(bytes([CRC_REQ]))
        else:
            self._fail_abort("Numero di blocco non valido")

    def _rcv_header(self, data: bytes):
        name = data.split(b"\0", 1)[0]
        if not name:
            self._write(bytes([ACK]))
            self._finish(True, f"Ricevuti {self._nfiles} file")
            return
        rest = data[len(name) + 1:].split(b"\0", 1)[0].split()
        self._size = int(rest[0]) if rest and rest[0].isdigit() else None
        self._name = name.decode("utf-8", "replace")
        self._buf = bytearray()
        self._want_header = False
        self._expect = 1
        self.status.emit(f"Ricezione {self._name}")
        self._write(bytes([ACK]))
        self._write(bytes([CRC_REQ]))

    def _rcv_file_done(self):
        data = bytes(self._buf)
        if self._ymodem and self._size is not None:
            data = data[:self._size]
        elif self._strip:
            data = data.rstrip(bytes([PAD]))
        self._nfiles += 1
        self.file_received.emit(self._name, data)

    def _rcv_eot(self):
        if self._state == "handshake":
            self._state = "recv"
        if self._ymodem:
            if not self._eot_seen:
                self._eot_seen = True
                self._write(bytes([NAK]))
                return
            self._write(bytes([ACK]))
            self._rcv_file_done()
            self._want_header = True
            self._eot_seen = False
            self._write(bytes([CRC_REQ]))
            self.status.emit("File ricevuto, in attesa del successivo")
        else:
            self._write(bytes([ACK]))
            self._rcv_file_done()
            self._finish(True, "Completato")

    def _rcv_timeout(self):
        if self._state == "handshake":
            self._rcv_handshake()
            return
        if self._state != "recv":
            return
        if self._rx:                      # blocco incompleto: silenzio a meta' frame
            self._rcv_error()
            return
        self._errors += 1
        if self._errors > self.max_retries:
            self._fail_abort("Timeout: il mittente non risponde")
            return
        if self._want_header or (self._expect == 1 and not self._buf):
            self._write(self._hs_char())
        else:
            self._write(bytes([NAK]))
        self._arm(self.block_timeout)
