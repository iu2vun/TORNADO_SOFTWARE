"""Collegamento USB-seriale con il display Waveshare ESP32-S3-LCD-4.3.

Protocollo: una riga JSON (UTF-8, terminata da '\\n') per messaggio, 115200 8N1.

PC -> ESP32   {"t":"s", ...stato...}                      (a ogni nuovo stato VNA e almeno ogni 1 s)
              {"t":"ev","m":"testo"}                      (evento del registro di stato)
ESP32 -> PC   {"c":"hello"}                               (richiede subito lo stato)
              {"c":"run","v":1|0}                         (Continuo / Hold)
              {"c":"single"}  {"c":"preset"}  {"c":"calref"}
              {"c":"set","k":"start|stop|pts|ifbw|lvl|avg","v":numero}   (start/stop in Hz)

Il PC resta l'unico proprietario del VNA: l'ESP32 non parla mai col VNA direttamente.
"""
from __future__ import annotations

import json
import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("tornadovna.esp")

try:
    import serial
    from serial.tools import list_ports
except Exception:          # pyserial assente: il resto dell'app funziona comunque
    serial = None
    list_ports = None

BAUD = 115200
KEYS = ("start", "stop", "pts", "ifbw", "lvl", "avg")


def available_ports() -> list[tuple[str, str]]:
    if list_ports is None:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


class EspLink(QObject):
    command = Signal(dict)          # comando valido ricevuto dall'ESP32
    stateChanged = Signal(bool, str)  # connesso, testo

    def __init__(self, get_state, parent=None):
        super().__init__(parent)
        self._get_state = get_state     # callable -> dict di stato da inviare
        self._ser = None
        self._buf = b""
        self._last_tx = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)

    # ------------------------------------------------------------------ porta
    @property
    def is_open(self) -> bool:
        return self._ser is not None

    def open(self, port: str) -> bool:
        if serial is None:
            self.stateChanged.emit(False, "pyserial non installato")
            return False
        self.close()
        try:
            # DTR/RTS a False: sulle schede con CH343/USB-UART evita il reset dell'ESP32 all'apertura
            s = serial.Serial()
            s.port, s.baudrate, s.timeout, s.write_timeout = port, BAUD, 0, 0.2
            s.dtr = False
            s.rts = False
            s.open()
            self._ser = s
        except Exception as e:
            self._ser = None
            self.stateChanged.emit(False, f"{port}: {e}")
            return False
        self._buf = b""
        self._timer.start()
        self.stateChanged.emit(True, port)
        self.send_state()
        return True

    def close(self):
        self._timer.stop()
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            self.stateChanged.emit(False, "chiuso")

    # ------------------------------------------------------------------ TX
    def _write(self, obj: dict):
        if self._ser is None:
            return
        try:
            self._ser.write((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))
        except Exception as e:
            log.warning("scrittura seriale: %s", e)
            self._lost(str(e))

    def send_state(self):
        d = dict(self._get_state())
        d["t"] = "s"
        self._write(d)
        self._last_tx = time.monotonic()

    def send_event(self, text: str):
        self._write({"t": "ev", "m": text[:120]})

    # ------------------------------------------------------------------ RX
    def _poll(self):
        s = self._ser
        if s is None:
            return
        try:
            n = s.in_waiting
            if n:
                self._buf += s.read(n)
        except Exception as e:
            self._lost(str(e))
            return
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if line:
                self._handle(line)
        if len(self._buf) > 4096:           # spazzatura senza newline
            self._buf = b""
        if time.monotonic() - self._last_tx >= 1.0:
            self.send_state()

    def _handle(self, line: bytes):
        try:
            m = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            return                          # righe di log dell'ESP32: ignorate
        if not isinstance(m, dict):
            return
        c = m.get("c")
        if c == "hello":
            self.send_state()
        elif c in ("run", "single", "preset", "calref"):
            self.command.emit(m)
        elif c == "set" and m.get("k") in KEYS and isinstance(m.get("v"), (int, float)):
            self.command.emit(m)

    def _lost(self, why: str):
        self.close()
        self.stateChanged.emit(False, f"persa: {why}")


from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout  # noqa: E402


class EspDialog(QDialog):
    """Scelta porta COM del display e connessione/disconnessione."""

    def __init__(self, link: EspLink, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Display ESP32")
        self._link = link
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Porta USB-seriale del display Waveshare ESP32-S3-LCD-4.3:"))
        row = QHBoxLayout()
        self.cmb = QComboBox()
        self.cmb.setMinimumWidth(320)
        b = QPushButton("Aggiorna")
        b.clicked.connect(self.refresh)
        row.addWidget(self.cmb, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.lbl = QLabel("")
        self.lbl.setObjectName("Dim")
        v.addWidget(self.lbl)
        self.btn = QPushButton("Connetti")
        self.btn.setProperty("primary", True)
        self.btn.clicked.connect(self._toggle)
        v.addWidget(self.btn)
        link.stateChanged.connect(self._on_state)

    def refresh(self):
        cur = self.cmb.currentData()
        self.cmb.clear()
        for dev, desc in available_ports():
            self.cmb.addItem(f"{dev}  -  {desc}", dev)
        i = self.cmb.findData(cur)
        if i >= 0:
            self.cmb.setCurrentIndex(i)
        if serial is None:
            self.lbl.setText("pyserial non installato (pip install pyserial)")
        self._on_state(self._link.is_open, "")

    def _toggle(self):
        if self._link.is_open:
            self._link.close()
        elif self.cmb.currentData():
            self._link.open(self.cmb.currentData())

    def _on_state(self, ok, text):
        self.btn.setText("Disconnetti" if ok else "Connetti")
        if text:
            self.lbl.setText(("Connesso: " if ok else "") + text)
