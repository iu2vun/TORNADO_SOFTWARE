"""Collegamenti del terminale: seriale (SerialLink) e TCP (TcpLink) dietro un'unica interfaccia (LinkMux).

Session, trasferimenti e macro usano solo `link.write`, i segnali e le proprietà qui sotto,
quindi funzionano uguali su COM e su socket.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from .serial_core import PortInfo, SerialConfig, SerialLink

RETRY_MS = 2000


@dataclass(frozen=True)
class TcpConfig:
    host: str = "192.168.1.100"
    tcp_port: int = 23

    @property
    def port(self) -> str:
        return f"{self.host}:{self.tcp_port}"


class TcpLink(QObject):
    """Client TCP con la stessa interfaccia di SerialLink (linee modem e break assenti)."""

    opened = Signal(str)
    closed = Signal(str)
    error = Signal(str)
    data_received = Signal(bytes)
    pins_changed = Signal(dict)
    stats_changed = Signal(int, int)
    reconnect_pending = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = QTcpSocket(self)
        self._sock.setSocketOption(QAbstractSocket.SocketOption.LowDelayOption, 1)
        self._sock.setSocketOption(QAbstractSocket.SocketOption.KeepAliveOption, 1)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.errorOccurred.connect(self._on_error)
        self._cfg = TcpConfig()
        self._want_open = False
        self._rx = self._tx = 0
        self.auto_reconnect = True
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(RETRY_MS)
        self._retry.timeout.connect(self._connect)

    # --- stato -----------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._sock.state() == QAbstractSocket.SocketState.ConnectedState

    @property
    def config(self) -> TcpConfig:
        return self._cfg

    @property
    def waiting_reconnect(self) -> bool:
        """Connessione in corso o in attesa di nuovo tentativo."""
        return self._want_open and not self.is_open

    # --- apertura / chiusura ---------------------------------------------------
    def open(self, cfg: TcpConfig) -> bool:
        self.close()
        if not cfg.host.strip() or not (0 < cfg.tcp_port < 65536):
            self.error.emit("indirizzo o porta TCP non validi")
            return False
        self._cfg = cfg
        self._want_open = True
        self._connect()
        return True

    def _connect(self):
        if not self._want_open or self._sock.state() != QAbstractSocket.SocketState.UnconnectedState:
            return
        self._sock.connectToHost(self._cfg.host.strip(), self._cfg.tcp_port)

    def close(self):
        self._want_open = False
        self._retry.stop()
        if self._sock.state() != QAbstractSocket.SocketState.UnconnectedState:
            was_open = self.is_open
            self._sock.abort()
            if was_open:
                self.closed.emit("")

    def reconfigure(self, cfg):  # i parametri TCP si applicano alla connessione successiva
        self._cfg = cfg

    # --- I/O ---------------------------------------------------------------------
    def write(self, data: bytes) -> int:
        if not self.is_open or not data:
            return 0
        n = self._sock.write(bytes(data))
        if n > 0:
            self._tx += n
            self.stats_changed.emit(self._rx, self._tx)
        return max(n, 0)

    def bytes_to_write(self) -> int:
        return int(self._sock.bytesToWrite()) if self.is_open else 0

    def reset_stats(self):
        self._rx = self._tx = 0
        self.stats_changed.emit(0, 0)

    def flush_buffers(self):
        if self.is_open:
            self._sock.readAll()

    def set_dtr(self, on: bool): ...
    def set_rts(self, on: bool): ...
    def send_break(self, ms: int = 250): ...
    rts = True
    dtr = True

    def on_ports_changed(self, ports: list[PortInfo]): ...

    # --- eventi socket -----------------------------------------------------------
    def _on_connected(self):
        self.opened.emit(self._cfg.port)

    def _on_disconnected(self):
        if self._want_open and self.auto_reconnect:
            self.closed.emit("connessione persa")
            self.reconnect_pending.emit(self._cfg.port)
            self._retry.start()
        elif self._want_open:
            self._want_open = False
            self.closed.emit("connessione chiusa dal peer")

    def _on_ready_read(self):
        data = bytes(self._sock.readAll())
        if data:
            self._rx += len(data)
            self.data_received.emit(data)
            self.stats_changed.emit(self._rx, self._tx)

    def _on_error(self, _err):
        if self._sock.state() != QAbstractSocket.SocketState.UnconnectedState:
            return                      # errore durante una connessione viva: segue disconnected()
        msg = self._sock.errorString()
        if self._want_open and self.auto_reconnect:
            self.reconnect_pending.emit(f"{self._cfg.port} ({msg})")
            self._retry.start()
        else:
            self._want_open = False
            self.error.emit(f"{self._cfg.port}: {msg}")
            self.closed.emit(msg)


Config = Union[SerialConfig, TcpConfig]


class LinkMux(QObject):
    """Presenta il collegamento attivo (seriale o TCP) con segnali e metodi unificati."""

    opened = Signal(str)
    closed = Signal(str)
    error = Signal(str)
    data_received = Signal(bytes)
    data_sent = Signal(bytes)
    pins_changed = Signal(dict)
    stats_changed = Signal(int, int)
    reconnect_pending = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.serial = SerialLink(self)
        self.tcp = TcpLink(self)
        self.active: Union[SerialLink, TcpLink] = self.serial
        for ln in (self.serial, self.tcp):
            for name in ("opened", "closed", "error", "data_received", "pins_changed", "stats_changed",
                         "reconnect_pending"):
                getattr(ln, name).connect(getattr(self, name))
        self._auto = True

    # --- stato ---------------------------------------------------------------------
    @property
    def kind(self) -> str:
        return "tcp" if self.active is self.tcp else "serial"

    @property
    def is_open(self) -> bool:
        return self.active.is_open

    @property
    def config(self):
        return self.active.config

    @property
    def waiting_reconnect(self) -> bool:
        return self.active.waiting_reconnect

    @property
    def auto_reconnect(self) -> bool:
        return self._auto

    @auto_reconnect.setter
    def auto_reconnect(self, on: bool):
        self._auto = bool(on)
        self.serial.auto_reconnect = self.tcp.auto_reconnect = self._auto

    @property
    def rts(self) -> bool:
        return self.active.rts

    @property
    def dtr(self) -> bool:
        return self.active.dtr

    # --- apertura ---------------------------------------------------------------------
    def open(self, cfg: Config) -> bool:
        new = self.tcp if isinstance(cfg, TcpConfig) else self.serial
        if new is not self.active:
            self.active.close()
            self.active = new
        return self.active.open(cfg)

    def close(self):
        self.serial.close()
        self.tcp.close()

    def reconfigure(self, cfg: Config):
        if (self.active is self.tcp) == isinstance(cfg, TcpConfig):
            self.active.reconfigure(cfg)

    # --- I/O --------------------------------------------------------------------------
    def write(self, data: bytes) -> int:
        n = self.active.write(data)
        if n > 0:
            self.data_sent.emit(bytes(data[:n]))
        return n

    def bytes_to_write(self) -> int:
        return self.active.bytes_to_write()

    def reset_stats(self):
        self.serial.reset_stats()
        self.tcp.reset_stats()

    def flush_buffers(self):
        self.active.flush_buffers()

    def set_dtr(self, on: bool):
        self.serial.set_dtr(on)

    def set_rts(self, on: bool):
        self.serial.set_rts(on)

    def send_break(self, ms: int = 250):
        self.serial.send_break(ms)

    def on_ports_changed(self, ports: list[PortInfo]):
        self.serial.on_ports_changed(ports)
