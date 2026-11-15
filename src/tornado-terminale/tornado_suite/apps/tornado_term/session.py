"""Session: una porta seriale + un terminale + trasferimenti + pannello di configurazione.

Ogni sessione ha il proprio SerialLink, TerminalWidget, motori di trasferimento e pannello.
La sessione 1 usa le chiavi QSettings storiche (senza prefisso), le altre usano "tN/".
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from ...core.theme import P
from .links import LinkMux, TcpConfig
from .macros import MacroRunner
from .serial_core import (
    DATA_BITS, FLOW, PARITY, STANDARD_BAUDS, STOP_BITS, PortInfo, SerialConfig, SerialLink,
)
from .sessionlog import HexWindow, SessionLog
from .terminal_widget import TerminalWidget
from .transfer import AsciiSender, XYModem
from .transfer_ui import TransferDialog

PIN_ORDER = ["CTS", "DSR", "DCD", "RI", "RTS", "DTR"]
ENTER = {"CR": b"\r", "CR+LF": b"\r\n", "LF": b"\n"}
BACKSPACE = {"DEL (0x7F)": b"\x7f", "BS (0x08)": b"\x08"}
HOME_END = {"xterm (ESC[H / ESC[F)": False, "VT220 (ESC[1~ / ESC[4~)": True}
DEFAULT_COLS = 87   # 80 + 7: larghezza richiesta dalla schermata del firmware
DEFAULT_ROWS = 24


class Led(QLabel):
    def __init__(self, name: str):
        super().__init__(name)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(38)
        self.set_on(False)

    def set_on(self, on: bool):
        bg, fg = (P.ok, P.on_accent) if on else (P.raised, P.text_dim)
        self.setStyleSheet(f"background:{bg};color:{fg};border-radius:3px;padding:2px 4px;")


class Session(QObject):
    summary_changed = Signal()          # stato/config/contatori cambiati
    term_stats = Signal(float, float, int)
    message = Signal(str, int)          # testo, ms (barra di stato)
    focused = Signal()                  # il terminale ha preso il fuoco
    scan_requested = Signal()

    def __init__(self, index: int, settings: QSettings, port_busy: Callable[[str, "Session"], bool], parent=None,
                 prefix: str | None = None):
        super().__init__(parent)
        self.index = index
        self.s = settings
        self._pfx = prefix if prefix is not None else ("" if index == 1 else f"t{index}/")
        self._port_busy = port_busy
        self._ports: list[PortInfo] = []
        self._rx = self._tx = 0

        self.link = LinkMux(self)
        self.log = SessionLog(self)
        self.hexwin: HexWindow | None = None
        self.term = TerminalWidget(DEFAULT_COLS, DEFAULT_ROWS, 10000)
        self.ascii_tx = AsciiSender(self.link.write, self.link.bytes_to_write, self)
        self.xy = XYModem(self.link.write, self)
        self.macro_runner = MacroRunner(self.link.write, lambda: self.term.enter_seq, self)
        self.dialogs: list[TransferDialog] = []

        self.panel = self._build_panel()
        self.term.installEventFilter(self)
        self.term.viewport().installEventFilter(self)

        self.term.send.connect(self._on_term_send)
        self.term.grid_changed.connect(self._on_grid)
        self.term.stats.connect(self.term_stats)
        self.link.data_received.connect(self._on_rx_data)
        self.link.data_received.connect(self.log.rx)
        self.link.data_sent.connect(self.log.tx)
        self.link.data_received.connect(lambda b: self._hex_feed("RX", b))
        self.link.data_sent.connect(lambda b: self._hex_feed("TX", b))
        self.link.opened.connect(self._on_opened)
        self.link.closed.connect(self._on_closed)
        self.link.opened.connect(lambda p: self.log.note(f"connesso a {p}"))
        self.link.closed.connect(lambda r: self.log.note(f"disconnesso {r}".strip()))
        self.link.closed.connect(self.abort_transfers)
        self.link.error.connect(lambda m: self.message.emit(m, 8000))
        self.link.pins_changed.connect(self._on_pins)
        self.link.stats_changed.connect(self._on_stats)
        self.link.reconnect_pending.connect(
            lambda p: self.message.emit(f"T{self.index}: {p} scollegata, attendo la riconnessione...", 0))
        self.load_settings()
        self._sync_ui()

    # --- identita' ----------------------------------------------------------
    @property
    def title(self) -> str:
        if self.link.is_open:
            port = self.link.config.port
        elif self.cb_kind.currentData() == "tcp":
            port = self._current_tcp().port
        else:
            port = self.cb_port.currentData() or ""
        return f"Terminale {self.index}" + (f" - {port}" if port else "")

    def summary(self) -> str:
        if self.link.is_open:
            c = self.link.config
            fmt = "" if self.link.kind == "tcp" else f" {c.baud},{c.data_bits}{c.parity[0]}{c.stop_bits}"
            return f"T{self.index} {c.port}{fmt} | RX {self._rx} TX {self._tx}"
        if self.link.waiting_reconnect:
            return f"T{self.index} riconnessione..."
        return f"T{self.index} disconnesso"

    # --- pannello -----------------------------------------------------------
    def _build_panel(self) -> QWidget:
        self.cb_port = QComboBox()
        self.cb_port.setMinimumWidth(230)
        self.cb_baud = QComboBox()
        self.cb_baud.setEditable(True)
        self.cb_baud.setValidator(QIntValidator(50, 12_000_000, self))
        for b in STANDARD_BAUDS:
            self.cb_baud.addItem(str(b))
        self.cb_data = QComboBox(); self.cb_data.addItems(DATA_BITS)
        self.cb_par = QComboBox(); self.cb_par.addItems(PARITY)
        self.cb_stop = QComboBox(); self.cb_stop.addItems(STOP_BITS)
        self.cb_flow = QComboBox(); self.cb_flow.addItems(FLOW)
        self.btn_scan = QPushButton("Aggiorna porte")
        self.btn_conn = QPushButton("Connetti")
        self.btn_conn.setCheckable(True)

        self.cb_kind = QComboBox()
        self.cb_kind.addItem("Seriale", "serial")
        self.cb_kind.addItem("TCP (rete)", "tcp")
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("192.168.1.100 o nome host")
        self.sp_tcp = QSpinBox(); self.sp_tcp.setRange(1, 65535); self.sp_tcp.setValue(23)

        grp = QGroupBox("Connessione")
        form = QFormLayout(grp)
        self._form = form
        form.addRow("Tipo", self.cb_kind)
        form.addRow("Porta", self.cb_port)
        form.addRow("Baud", self.cb_baud)
        form.addRow("Data bits", self.cb_data)
        form.addRow("Parita'", self.cb_par)
        form.addRow("Stop bits", self.cb_stop)
        form.addRow("Flusso", self.cb_flow)
        form.addRow("Indirizzo", self.ed_host)
        form.addRow("Porta TCP", self.sp_tcp)
        form.addRow(self.btn_scan)
        form.addRow(self.btn_conn)
        self._serial_rows = (self.cb_port, self.cb_baud, self.cb_data, self.cb_par, self.cb_stop, self.cb_flow, self.btn_scan)
        self._tcp_rows = (self.ed_host, self.sp_tcp)

        self.chk_auto = QCheckBox("Riconnessione automatica")
        self.chk_rts = QCheckBox("RTS")
        self.chk_dtr = QCheckBox("DTR")
        self.btn_break = QPushButton("Invia BREAK")
        self.btn_flush = QPushButton("Svuota buffer")
        self.btn_reset = QPushButton("Azzera contatori")
        self.leds = {n: Led(n) for n in PIN_ORDER}

        grp2 = QGroupBox("Linee di controllo")
        v2 = QVBoxLayout(grp2)
        row = QHBoxLayout(); row.addWidget(self.chk_rts); row.addWidget(self.chk_dtr)
        v2.addLayout(row)
        r1 = QHBoxLayout(); r2 = QHBoxLayout()
        for n in ("CTS", "DSR", "DCD"): r1.addWidget(self.leds[n])
        for n in ("RI", "RTS", "DTR"): r2.addWidget(self.leds[n])
        v2.addLayout(r1); v2.addLayout(r2)
        v2.addWidget(self.btn_break)
        v2.addWidget(self.btn_flush)
        v2.addWidget(self.btn_reset)
        v2.addWidget(self.chk_auto)

        self.sp_cols = QSpinBox(); self.sp_cols.setRange(10, 400)
        self.sp_rows = QSpinBox(); self.sp_rows.setRange(2, 200)
        self.chk_fit = QCheckBox("Adatta alla finestra")
        self.sp_font = QSpinBox(); self.sp_font.setRange(6, 40); self.sp_font.setSuffix(" pt")
        self.cb_enter = QComboBox(); self.cb_enter.addItems(ENTER)
        self.cb_bs = QComboBox(); self.cb_bs.addItems(BACKSPACE)
        self.cb_he = QComboBox(); self.cb_he.addItems(HOME_END)
        self.chk_echo = QCheckBox("Eco locale")
        self.chk_lf = QCheckBox("LF = a capo (CR implicito)")
        self.btn_clear = QPushButton("Pulisci schermo")

        grp3 = QGroupBox("Terminale")
        f3 = QFormLayout(grp3)
        f3.addRow("Colonne", self.sp_cols)
        f3.addRow("Righe", self.sp_rows)
        f3.addRow(self.chk_fit)
        f3.addRow("Font", self.sp_font)
        f3.addRow("Invio (TX)", self.cb_enter)
        f3.addRow("Backspace", self.cb_bs)
        f3.addRow("Home/End", self.cb_he)
        f3.addRow(self.chk_echo)
        f3.addRow(self.chk_lf)
        f3.addRow(self.btn_clear)

        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.addWidget(grp); lay.addWidget(grp2); lay.addWidget(grp3); lay.addStretch(1)

        self.cb_kind.currentIndexChanged.connect(lambda _i: (self._sync_ui(), self.summary_changed.emit()))
        self.ed_host.returnPressed.connect(self._toggle_connect)
        self.btn_scan.clicked.connect(lambda: self.scan_requested.emit())
        self.btn_conn.clicked.connect(self._toggle_connect)
        for cb in (self.cb_data, self.cb_par, self.cb_stop, self.cb_flow):
            cb.currentIndexChanged.connect(self._params_changed)
        self.cb_baud.lineEdit().editingFinished.connect(self._params_changed)
        self.cb_baud.activated.connect(lambda _i: self._params_changed())
        self.chk_auto.toggled.connect(lambda on: setattr(self.link, "auto_reconnect", on))
        self.chk_rts.toggled.connect(self.link.set_rts)
        self.chk_dtr.toggled.connect(self.link.set_dtr)
        self.btn_break.clicked.connect(lambda: self.link.send_break(250))
        self.btn_flush.clicked.connect(self.link.flush_buffers)
        self.btn_reset.clicked.connect(self.link.reset_stats)
        for sp in (self.sp_cols, self.sp_rows):
            sp.setKeyboardTracking(False)
            sp.valueChanged.connect(self._grid_edited)
        self.chk_fit.toggled.connect(self._fit_toggled)
        self.sp_font.valueChanged.connect(self.term.set_font_size)
        self.cb_enter.currentTextChanged.connect(lambda t: setattr(self.term, "enter_seq", ENTER[t]))
        self.cb_bs.currentTextChanged.connect(lambda t: setattr(self.term, "backspace_seq", BACKSPACE[t]))
        self.cb_he.currentTextChanged.connect(lambda t: setattr(self.term, "home_end_vt220", HOME_END[t]))
        self.chk_echo.toggled.connect(lambda on: setattr(self.term, "local_echo", on))
        self.chk_lf.toggled.connect(self.term.set_lf_newline)
        self.btn_clear.clicked.connect(self.term.clear)
        self.btn_clear.clicked.connect(self.term.setFocus)
        return panel

    # --- terminale ----------------------------------------------------------
    def _grid_edited(self):
        if not self.chk_fit.isChecked():
            self.term.set_grid(self.sp_cols.value(), self.sp_rows.value())

    def _fit_toggled(self, on: bool):
        self.sp_cols.setEnabled(not on)
        self.sp_rows.setEnabled(not on)
        self.term.set_fit(on)
        if not on:
            self.term.set_grid(self.sp_cols.value(), self.sp_rows.value())

    def _on_grid(self, cols: int, rows: int):
        self.sp_cols.blockSignals(True); self.sp_rows.blockSignals(True)
        self.sp_cols.setValue(cols); self.sp_rows.setValue(rows)
        self.sp_cols.blockSignals(False); self.sp_rows.blockSignals(False)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.FocusIn or ev.type() == QEvent.Type.MouseButtonPress:
            self.focused.emit()
        return False

    # --- seriale ------------------------------------------------------------
    def _current_cfg(self) -> SerialConfig:
        try:
            baud = int(self.cb_baud.currentText())
        except ValueError:
            baud = 115200
        return SerialConfig(
            port=self.cb_port.currentData() or "", baud=baud,
            data_bits=self.cb_data.currentText(), parity=self.cb_par.currentText(),
            stop_bits=self.cb_stop.currentText(), flow=self.cb_flow.currentText(),
        )

    def _current_tcp(self) -> TcpConfig:
        return TcpConfig(self.ed_host.text().strip(), self.sp_tcp.value())

    def _params_changed(self, *_):
        if self.link.is_open and self.link.kind == "serial":
            self.link.reconfigure(self._current_cfg())
            self.summary_changed.emit()

    def _toggle_connect(self):
        if self.link.is_open or self.link.waiting_reconnect:
            self.link.close()
            self._sync_ui()
            return
        if self.cb_kind.currentData() == "tcp":
            tcfg = self._current_tcp()
            if not tcfg.host:
                self.message.emit(f"T{self.index}: indirizzo TCP mancante", 5000)
                self.btn_conn.setChecked(False)
                return
            if self._port_busy("tcp:" + tcfg.port, self):
                self.message.emit(f"{tcfg.port} e' gia' collegata nell'altro terminale", 6000)
                self.btn_conn.setChecked(False)
                return
            if self.link.open(tcfg):
                self.term.setFocus()
            else:
                self.btn_conn.setChecked(False)
            self._sync_ui()
            return
        cfg = self._current_cfg()
        if not cfg.port:
            self.message.emit(f"T{self.index}: nessuna porta selezionata", 5000)
            self.btn_conn.setChecked(False)
            return
        if self._port_busy(cfg.port, self):
            self.message.emit(f"{cfg.port} e' gia' aperta nell'altro terminale", 6000)
            self.btn_conn.setChecked(False)
            return
        if self.link.open(cfg):
            self.term.setFocus()
        else:
            self.btn_conn.setChecked(False)

    def set_ports(self, ports: list[PortInfo]):
        self._ports = ports
        keep = self.cb_port.currentData() or self.s.value(self._pfx + "port", "", str)
        self.cb_port.blockSignals(True)
        self.cb_port.clear()
        for p in ports:
            self.cb_port.addItem(p.label, p.name)
            tip = f"{p.manufacturer}".strip()
            if p.vid is not None:
                tip += f"  VID:PID {p.vid:04X}:{p.pid or 0:04X}"
            if p.serial:
                tip += f"  S/N {p.serial}"
            self.cb_port.setItemData(self.cb_port.count() - 1, tip.strip(), Qt.ItemDataRole.ToolTipRole)
        idx = self.cb_port.findData(keep)
        if idx >= 0:
            self.cb_port.setCurrentIndex(idx)
        self.cb_port.blockSignals(False)
        self._sync_ui()

    def port_in_use(self) -> str:
        if not (self.link.is_open or self.link.waiting_reconnect):
            return ""
        c = self.link.config
        return ("tcp:" + c.port) if self.link.kind == "tcp" else c.port

    def _on_opened(self, port: str):
        self.message.emit(f"T{self.index}: {port} aperta", 3000)
        self._sync_ui()

    def _on_closed(self, reason: str):
        if reason:
            self.message.emit(f"T{self.index}: porta chiusa ({reason})", 8000)
        for led in self.leds.values():
            led.set_on(False)
        self._sync_ui()

    def _on_pins(self, pins: dict):
        for n, led in self.leds.items():
            led.set_on(pins.get(n, False))

    def _on_stats(self, rx: int, tx: int):
        self._rx, self._tx = rx, tx
        self.summary_changed.emit()

    def _sync_ui(self):
        up = self.link.is_open
        waiting = self.link.waiting_reconnect
        tcp = self.cb_kind.currentData() == "tcp"
        for w in self._serial_rows:
            self._form.setRowVisible(w, not tcp)
        for w in self._tcp_rows:
            self._form.setRowVisible(w, tcp)
        self.cb_kind.setEnabled(not up and not waiting)
        self.ed_host.setEnabled(not up and not waiting)
        self.sp_tcp.setEnabled(not up and not waiting)
        self.cb_port.setEnabled(not up and not waiting)
        self.btn_scan.setEnabled(not up)
        self.btn_conn.setChecked(up or waiting)
        self.btn_conn.setText("Disconnetti" if up or waiting else "Connetti")
        self.btn_break.setEnabled(up and self.link.kind == "serial")
        self.btn_flush.setEnabled(up)
        self.summary_changed.emit()

    # --- dati, trasferimenti, macro ----------------------------------------
    def _on_rx_data(self, data: bytes):
        if not self.xy.feed(data):
            self.term.feed(data)

    def _on_term_send(self, data: bytes):
        if not self.xy.active:      # durante XMODEM/YMODEM la tastiera non deve inquinare il flusso
            self.link.write(data)

    def abort_transfers(self, *_):
        self.ascii_tx.cancel()
        self.xy.abort()
        self.macro_runner.cancel()

    def open_transfer(self, kind: str, parent_window):
        if not self.link.is_open:
            self.message.emit(f"T{self.index}: connetti prima la porta seriale", 5000)
            return
        dlg = TransferDialog(kind, self.ascii_tx, self.xy, self.s, parent_window)
        dlg.setWindowTitle(f"{dlg.windowTitle()} - T{self.index}")
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.dialogs.append(dlg)
        dlg.destroyed.connect(lambda _=None, d=dlg: self.dialogs.remove(d) if d in self.dialogs else None)
        dlg.show()

    def run_macro(self, text: str):
        if not self.link.is_open:
            self.message.emit(f"T{self.index}: connetti prima la porta seriale", 5000)
        elif self.xy.active:
            self.message.emit(f"T{self.index}: trasferimento in corso, macro non eseguita", 5000)
        elif not self.macro_runner.run(text):
            self.message.emit(f"T{self.index}: macro vuota o gia' in esecuzione", 3000)

    # --- persistenza --------------------------------------------------------
    def load_settings(self):
        s, p = self.s, self._pfx
        self.cb_baud.setCurrentText(str(s.value(p + "baud", 115200, int)))
        self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(s.value(p + "kind", "serial", str))))
        self.ed_host.setText(s.value(p + "tcp_host", "", str))
        self.sp_tcp.setValue(s.value(p + "tcp_port", 23, int))
        for cb, key, default in (
            (self.cb_data, "data_bits", "8"), (self.cb_par, "parity", "None"),
            (self.cb_stop, "stop_bits", "1"), (self.cb_flow, "flow", "None"),
            (self.cb_enter, "enter", "CR"), (self.cb_bs, "backspace", "DEL (0x7F)"),
            (self.cb_he, "home_end", "xterm (ESC[H / ESC[F)"),
        ):
            cb.setCurrentText(s.value(p + key, default, str))
        self.chk_auto.setChecked(s.value(p + "auto_reconnect", True, bool))
        self.link.auto_reconnect = self.chk_auto.isChecked()
        self.chk_rts.setChecked(s.value(p + "rts", True, bool))
        self.chk_dtr.setChecked(s.value(p + "dtr", True, bool))
        self.sp_font.setValue(s.value(p + "font_pt", 10, int))
        self.chk_lf.setChecked(s.value(p + "lf_newline", True, bool))
        self.chk_echo.setChecked(s.value(p + "local_echo", False, bool))
        self.term.local_echo = self.chk_echo.isChecked()
        self.term.enter_seq = ENTER[self.cb_enter.currentText()]
        self.term.backspace_seq = BACKSPACE[self.cb_bs.currentText()]
        self.term.home_end_vt220 = HOME_END[self.cb_he.currentText()]
        self.term.set_lf_newline(self.chk_lf.isChecked())
        self.sp_cols.setValue(s.value(p + "cols", DEFAULT_COLS, int))
        self.sp_rows.setValue(s.value(p + "rows", DEFAULT_ROWS, int))
        self.term.set_grid(self.sp_cols.value(), self.sp_rows.value())
        self.chk_fit.setChecked(s.value(p + "fit", False, bool))

    def save_settings(self):
        c, s, p = self._current_cfg(), self.s, self._pfx
        for k, v in (
            ("kind", self.cb_kind.currentData()), ("tcp_host", self.ed_host.text().strip()),
            ("tcp_port", self.sp_tcp.value()),
            ("port", c.port), ("baud", c.baud), ("data_bits", c.data_bits), ("parity", c.parity),
            ("stop_bits", c.stop_bits), ("flow", c.flow), ("enter", self.cb_enter.currentText()),
            ("backspace", self.cb_bs.currentText()), ("home_end", self.cb_he.currentText()),
            ("auto_reconnect", self.chk_auto.isChecked()), ("rts", self.chk_rts.isChecked()),
            ("dtr", self.chk_dtr.isChecked()), ("font_pt", self.sp_font.value()),
            ("lf_newline", self.chk_lf.isChecked()), ("local_echo", self.chk_echo.isChecked()),
            ("fit", self.chk_fit.isChecked()), ("cols", self.sp_cols.value()), ("rows", self.sp_rows.value()),
        ):
            s.setValue(p + k, v)

    def shutdown(self):
        self.abort_transfers()
        self.link.close()
        self.log.stop()
        if self.hexwin:
            self.hexwin.close()

    # --- API programmatica (usata anche da TerminalPanel) -------------------------
    def open_serial(self, port: str, baud: int = 115200, data_bits: str = "8", parity: str = "None",
                    stop_bits: str = "1", flow: str = "None") -> bool:
        if self.link.is_open or self.link.waiting_reconnect:
            self.link.close()
        self.cb_kind.setCurrentIndex(self.cb_kind.findData("serial"))
        if self.cb_port.findData(port) < 0:
            self.cb_port.addItem(port, port)
        self.cb_port.setCurrentIndex(self.cb_port.findData(port))
        self.cb_baud.setCurrentText(str(baud))
        for cb, v in ((self.cb_data, data_bits), (self.cb_par, parity), (self.cb_stop, stop_bits), (self.cb_flow, flow)):
            cb.setCurrentText(v)
        self._toggle_connect()
        return self.link.is_open

    def open_tcp(self, host: str, port: int) -> bool:
        if self.link.is_open or self.link.waiting_reconnect:
            self.link.close()
        self.cb_kind.setCurrentIndex(self.cb_kind.findData("tcp"))
        self.ed_host.setText(host)
        self.sp_tcp.setValue(int(port))
        self._toggle_connect()
        return self.link.waiting_reconnect or self.link.is_open

    def disconnect_link(self):
        self.link.close()
        self._sync_ui()

    def send_bytes(self, data: bytes) -> int:
        return self.link.write(data)

    def send_text(self, text: str, newline: bool = True) -> int:
        return self.link.write(text.encode("utf-8", "replace") + (self.term.enter_seq if newline else b""))

    # --- log e vista esadecimale ------------------------------------------------------
    def start_log(self, parent_window=None, hex_mode: bool = False, path: str = "") -> bool:
        if not path:
            from datetime import datetime
            name = f"T{self.index}_{datetime.now():%Y%m%d_%H%M%S}.{'hex.log' if hex_mode else 'log'}"
            path, _ = QFileDialog.getSaveFileName(parent_window, "Avvia log", name, "Log (*.log *.txt)")
            if not path:
                return False
        try:
            self.log.start(path, hex_mode)
        except OSError as e:
            self.message.emit(f"T{self.index}: log non avviabile: {e}", 8000)
            return False
        self.message.emit(f"T{self.index}: log su {path}", 5000)
        return True

    def stop_log(self):
        if self.log.active:
            self.log.stop()
            self.message.emit(f"T{self.index}: log chiuso", 3000)

    def show_hex(self, parent_window=None):
        if self.hexwin is None:
            self.hexwin = HexWindow(self.title, parent_window)
            self.hexwin.destroyed.connect(lambda _=None: setattr(self, "hexwin", None))
        self.hexwin.show()
        self.hexwin.raise_()

    def _hex_feed(self, d: str, data: bytes):
        if self.hexwin is not None:
            self.hexwin.feed(d, data)
