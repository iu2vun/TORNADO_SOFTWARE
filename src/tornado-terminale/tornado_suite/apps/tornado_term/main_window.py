"""Finestra principale del terminale.

Mette insieme engine, widget di rendering, trasporto seriale, monitor porte
hotplug e splash. Nessuna logica di protocollo qui dentro: solo orchestrazione.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollBar, QStatusBar, QToolBar, QWidget,
)

from .ansi_engine import ENCODINGS, AnsiEngine, HexDumpMode
from .port_monitor import PortMonitor
from .splash import render_splash
from .terminal_widget import ResizeMode, TerminalWidget
from .transport import COMMON_BAUDS, DATA_BITS, FLOW, PARITY, STOP_BITS, SerialTransport

# Fine riga inviato su Invio.
LINE_ENDINGS = {
    "CR (\\r)": b"\r",
    "LF (\\n)": b"\n",
    "CRLF (\\r\\n)": b"\r\n",
}


class MainWindow(QMainWindow):
    def __init__(
        self,
        app_name: str = "Terminale",
        app_version: str = "0.4",
        callsign: str | None = None,
    ) -> None:
        super().__init__()
        self._app_name = app_name
        self._app_version = app_version
        self._callsign = callsign

        self.engine = AnsiEngine(cols=80, rows=24)
        self.term = TerminalWidget(self.engine)
        self.transport: SerialTransport | None = None

        self._build_central()
        self._build_toolbar()
        self._build_shortcuts()

        self.setStatusBar(QStatusBar())
        self.engine.titleChanged.connect(self._on_title)
        self.engine.bell.connect(lambda: QApplication.beep())
        self.setWindowTitle(app_name)

        self.term.dataToSend.connect(self._on_data_to_send)
        self.scrollbar.valueChanged.connect(self._on_scrollbar)
        self.engine.scrollChanged.connect(self._on_scroll_changed)

        # Hotplug porte.
        self.monitor = PortMonitor(interval_ms=1500, parent=self)
        self.monitor.portsChanged.connect(self._on_ports_changed)
        self.monitor.start()

        # Ripristina le impostazioni salvate (porta/baud/font/geometria...).
        self._settings = QSettings()
        self._restore_settings()

        # Splash disegnata nel terminale.
        self.engine.feed(
            render_splash(app_name, app_version, callsign, width=80)
        )
        self._refresh_status()

    # ---- persistenza impostazioni --------------------------------------

    def _restore_settings(self) -> None:
        s = self._settings
        geo = s.value("win/geometry")
        if isinstance(geo, QByteArray) and not geo.isEmpty():
            self.restoreGeometry(geo)
        else:
            self.resize(1040, 660)

        def restore_combo(combo, key, default=None):
            val = s.value(key)
            if val is None:
                if default is not None:
                    combo.setCurrentText(str(default))
                return
            idx = combo.findText(str(val))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif combo.isEditable():
                combo.setCurrentText(str(val))

        restore_combo(self.baud_combo, "conn/baud", "115200")
        restore_combo(self.data_combo, "conn/data", "8")
        restore_combo(self.parity_combo, "conn/parity", "None")
        restore_combo(self.stop_combo, "conn/stop", "1")
        restore_combo(self.flow_combo, "conn/flow", "None")
        restore_combo(self.eol_combo, "opt/eol")
        restore_combo(self.enc_combo, "opt/enc", "UTF-8")
        # La porta si ripristina quando il monitor la ripopola (puo' non
        # esserci ancora): memorizzo il nome desiderato.
        self._wanted_port = s.value("conn/port")

        echo = s.value("opt/echo", False, type=bool)
        self.echo_check.setChecked(echo)
        bs = s.value("opt/backspace_del", True, type=bool)
        self.bs_check.setChecked(bs)
        font = s.value("view/font_hp", 17, type=int)
        self.term.font_size = font

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("win/geometry", self.saveGeometry())
        s.setValue("conn/port", self.port_combo.currentData())
        s.setValue("conn/baud", self.baud_combo.currentText())
        s.setValue("conn/data", self.data_combo.currentText())
        s.setValue("conn/parity", self.parity_combo.currentText())
        s.setValue("conn/stop", self.stop_combo.currentText())
        s.setValue("conn/flow", self.flow_combo.currentText())
        s.setValue("opt/eol", self.eol_combo.currentText())
        s.setValue("opt/enc", self.enc_combo.currentText())
        s.setValue("opt/echo", self.echo_check.isChecked())
        s.setValue("opt/backspace_del", self.bs_check.isChecked())
        s.setValue("view/font_hp", self.term.font_size)

    # ---- costruzione UI -------------------------------------------------

    def _build_central(self) -> None:
        self.scrollbar = QScrollBar(Qt.Vertical)
        self.scrollbar.setEnabled(False)
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.term, 1)
        layout.addWidget(self.scrollbar)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> None:
        def label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("tbLabel")
            return lbl

        # --- Barra 1: parametri di connessione (bloccati mentre connesso) ---
        tb = QToolBar("Connessione")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(label("Porta"))
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(210)
        tb.addWidget(self.port_combo)

        tb.addWidget(label("Baud"))
        self.baud_combo = QComboBox()
        for baud in COMMON_BAUDS:
            self.baud_combo.addItem(str(baud), baud)
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setEditable(True)
        tb.addWidget(self.baud_combo)

        tb.addWidget(label("Dati"))
        self.data_combo = QComboBox()
        for name in DATA_BITS:
            self.data_combo.addItem(name)
        self.data_combo.setCurrentText("8")
        tb.addWidget(self.data_combo)

        tb.addWidget(label("Parità"))
        self.parity_combo = QComboBox()
        for name in PARITY:
            self.parity_combo.addItem(name)
        tb.addWidget(self.parity_combo)

        tb.addWidget(label("Stop"))
        self.stop_combo = QComboBox()
        for name in STOP_BITS:
            self.stop_combo.addItem(name)
        tb.addWidget(self.stop_combo)

        tb.addWidget(label("Flusso"))
        self.flow_combo = QComboBox()
        for name in FLOW:
            self.flow_combo.addItem(name)
        tb.addWidget(self.flow_combo)

        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().Policy.Expanding,
                             spacer.sizePolicy().Policy.Preferred)
        tb.addWidget(spacer)

        self.connect_btn = QPushButton("Connetti")
        self.connect_btn.setObjectName("connectBtn")
        self.connect_btn.setProperty("connected", False)
        self.connect_btn.clicked.connect(self.toggle_connection)
        tb.addWidget(self.connect_btn)

        # --- Barra 2: opzioni runtime (sempre attive) ---
        self.addToolBarBreak()
        tb2 = QToolBar("Opzioni")
        tb2.setMovable(False)
        self.addToolBar(tb2)

        tb2.addWidget(label("Fine riga"))
        self.eol_combo = QComboBox()
        for name in LINE_ENDINGS:
            self.eol_combo.addItem(name)
        self.eol_combo.setCurrentText("CR (\\r)")
        self.eol_combo.currentTextChanged.connect(self._on_eol_changed)
        tb2.addWidget(self.eol_combo)

        tb2.addWidget(label("Codifica"))
        self.enc_combo = QComboBox()
        for name in ENCODINGS:
            self.enc_combo.addItem(name)
        self.enc_combo.setToolTip("Come interpretare i byte del dispositivo e quelli inviati.\n"
                                  "Dispositivi «ANSI» con accenti/simboli: Windows-1252; vecchi apparati DOS: CP437.\n"
                                  "Le sequenze di escape ANSI/VT (colori, cursore) sono sempre interpretate.")
        self.enc_combo.currentTextChanged.connect(self._on_enc_changed)
        tb2.addWidget(self.enc_combo)

        self.echo_check = QCheckBox("Eco locale")
        self.echo_check.toggled.connect(self._on_echo_toggled)
        tb2.addWidget(self.echo_check)

        self.bs_check = QCheckBox("Backspace = DEL")
        self.bs_check.setChecked(True)
        self.bs_check.toggled.connect(self._on_backspace_toggled)
        tb2.addWidget(self.bs_check)

    def _on_enc_changed(self, name: str) -> None:
        self.term._engine.encoding = ENCODINGS[name]

    def _on_eol_changed(self, name: str) -> None:
        self.term.line_ending = LINE_ENDINGS[name]

    def _on_echo_toggled(self, checked: bool) -> None:
        self.term.local_echo = checked

    def _on_backspace_toggled(self, checked: bool) -> None:
        self.term.backspace_is_del = checked

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("F2"), self, self.toggle_hex)
        QShortcut(QKeySequence("F3"), self, self.toggle_resize_mode)
        QShortcut(QKeySequence.ZoomIn, self, lambda: self.bump_font(+1))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self.bump_font(-1))
        QShortcut(QKeySequence("Ctrl+L"), self, self.clear_screen)

    # ---- hotplug porte --------------------------------------------------

    def _on_ports_changed(self, ports: list[tuple[str, str]]) -> None:
        current = self.port_combo.currentData()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        for name, detail in ports:
            text = f"{name}  {detail}".strip() if detail else name
            self.port_combo.addItem(text, name)
        if not ports:
            self.port_combo.addItem("(nessuna porta)", None)
        # Ripristina la selezione: la porta corrente, oppure quella salvata
        # dalla sessione precedente se e' appena ricomparsa.
        target = current or getattr(self, "_wanted_port", None)
        if target:
            idx = self.port_combo.findData(target)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
                self._wanted_port = None  # ripristino consumato
        self.port_combo.blockSignals(False)

        # Se la porta connessa e' sparita, la connessione cadra' da sola
        # via errore seriale; qui aggiorniamo solo la lista.
        self._refresh_status()

    # ---- scrollback -----------------------------------------------------

    def _on_scroll_changed(self, offset: int, max_offset: int) -> None:
        self.scrollbar.blockSignals(True)
        self.scrollbar.setEnabled(max_offset > 0)
        self.scrollbar.setRange(0, max_offset)
        self.scrollbar.setPageStep(max(1, self.engine.rows - 1))
        self.scrollbar.setValue(max_offset - offset)  # invertito: 0 basso = live
        self.scrollbar.blockSignals(False)

    def _on_scrollbar(self, value: int) -> None:
        self.engine.scroll_to(self.scrollbar.maximum() - value)

    # ---- connessione ----------------------------------------------------

    def toggle_connection(self) -> None:
        if self.transport and self.transport.is_open:
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self) -> None:
        port_name = self.port_combo.currentData()
        if not port_name:
            self.statusBar().showMessage("Nessuna porta selezionata")
            return
        try:
            baud = int(self.baud_combo.currentText())
        except ValueError:
            self.statusBar().showMessage("Baud non valido")
            return

        self.transport = SerialTransport(
            port_name,
            baud,
            data_bits=self.data_combo.currentText(),
            parity=self.parity_combo.currentText(),
            stop_bits=self.stop_combo.currentText(),
            flow=self.flow_combo.currentText(),
        )
        self.transport.dataReceived.connect(self.engine.feed)
        self.transport.errorOccurred.connect(self._on_transport_error)
        self.transport.closed.connect(self._on_transport_closed)

        if self.transport.open():
            self._set_connected_ui(True)
            self.term.setFocus()
            self.engine.feed(
                b"\x1b[1;32m--- connesso a %s @ %d ---\x1b[0m\r\n"
                % (port_name.encode(), baud)
            )
        self._refresh_status()

    def disconnect_serial(self) -> None:
        if self.transport:
            self.transport.close()

    def _on_data_to_send(self, data: bytes) -> None:
        if self.transport and self.transport.is_open:
            self.transport.write(data)

    def _on_transport_closed(self) -> None:
        self._set_connected_ui(False)
        self.engine.feed(b"\x1b[1;33m--- disconnesso ---\x1b[0m\r\n")
        self._refresh_status()

    def _on_transport_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"Errore seriale: {msg}")

    def _set_connected_ui(self, connected: bool) -> None:
        self.connect_btn.setText("Disconnetti" if connected else "Connetti")
        self.connect_btn.setProperty("connected", connected)
        # Forza il re-styling dopo il cambio di property.
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)
        for combo in (self.port_combo, self.baud_combo, self.data_combo,
                      self.parity_combo, self.stop_combo, self.flow_combo):
            combo.setEnabled(not connected)

    # ---- comandi --------------------------------------------------------

    def clear_screen(self) -> None:
        self.engine.feed(b"\x1b[2J\x1b[H")

    def toggle_hex(self) -> None:
        modes = {
            HexDumpMode.OFF: HexDumpMode.WIDE,
            HexDumpMode.WIDE: HexDumpMode.NARROW,
            HexDumpMode.NARROW: HexDumpMode.OFF,
        }
        self.engine.hex_mode = modes[self.engine.hex_mode]
        self._refresh_status()

    def toggle_resize_mode(self) -> None:
        self.term.resize_mode = (
            ResizeMode.FONT
            if self.term.resize_mode is ResizeMode.BUFFER
            else ResizeMode.BUFFER
        )
        self._refresh_status()

    def bump_font(self, delta: int) -> None:
        self.term.font_size = self.term.font_size + delta
        self._refresh_status()

    def _on_title(self, title: str) -> None:
        base = self._app_name
        self.setWindowTitle(f"{title} — {base}" if title else base)

    def _refresh_status(self) -> None:
        connected = bool(self.transport and self.transport.is_open)
        dot = "\u25cf"
        state = f"{dot} connesso" if connected else f"{dot} offline"
        self.statusBar().showMessage(
            f"{state}   |   {self.engine.cols}x{self.engine.rows}   |   "
            f"hex: {self.engine.hex_mode.name}   |   "
            f"font: {self.term.font_size}pt   |   "
            f"scrollback: {self.engine.history_len} righe"
        )

    def closeEvent(self, event) -> None:
        self._save_settings()
        self.monitor.stop()
        if self.transport:
            self.transport.close()
        super().closeEvent(event)
