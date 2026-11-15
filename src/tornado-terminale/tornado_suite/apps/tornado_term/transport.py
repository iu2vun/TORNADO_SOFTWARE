"""Trasporto seriale su QSerialPort.

TransportBase e' l'astrazione comune (cfr. CommClassBase del progetto VB):
open/close/write + segnali dataReceived/opened/closed/errorOccurred, con
capability flag has_baudrate/has_pins. Un TransportTcp futuro implementa la
stessa interfaccia e il resto dell'app non cambia.

QSerialPort e' asincrono e vive sull'event loop Qt: nessun thread, il
readyRead arriva come segnale.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo


class TransportBase(QObject):
    """Interfaccia di trasporto comune a seriale e (in futuro) TCP."""

    dataReceived = Signal(bytes)
    opened = Signal()
    closed = Signal()
    errorOccurred = Signal(str)

    has_baudrate: bool = False
    has_pins: bool = False

    def open(self) -> bool:  # pragma: no cover - override
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - override
        raise NotImplementedError

    def write(self, data: bytes) -> None:  # pragma: no cover - override
        raise NotImplementedError

    @property
    def is_open(self) -> bool:  # pragma: no cover - override
        raise NotImplementedError


# Mappe nome-leggibile -> enum Qt, per popolare i combo della toolbar.
PARITY = {
    "None": QSerialPort.Parity.NoParity,
    "Even": QSerialPort.Parity.EvenParity,
    "Odd": QSerialPort.Parity.OddParity,
    "Mark": QSerialPort.Parity.MarkParity,
    "Space": QSerialPort.Parity.SpaceParity,
}

STOP_BITS = {
    "1": QSerialPort.StopBits.OneStop,
    "1.5": QSerialPort.StopBits.OneAndHalfStop,
    "2": QSerialPort.StopBits.TwoStop,
}

DATA_BITS = {
    "5": QSerialPort.DataBits.Data5,
    "6": QSerialPort.DataBits.Data6,
    "7": QSerialPort.DataBits.Data7,
    "8": QSerialPort.DataBits.Data8,
}

FLOW = {
    "None": QSerialPort.FlowControl.NoFlowControl,
    "RTS/CTS": QSerialPort.FlowControl.HardwareControl,
    "XON/XOFF": QSerialPort.FlowControl.SoftwareControl,
}

COMMON_BAUDS = [
    9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1500000, 2000000,
]


def list_ports() -> list[tuple[str, str]]:
    """Porte disponibili come (nome_sistema, descrizione)."""
    out = []
    for info in QSerialPortInfo.availablePorts():
        desc = info.description() or ""
        manu = info.manufacturer() or ""
        label = f"{info.portName()}"
        detail = " ".join(p for p in (desc, manu) if p)
        out.append((info.portName(), detail or label))
    return out


class SerialTransport(TransportBase):
    """Trasporto su porta seriale fisica/virtuale (USB-CDC incluso)."""

    has_baudrate = True
    has_pins = True

    def __init__(
        self,
        port_name: str,
        baudrate: int = 115200,
        *,
        data_bits: str = "8",
        parity: str = "None",
        stop_bits: str = "1",
        flow: str = "None",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._port = QSerialPort(self)
        self._port.setPortName(port_name)
        self._port.setBaudRate(baudrate)
        self._port.setDataBits(DATA_BITS[data_bits])
        self._port.setParity(PARITY[parity])
        self._port.setStopBits(STOP_BITS[stop_bits])
        self._port.setFlowControl(FLOW[flow])
        self._port.readyRead.connect(self._on_ready_read)
        self._port.errorOccurred.connect(self._on_error)

    def open(self) -> bool:
        if self._port.isOpen():
            return True
        ok = self._port.open(QSerialPort.OpenModeFlag.ReadWrite)
        if ok:
            self._port.clear()
            self.opened.emit()
        else:
            self.errorOccurred.emit(self._port.errorString())
        return ok

    def close(self) -> None:
        if self._port.isOpen():
            self._port.close()
            self.closed.emit()

    def write(self, data: bytes) -> None:
        if self._port.isOpen() and data:
            self._port.write(data)

    @property
    def is_open(self) -> bool:
        return self._port.isOpen()

    @property
    def baudrate(self) -> int:
        return self._port.baudRate()

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        # Cambio a caldo: usato dai wizard bootloader che cambiano baud a meta'.
        self._port.setBaudRate(value)

    def set_pins(self, *, dtr: bool | None = None, rts: bool | None = None) -> None:
        """Controllo linee DTR/RTS. ESP32/nodi in boot le usano per reset/boot."""
        if dtr is not None:
            self._port.setDataTerminalReady(dtr)
        if rts is not None:
            self._port.setRequestToSend(rts)

    def _on_ready_read(self) -> None:
        data = self._port.readAll()
        if data:
            self.dataReceived.emit(bytes(data.data()))

    def _on_error(self, error) -> None:
        if error == QSerialPort.SerialPortError.NoError:
            return
        msg = self._port.errorString()
        self.errorOccurred.emit(msg)
        # Errori di risorsa = device scollegato: chiudi in modo pulito.
        if error in (
            QSerialPort.SerialPortError.ResourceError,
            QSerialPort.SerialPortError.PermissionError,
        ):
            self.close()
