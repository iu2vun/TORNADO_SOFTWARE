"""TerminalPanel: il terminale di Tornado-Term come widget incorporabile in altre GUI.

Esempio (GUI LibreSDR)::

    from tornado_suite.apps.tornado_term.panel import TerminalPanel
    term = TerminalPanel("libresdr")              # chiave QSettings: ricorda porta/baud/IP
    layout.addWidget(term)
    term.connect_serial("COM7", 115200)           # console UART di debug
    # oppure
    term.connect_tcp("192.168.1.50", 5000)        # console testuale via Ethernet
    term.send_line("status")
    term.rx.connect(lambda data: ...)             # byte ricevuti
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from ...core.theme import P
from .serial_core import PortWatcher
from .session import Session


class TerminalPanel(QWidget):
    rx = Signal(bytes)                  # byte ricevuti
    tx = Signal(bytes)                  # byte inviati
    connection_changed = Signal(bool)   # True = connesso

    def __init__(self, key: str = "embedded", parent: QWidget | None = None, settings: QSettings | None = None,
                 show_settings: bool = False):
        super().__init__(parent)
        self.settings = settings or QSettings("TornadoLaboratori", "Tornado-Term")
        self.watcher = PortWatcher(1000, self)
        self.session = Session(9, self.settings, lambda p, me: False, self, prefix=f"{key}/")
        s = self.session

        self.lbl = QLabel("disconnesso")
        self.lbl.setStyleSheet(f"color:{P.text_dim};")
        self.btn_conn = QPushButton("Connetti")
        self.btn_set = QPushButton("Impostazioni"); self.btn_set.setCheckable(True)
        self.btn_log = QPushButton("Log"); self.btn_log.setCheckable(True)
        self.btn_hex = QPushButton("Hex")
        btn_clear = QPushButton("Pulisci")
        bar = QHBoxLayout(); bar.setContentsMargins(0, 0, 0, 0)
        for w in (self.btn_conn, self.btn_set, self.btn_log, self.btn_hex, btn_clear):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.lbl)

        side = QScrollArea(); side.setWidgetResizable(True); side.setFrameShape(QScrollArea.Shape.NoFrame)
        side.setWidget(s.panel); side.setMinimumWidth(260)
        self._side = side
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(s.term); split.addWidget(side)
        split.setStretchFactor(0, 1); split.setSizes([800, 280])
        split.setChildrenCollapsible(False)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar); lay.addWidget(split, 1)
        side.setVisible(show_settings)
        self.btn_set.setChecked(show_settings)

        self.btn_conn.clicked.connect(s._toggle_connect)
        self.btn_set.toggled.connect(side.setVisible)
        self.btn_log.clicked.connect(self._toggle_log)
        self.btn_hex.clicked.connect(lambda: s.show_hex(self))
        btn_clear.clicked.connect(s.term.clear)
        s.summary_changed.connect(self._refresh)
        s.message.connect(lambda t, ms: self.lbl.setText(t))
        s.scan_requested.connect(lambda: self.watcher.refresh(force=True))
        s.link.data_received.connect(self.rx)
        s.link.data_sent.connect(self.tx)
        s.link.opened.connect(lambda _p: self.connection_changed.emit(True))
        s.link.closed.connect(lambda _r: self.connection_changed.emit(False))
        self.watcher.ports_changed.connect(s.set_ports)
        self.watcher.ports_changed.connect(s.link.on_ports_changed)
        self.watcher.start()
        self._refresh()

    # --- API -----------------------------------------------------------------------
    @property
    def terminal(self):
        return self.session.term

    @property
    def is_connected(self) -> bool:
        return self.session.link.is_open

    def connect_serial(self, port: str, baud: int = 115200, data_bits: str = "8", parity: str = "None",
                       stop_bits: str = "1", flow: str = "None") -> bool:
        return self.session.open_serial(port, baud, data_bits, parity, stop_bits, flow)

    def connect_tcp(self, host: str, port: int) -> bool:
        return self.session.open_tcp(host, port)

    def disconnect_link(self) -> None:
        self.session.disconnect_link()

    def send(self, data: bytes | str) -> int:
        return self.session.send_bytes(data.encode("utf-8") if isinstance(data, str) else bytes(data))

    def send_line(self, text: str) -> int:
        return self.session.send_text(text, True)

    def start_log(self, path: str = "", hex_mode: bool = False) -> bool:
        ok = self.session.start_log(self, hex_mode, path)
        self.btn_log.setChecked(self.session.log.active)
        return ok

    def stop_log(self) -> None:
        self.session.stop_log()
        self.btn_log.setChecked(False)

    def shutdown(self) -> None:
        self.session.save_settings()
        self.watcher.stop()
        self.session.shutdown()

    # --- interno ---------------------------------------------------------------------
    def _toggle_log(self):
        if self.session.log.active:
            self.stop_log()
        else:
            self.start_log()

    def _refresh(self):
        s = self.session
        up = s.link.is_open
        waiting = s.link.waiting_reconnect
        self.btn_conn.setText("Disconnetti" if up or waiting else "Connetti")
        self.lbl.setText(s.summary().split(" ", 1)[1] if " " in s.summary() else s.summary())

    def closeEvent(self, e):
        self.shutdown()
        super().closeEvent(e)
