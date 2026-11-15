"""Core seriale di Tornado-Term: scansione porte, parametri, linee di controllo, riconnessione."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo

SP = QSerialPort

STANDARD_BAUDS = [
    300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 115200,
    230400, 460800, 500000, 576000, 921600, 1000000, 1500000, 2000000, 3000000,
]

DATA_BITS = {
    "5": SP.DataBits.Data5, "6": SP.DataBits.Data6,
    "7": SP.DataBits.Data7, "8": SP.DataBits.Data8,
}
PARITY = {
    "None": SP.Parity.NoParity, "Even": SP.Parity.EvenParity, "Odd": SP.Parity.OddParity,
    "Mark": SP.Parity.MarkParity, "Space": SP.Parity.SpaceParity,
}
STOP_BITS = {
    "1": SP.StopBits.OneStop, "1.5": SP.StopBits.OneAndHalfStop, "2": SP.StopBits.TwoStop,
}
FLOW = {
    "None": SP.FlowControl.NoFlowControl,
    "RTS/CTS": SP.FlowControl.HardwareControl,
    "XON/XOFF": SP.FlowControl.SoftwareControl,
}

_PIN = SP.PinoutSignal
_PIN_MAP = {
    "CTS": _PIN.ClearToSendSignal, "DSR": _PIN.DataSetReadySignal,
    "DCD": _PIN.DataCarrierDetectSignal, "RI": _PIN.RingIndicatorSignal,
    "RTS": _PIN.RequestToSendSignal, "DTR": _PIN.DataTerminalReadySignal,
}


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


@dataclass(frozen=True)
class PortInfo:
    name: str
    description: str = ""
    manufacturer: str = ""
    vid: int | None = None
    pid: int | None = None
    serial: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} - {self.description}" if self.description else self.name


def list_ports() -> list[PortInfo]:
    out = []
    for i in QSerialPortInfo.availablePorts():
        out.append(PortInfo(
            name=i.portName(),
            description=i.description(),
            manufacturer=i.manufacturer(),
            vid=i.vendorIdentifier() if i.hasVendorIdentifier() else None,
            pid=i.productIdentifier() if i.hasProductIdentifier() else None,
            serial=i.serialNumber(),
        ))
    out.sort(key=lambda p: _natural_key(p.name))
    return out


class PortWatcher(QObject):
    """Rileva inserimento/rimozione porte (polling) ed emette l'elenco aggiornato."""

    ports_changed = Signal(list)  # list[PortInfo]

    def __init__(self, interval_ms: int = 1000, parent=None):
        super().__init__(parent)
        self._names: tuple[str, ...] | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.refresh)

    def start(self):
        self.refresh(force=True)
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def refresh(self, force: bool = False):
        ports = list_ports()
        names = tuple(p.name for p in ports)
        if force or names != self._names:
            self._names = names
            self.ports_changed.emit(ports)


@dataclass(frozen=True)
class SerialConfig:
    port: str = ""
    baud: int = 115200
    data_bits: str = "8"
    parity: str = "None"
    stop_bits: str = "1"
    flow: str = "None"


class SerialLink(QObject):
    """Wrapper di QSerialPort con riconfigurazione a caldo, linee modem, break e auto-riconnessione."""

    opened = Signal(str)            # nome porta
    closed = Signal(str)            # motivo ("" = chiusura utente)
    error = Signal(str)
    data_received = Signal(bytes)
    pins_changed = Signal(dict)     # {"CTS": bool, ...}
    stats_changed = Signal(int, int)  # rx, tx
    reconnect_pending = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._port = SP(self)
        self._port.readyRead.connect(self._on_ready_read)
        self._port.errorOccurred.connect(self._on_error)
        self._cfg = SerialConfig()
        self._want_open = False
        self._lost = False
        self._opening = False
        self._rts = True
        self._dtr = True
        self._pins: dict[str, bool] = {}
        self._rx = 0
        self._tx = 0
        self.auto_reconnect = True
        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._poll_pins)

    # --- stato -------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._port.isOpen()

    @property
    def config(self) -> SerialConfig:
        return self._cfg

    @property
    def waiting_reconnect(self) -> bool:
        return self._want_open and self._lost

    # --- apertura / chiusura ----------------------------------------------
    def open(self, cfg: SerialConfig) -> bool:
        if self.is_open:
            self._shutdown("")
        self._cfg = cfg
        self._want_open = True
        self._lost = False
        return self._do_open()

    def _do_open(self) -> bool:
        p = self._port
        cfg = self._cfg
        p.setPortName(cfg.port)
        self._apply_params(cfg)
        self._opening = True
        ok = p.open(SP.OpenModeFlag.ReadWrite)
        self._opening = False
        if not ok:
            self.error.emit(f"{cfg.port}: {p.errorString()}")
            self._want_open = False
            return False
        p.setReadBufferSize(0)
        self._apply_lines()
        self._pins = {}
        self._poll.start()
        self._poll_pins()
        self.opened.emit(cfg.port)
        return True

    def close(self):
        """Chiusura richiesta dall'utente: disabilita anche la riconnessione."""
        self._want_open = False
        self._lost = False
        if self.is_open:
            self._shutdown("")

    def _shutdown(self, reason: str):
        self._poll.stop()
        if self._port.isOpen():
            self._port.close()
        self._pins = {}
        self.closed.emit(reason)

    # --- parametri ---------------------------------------------------------
    def _apply_params(self, cfg: SerialConfig):
        p = self._port
        p.setBaudRate(int(cfg.baud))
        p.setDataBits(DATA_BITS[cfg.data_bits])
        p.setParity(PARITY[cfg.parity])
        p.setStopBits(STOP_BITS[cfg.stop_bits])
        p.setFlowControl(FLOW[cfg.flow])

    def reconfigure(self, cfg: SerialConfig):
        """Applica i parametri a caldo (stessa porta) o riapre se la porta cambia."""
        old = self._cfg
        self._cfg = cfg
        if not self.is_open:
            return
        if cfg.port != old.port:
            self.open(cfg)
            return
        self._apply_params(cfg)
        if cfg.flow != old.flow:
            self._apply_lines()

    def _apply_lines(self):
        if not self.is_open:
            return
        self._port.setDataTerminalReady(self._dtr)
        if self._cfg.flow != "RTS/CTS":  # con flusso hardware RTS è gestito dal driver
            self._port.setRequestToSend(self._rts)

    def set_dtr(self, on: bool):
        self._dtr = on
        if self.is_open:
            self._port.setDataTerminalReady(on)
            self._poll_pins()

    def set_rts(self, on: bool):
        self._rts = on
        if self.is_open and self._cfg.flow != "RTS/CTS":
            self._port.setRequestToSend(on)
            self._poll_pins()

    @property
    def rts(self) -> bool:
        return self._rts

    @property
    def dtr(self) -> bool:
        return self._dtr

    def send_break(self, ms: int = 250):
        if not self.is_open:
            return
        self._port.setBreakEnabled(True)
        QTimer.singleShot(ms, self._break_off)

    def _break_off(self):
        if self._port.isOpen():
            self._port.setBreakEnabled(False)

    # --- I/O ---------------------------------------------------------------
    def write(self, data: bytes) -> int:
        if not self.is_open or not data:
            return 0
        n = self._port.write(data)
        if n > 0:
            self._tx += n
            self.stats_changed.emit(self._rx, self._tx)
        return max(n, 0)

    def bytes_to_write(self) -> int:
        return int(self._port.bytesToWrite()) if self.is_open else 0

    def reset_stats(self):
        self._rx = self._tx = 0
        self.stats_changed.emit(0, 0)

    def flush_buffers(self):
        if self.is_open:
            self._port.clear(SP.Direction.AllDirections)

    def _on_ready_read(self):
        data = bytes(self._port.readAll())
        if data:
            self._rx += len(data)
            self.data_received.emit(data)
            self.stats_changed.emit(self._rx, self._tx)

    # --- errori / riconnessione -------------------------------------------
    def _on_error(self, err):
        E = SP.SerialPortError
        if err == E.NoError or self._opening:
            return
        if err in (E.ResourceError, E.DeviceNotFoundError):
            msg = self._port.errorString()
            if self._want_open and self.auto_reconnect:
                self._lost = True
                self._shutdown(msg)
                self.reconnect_pending.emit(self._cfg.port)
            else:
                self._want_open = False
                self._shutdown(msg)
            return
        if err == E.TimeoutError:
            return
        self.error.emit(f"{self._cfg.port}: {self._port.errorString()}")

    def on_ports_changed(self, ports: list[PortInfo]):
        """Da collegare a PortWatcher.ports_changed: riapre la porta se ricompare."""
        if not (self._lost and self._want_open and self.auto_reconnect):
            return
        if any(p.name == self._cfg.port for p in ports):
            QTimer.singleShot(400, self._try_reopen)

    def _try_reopen(self):
        if self._lost and self._want_open and not self.is_open:
            self._lost = False
            if not self._do_open():
                self._lost = False

    # --- linee modem -------------------------------------------------------
    def _poll_pins(self):
        if not self.is_open:
            return
        sig = self._port.pinoutSignals()
        cur = {k: bool(sig & v) for k, v in _PIN_MAP.items()}
        if cur != self._pins:
            self._pins = cur
            self.pins_changed.emit(dict(cur))

    def with_port(self, port: str) -> SerialConfig:
        return replace(self._cfg, port=port)
