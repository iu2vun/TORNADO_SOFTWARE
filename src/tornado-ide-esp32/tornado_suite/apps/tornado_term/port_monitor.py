"""Monitor delle porte seriali con rilevamento hotplug.

Poll periodico di QSerialPortInfo: emette portsChanged solo quando l'insieme
delle porte cambia (device collegato/scollegato). Equivale, cross-platform,
al WM_DEVICECHANGE/USBArrival del mondo Win32, senza gestire messaggi nativi.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .transport import list_ports


class PortMonitor(QObject):
    """Segnala l'elenco porte quando cambia."""

    portsChanged = Signal(list)  # list[(nome, dettaglio)]

    def __init__(self, interval_ms: int = 1500, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self._last_key: list[str] | None = None

    def start(self) -> None:
        self._poll()  # emissione immediata all'avvio
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        ports = list_ports()
        key = [name for name, _ in ports]
        if key != self._last_key:
            self._last_key = key
            self.portsChanged.emit(ports)
