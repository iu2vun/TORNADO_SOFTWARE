"""Pannelli laterali (acquisizione, decoder) e tabelle dei risultati."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from logic_core import (N_CHANNELS, AcquisitionConfig, ChannelStats, ConfigError, DecoderConfig, Glitch,
                        I2cCfg, Lane, LogOptions, Message, SerialCfg, SerialResult, SpiCfg, Trigger, UartCfg)
from logic_core.serialbus import FORMATS, STANDARD_BAUDS
from tornado_ui import Card, P, dim, h2, icon

from .fmt import fmt_percent, fmt_si


def _card(title: str) -> tuple[Card, QVBoxLayout]:
    c = Card()
    lay = QVBoxLayout(c)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    lay.addWidget(h2(title))
    return c, lay


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setHorizontalSpacing(10)
    f.setVerticalSpacing(8)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    return f


# ================================ ACQUISIZIONE ================================
TRIGGER_MODES = (("Fronte di salita", "rising"), ("Fronte di discesa", "falling"),
                 ("Entrambi i fronti", "either"), ("Livello alto", "high"), ("Livello basso", "low"))
SAMPLE_PRESETS = (1_000, 10_000, 100_000, 1_000_000, 10_000_000, 24_000_000)


class AcquirePanel(QWidget):
    channelsChanged = Signal()
    acquireRequested = Signal()
    stopRequested = Signal()

    def __init__(self, device_kinds: tuple[str, ...], samplerates: tuple[float, ...], parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        card, lay = _card("Dispositivo")
        self.device = QComboBox()
        for k in device_kinds:
            self.device.addItem(k)
        lay.addWidget(self.device)
        v.addWidget(card)

        card, lay = _card("Acquisizione")
        f = _form()
        self.rate = QComboBox()
        for r in samplerates:
            self.rate.addItem(fmt_si(r, "S/s"), r)
        self.rate.setCurrentIndex(max(0, self.rate.findData(24e6)))
        self.samples = QComboBox()
        for n in SAMPLE_PRESETS:
            self.samples.addItem(fmt_si(n, "campioni"), n)
        self.samples.setCurrentIndex(self.samples.findData(1_000_000))
        self.lbl_dur = dim("")
        f.addRow("Frequenza", self.rate)
        f.addRow("Campioni", self.samples)
        f.addRow("Durata", self.lbl_dur)
        self.chk_cont = QCheckBox("Continua (ripeti fino a Stop)")
        self.chk_cont.setToolTip("Una cattura dopo l'altra: la schermata si aggiorna a ogni giro.\n"
                                 "Con l'hardware tra due catture passa circa 1 s (riavvio di sigrok-cli).")
        self.spin_pause = QDoubleSpinBox()
        self.spin_pause.setRange(0.0, 3600.0)
        self.spin_pause.setDecimals(1)
        self.spin_pause.setSingleStep(0.5)
        self.spin_pause.setSuffix(" s")
        self.spin_pause.setEnabled(False)
        f.addRow(self.chk_cont)
        f.addRow("Pausa", self.spin_pause)
        lay.addLayout(f)
        v.addWidget(card)

        card, lay = _card("Trigger")
        f = _form()
        self.trig_ch = QComboBox()
        self.trig_mode = QComboBox()
        for label, mode in TRIGGER_MODES:
            self.trig_mode.addItem(label, mode)
        self.trig_pre = QSpinBox()
        self.trig_pre.setRange(0, 95)
        self.trig_pre.setValue(10)
        self.trig_pre.setSuffix(" %")
        f.addRow("Canale", self.trig_ch)
        f.addRow("Condizione", self.trig_mode)
        f.addRow("Pre-trigger", self.trig_pre)
        self.chk_hwtrig = QCheckBox("Trigger hardware (sigrok)")
        self.chk_hwtrig.setToolTip("Il trigger lo gestisce sigrok-cli: aspetta l'evento senza finestra fissa e usa solo "
                                   "i campioni richiesti (non il doppio).\nSolo con «Hardware (sigrok)»; se sigrok-cli "
                                   "lo rifiuta si usa il trigger software.")
        f.addRow(self.chk_hwtrig)
        lay.addLayout(f)
        v.addWidget(card)

        card, lay = _card("Canali")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._en: list[QCheckBox] = []
        self._name: list[QLineEdit] = []
        for ch in range(N_CHANNELS):
            cb = QCheckBox()
            cb.setChecked(True)
            le = QLineEdit(f"CH{ch + 1}")
            le.setMaxLength(12)
            self._en.append(cb)
            self._name.append(le)
            col = (ch // 4) * 2
            grid.addWidget(cb, ch % 4, col)
            grid.addWidget(le, ch % 4, col + 1)
            cb.toggled.connect(self._on_channels)
            le.editingFinished.connect(self._on_channels)
        lay.addLayout(grid)
        v.addWidget(card)

        card, lay = _card("Log")
        self.chk_log = QCheckBox("Registra su file")
        self.chk_log.setToolTip("Crea una cartella Log_data_ora a ogni avvio (anche per una sola acquisizione).")
        self.chk_log_dec = QCheckBox("Dati decodificati (CSV)")
        self.chk_log_raw = QCheckBox("Catture grezze (.npz)")
        self.chk_log_ev = QCheckBox("Eventi (testo)")
        for c in (self.chk_log_dec, self.chk_log_raw, self.chk_log_ev):
            c.setChecked(True)
        self.spin_raw = QSpinBox()
        self.spin_raw.setRange(0, 1_000_000)
        self.spin_raw.setValue(200)
        self.spin_raw.setSpecialValueText("tutte")
        self.spin_raw.setToolTip("Quante catture grezze conservare: le più vecchie vengono cancellate. 0 = tutte.")
        self.log_dir = QLineEdit(str(Path.home() / "Documents" / "Tornado Logic" / "Log"))
        self.btn_log_dir = QPushButton("…")
        self.btn_log_dir.setFixedWidth(34)
        self.btn_log_dir.clicked.connect(self._pick_log_dir)
        lay.addWidget(self.chk_log)
        lay.addWidget(self.chk_log_dec)
        lay.addWidget(self.chk_log_raw)
        lay.addWidget(self.chk_log_ev)
        f = _form()
        f.addRow("Conserva", self.spin_raw)
        lay.addLayout(f)
        r = QHBoxLayout()
        r.addWidget(self.log_dir, 1)
        r.addWidget(self.btn_log_dir)
        lay.addLayout(r)
        v.addWidget(card)
        self._log_widgets = (self.chk_log_dec, self.chk_log_raw, self.chk_log_ev, self.spin_raw, self.log_dir,
                             self.btn_log_dir)
        self.chk_log.toggled.connect(self._update_log_enabled)
        self.chk_cont.toggled.connect(self.spin_pause.setEnabled)
        self._update_log_enabled()

        row = QHBoxLayout()
        self.btn_acquire = QPushButton("  Acquisisci")
        self.btn_acquire.setProperty("primary", True)
        self.btn_acquire.setIcon(icon("play", P.on_accent, 18))
        self.btn_stop = QPushButton("  Stop")
        self.btn_stop.setIcon(icon("close", size=18))
        self.btn_stop.setEnabled(False)
        row.addWidget(self.btn_acquire, 2)
        row.addWidget(self.btn_stop, 1)
        v.addLayout(row)
        v.addStretch(1)

        self.btn_acquire.clicked.connect(self.acquireRequested)
        self.btn_stop.clicked.connect(self.stopRequested)
        self.rate.currentIndexChanged.connect(self._update_duration)
        self.samples.currentIndexChanged.connect(self._update_duration)
        self.device.currentTextChanged.connect(lambda _t: self._update_hwtrig())
        self._update_hwtrig()
        self._fill_trigger_channels()
        self._update_duration()

    # -------------------------------------------------------------- stato
    def _fill_trigger_channels(self) -> None:
        cur = self.trig_ch.currentData() if self.trig_ch.count() else None
        self.trig_ch.blockSignals(True)
        self.trig_ch.clear()
        self.trig_ch.addItem("Nessuno", None)
        for ch, name in enumerate(self.names()):
            self.trig_ch.addItem(name, ch)
        self.trig_ch.setCurrentIndex(max(0, self.trig_ch.findData(cur)))
        self.trig_ch.blockSignals(False)

    def _update_hwtrig(self) -> None:
        hw = self.device.currentText().startswith("Hardware")
        self.chk_hwtrig.setEnabled(hw and not self._busy)
        if not hw:
            self.chk_hwtrig.setChecked(False)

    def _update_duration(self) -> None:
        self.lbl_dur.setText(fmt_si(self.samples.currentData() / self.rate.currentData(), "s"))

    def _on_channels(self) -> None:
        self._fill_trigger_channels()
        self.channelsChanged.emit()

    def device_kind(self) -> str:
        return self.device.currentText()

    def names(self) -> list[str]:
        return [(le.text().strip() or f"CH{i + 1}") for i, le in enumerate(self._name)]

    def set_names(self, names: list[str]) -> None:
        for le, n in zip(self._name, names):
            le.setText(n)
        self._fill_trigger_channels()
        self.channelsChanged.emit()

    def enabled_channels(self) -> list[int]:
        return [i for i, cb in enumerate(self._en) if cb.isChecked()]

    def config(self) -> AcquisitionConfig:
        ch = tuple(self.enabled_channels())
        if not ch:
            raise ConfigError("abilitare almeno un canale")
        tch = self.trig_ch.currentData()
        trig = None if tch is None else Trigger(int(tch), self.trig_mode.currentData(), self.trig_pre.value() / 100)
        return AcquisitionConfig(float(self.rate.currentData()), int(self.samples.currentData()), ch, trig,
                                 hw_trigger=trig is not None and self.chk_hwtrig.isChecked())

    def _update_log_enabled(self) -> None:
        for w in self._log_widgets:
            w.setEnabled(self.chk_log.isChecked() and not self._busy)

    def _pick_log_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Cartella dei log", self.log_dir.text())
        if d:
            self.log_dir.setText(d)

    def continuous(self) -> bool:
        return self.chk_cont.isChecked()

    def pause_s(self) -> float:
        return float(self.spin_pause.value())

    def log_options(self) -> LogOptions | None:
        """None se il log è spento; ConfigError se è acceso ma incompleto."""
        if not self.chk_log.isChecked():
            return None
        o = LogOptions(self.log_dir.text().strip(), self.chk_log_dec.isChecked(), self.chk_log_raw.isChecked(),
                       self.chk_log_ev.isChecked(), int(self.spin_raw.value()))
        if not o.folder:
            raise ConfigError("indicare la cartella dei log")
        if not o.any():
            raise ConfigError("selezionare almeno un tipo di log")
        return o

    _busy = False

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_acquire.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        for w in (self.device, self.rate, self.samples, self.trig_ch, self.trig_mode, self.trig_pre,
                  self.chk_cont, self.chk_log):
            w.setEnabled(not busy)
        self.spin_pause.setEnabled(not busy and self.chk_cont.isChecked())
        self._update_hwtrig()
        self._update_log_enabled()


# ================================== DECODER ===================================
class DecoderPanel(QScrollArea):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 8, 4, 0)
        v.setSpacing(10)
        self._chan_combos: list[tuple[QComboBox, bool]] = []

        # --- UART ---
        self.g_uart = self._group("UART", v)
        f = _form()
        self.u_ch = self._chan("Canale", f, 0)
        self.u_baud = QComboBox()
        self.u_baud.setEditable(True)
        for b in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 2000000):
            self.u_baud.addItem(str(b))
        self.u_baud.setCurrentText("115200")
        self.u_baud.lineEdit().setValidator(QDoubleValidator(1.0, 50e6, 0))
        self.u_bits = QSpinBox()
        self.u_bits.setRange(5, 9)
        self.u_bits.setValue(8)
        self.u_par = QComboBox()
        for label, val in (("Nessuna", "N"), ("Pari", "E"), ("Dispari", "O")):
            self.u_par.addItem(label, val)
        self.u_stop = QComboBox()
        self.u_stop.addItem("1", 1)
        self.u_stop.addItem("2", 2)
        self.u_lsb = QCheckBox("LSB per primo")
        self.u_lsb.setChecked(True)
        self.u_inv = QCheckBox("Linea invertita")
        f.addRow("Baud", self.u_baud)
        f.addRow("Bit dati", self.u_bits)
        f.addRow("Parità", self.u_par)
        f.addRow("Bit di stop", self.u_stop)
        f.addRow(self.u_lsb)
        f.addRow(self.u_inv)
        self.g_uart.setLayout(f)

        # --- SPI ---
        self.g_spi = self._group("SPI", v)
        f = _form()
        self.s_clk = self._chan("CLK", f, 1)
        self.s_mosi = self._chan("MOSI", f, 2, optional=True)
        self.s_miso = self._chan("MISO", f, 3, optional=True)
        self.s_cs = self._chan("CS", f, 4, optional=True)
        self.s_cpol = QComboBox()
        self.s_cpol.addItem("0 (clock a riposo basso)", 0)
        self.s_cpol.addItem("1 (clock a riposo alto)", 1)
        self.s_cpha = QComboBox()
        self.s_cpha.addItem("0 (campiona sul 1° fronte)", 0)
        self.s_cpha.addItem("1 (campiona sul 2° fronte)", 1)
        self.s_bits = QSpinBox()
        self.s_bits.setRange(1, 32)
        self.s_bits.setValue(8)
        self.s_msb = QCheckBox("MSB per primo")
        self.s_msb.setChecked(True)
        self.s_csl = QCheckBox("CS attivo basso")
        self.s_csl.setChecked(True)
        f.addRow("CPOL", self.s_cpol)
        f.addRow("CPHA", self.s_cpha)
        f.addRow("Bit per parola", self.s_bits)
        f.addRow(self.s_msb)
        f.addRow(self.s_csl)
        self.g_spi.setLayout(f)

        # --- I2C ---
        self.g_i2c = self._group("I²C", v)
        f = _form()
        self.i_scl = self._chan("SCL", f, 5)
        self.i_sda = self._chan("SDA", f, 6)
        self.g_i2c.setLayout(f)
        v.addStretch(1)

        self._wire()

    def _group(self, title: str, lay: QVBoxLayout) -> QGroupBox:
        g = QGroupBox(title)
        g.setCheckable(True)
        g.setChecked(True)
        lay.addWidget(g)
        return g

    def _chan(self, label: str, form: QFormLayout, default: int, optional: bool = False) -> QComboBox:
        cb = QComboBox()
        if optional:
            cb.addItem("—", None)
        for ch in range(N_CHANNELS):
            cb.addItem(f"CH{ch + 1}", ch)
        cb.setCurrentIndex(max(0, cb.findData(default)))
        form.addRow(label, cb)
        self._chan_combos.append((cb, optional))
        return cb

    def _wire(self) -> None:
        for g in (self.g_uart, self.g_spi, self.g_i2c):
            g.toggled.connect(self.changed)
        for cb, _ in self._chan_combos:
            cb.currentIndexChanged.connect(self.changed)
        for w in (self.u_par, self.u_stop, self.s_cpol, self.s_cpha):
            w.currentIndexChanged.connect(self.changed)
        for w in (self.u_bits, self.s_bits):
            w.valueChanged.connect(self.changed)
        for w in (self.u_lsb, self.u_inv, self.s_msb, self.s_csl):
            w.toggled.connect(self.changed)
        self.u_baud.currentTextChanged.connect(self.changed)

    def set_channel_names(self, names: list[str]) -> None:
        for cb, _ in self._chan_combos:
            cb.blockSignals(True)
            for i in range(cb.count()):
                d = cb.itemData(i)
                if d is not None:
                    cb.setItemText(i, names[d])
            cb.blockSignals(False)

    def _baud(self) -> float:
        try:
            v = float(self.u_baud.currentText().replace(",", "."))
        except ValueError:
            return 115200.0
        return v if v > 0 else 115200.0

    def config(self) -> DecoderConfig:
        return DecoderConfig(
            uart=UartCfg(self.g_uart.isChecked(), self.u_ch.currentData(), self._baud(), self.u_bits.value(),
                         self.u_par.currentData(), self.u_stop.currentData(), self.u_lsb.isChecked(),
                         self.u_inv.isChecked()),
            spi=SpiCfg(self.g_spi.isChecked(), self.s_clk.currentData(), self.s_mosi.currentData(),
                       self.s_miso.currentData(), self.s_cs.currentData(), self.s_cpol.currentData(),
                       self.s_cpha.currentData(), self.s_bits.value(), self.s_msb.isChecked(),
                       self.s_csl.isChecked()),
            i2c=I2cCfg(self.g_i2c.isChecked(), self.i_scl.currentData(), self.i_sda.currentData()),
        )


# ================================== SERIALE ===================================
SERIAL_MODES = (("RS-232 (linee TX e RX)", "rs232"), ("RS-485 da uscita RO", "rs485"),
                ("RS-485 da A e B", "rs485_ab"))


def _fmt_name(fmt: tuple[int, str, int]) -> str:
    return f"{fmt[0]}{fmt[1]}{fmt[2]}"


class SerialPanel(QScrollArea):
    """Analizzatore RS-232 / RS-485: canali, baud e formato (automatici o fissi), direzione DE."""
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 8, 4, 0)
        v.setSpacing(10)
        self._combos: list[QComboBox] = []

        # casella esplicita (un QGroupBox spuntabile disabilita i campi e il tema non ne mostra la spunta)
        self.g = QCheckBox("Abilita analizzatore seriale")
        self.g.setChecked(False)
        v.addWidget(self.g)
        grp = QGroupBox("Configurazione RS-232 / RS-485")
        v.addWidget(grp)
        f = _form()
        self.f = f
        self.mode = QComboBox()
        for label, val in SERIAL_MODES:
            self.mode.addItem(label, val)
        f.addRow("Modo", self.mode)
        self.tx = self._chan("TX", 0, True)
        self.rx = self._chan("RX", 1, True)
        self.ro = self._chan("RO", 2)
        self.a = self._chan("A", 4)
        self.b = self._chan("B", 5)
        self.swap = QCheckBox("Scambia A / B")
        f.addRow(self.swap)
        self.de = self._chan("DE", 3, True)
        self.de_high = QCheckBox("DE attivo alto")
        self.de_high.setChecked(True)
        f.addRow(self.de_high)

        self.baud = QComboBox()
        self.baud.setEditable(True)
        self.baud.addItem("Automatico")
        for b in STANDARD_BAUDS:
            self.baud.addItem(str(b))
        self.baud.lineEdit().setPlaceholderText("Automatico")
        self.fmt = QComboBox()
        self.fmt.addItem("Automatico", None)
        for fm in FORMATS:
            self.fmt.addItem(_fmt_name(fm), fm)
        self.invert = QCheckBox("Linea invertita")
        self.modbus = QCheckBox("Decodifica Modbus RTU")
        self.modbus.setToolTip("Interpreta ogni messaggio come frame Modbus RTU: CRC, funzione, registri, "
                               "abbinamento richiesta/risposta ed eccezioni")
        self.gap = QDoubleSpinBox()
        self.gap.setRange(0.5, 50.0)
        self.gap.setSingleStep(0.5)
        self.gap.setDecimals(1)
        self.gap.setValue(3.5)
        self.gap.setSuffix(" car.")
        f.addRow("Baud", self.baud)
        f.addRow("Formato", self.fmt)
        f.addRow(self.invert)
        f.addRow("Pausa messaggio", self.gap)
        f.addRow(self.modbus)
        grp.setLayout(f)

        self.lbl_detect = note()
        self.lbl_detect.setWordWrap(True)
        self.lbl_detect.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        v.addWidget(self.lbl_detect)
        hint = dim("RS-232: portare i livelli a TTL con un convertitore (MAX3232). RS-485: usare RO di un "
                   "transceiver, oppure A e B digitalizzati. Senza DE, richiesta e risposta ravvicinate "
                   "si uniscono in un solo messaggio.")
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        v.addWidget(hint)
        v.addStretch(1)
        self._wire()
        self._update_rows()

    def _chan(self, label: str, default: int, optional: bool = False) -> QComboBox:
        cb = QComboBox()
        if optional:
            cb.addItem("—", None)
        for ch in range(N_CHANNELS):
            cb.addItem(f"CH{ch + 1}", ch)
        cb.setCurrentIndex(max(0, cb.findData(default)))
        self.f.addRow(label, cb)
        self._combos.append(cb)
        return cb

    def _wire(self) -> None:
        self.g.toggled.connect(self.changed)
        self.mode.currentIndexChanged.connect(self._update_rows)
        self.mode.currentIndexChanged.connect(self.changed)
        for cb in self._combos:
            cb.currentIndexChanged.connect(self.changed)
        self.fmt.currentIndexChanged.connect(self.changed)
        self.baud.currentTextChanged.connect(self.changed)
        for w in (self.swap, self.de_high, self.invert, self.modbus):
            w.toggled.connect(self.changed)
        self.gap.valueChanged.connect(self.changed)

    def _update_rows(self) -> None:
        m = self.mode.currentData()
        for w, on in ((self.tx, m == "rs232"), (self.rx, m == "rs232"), (self.ro, m == "rs485"),
                      (self.a, m == "rs485_ab"), (self.b, m == "rs485_ab"), (self.swap, m == "rs485_ab"),
                      (self.de, m != "rs232"), (self.de_high, m != "rs232")):
            self.f.setRowVisible(w, on)

    def set_channel_names(self, names: list[str]) -> None:
        for cb in self._combos:
            cb.blockSignals(True)
            for i in range(cb.count()):
                d = cb.itemData(i)
                if d is not None:
                    cb.setItemText(i, names[d])
            cb.blockSignals(False)

    def _baud(self) -> float | None:
        txt = self.baud.currentText().strip().replace(",", ".")
        if not txt or txt.lower().startswith("auto"):
            return None
        try:
            v = float(txt)
        except ValueError:
            return None
        return v if v > 0 else None

    def config(self) -> SerialCfg:
        return SerialCfg(
            enabled=self.g.isChecked(), mode=self.mode.currentData(), ch_tx=self.tx.currentData(),
            ch_rx=self.rx.currentData(), ch_ro=self.ro.currentData(), ch_a=self.a.currentData(),
            ch_b=self.b.currentData(), swap_ab=self.swap.isChecked(), ch_de=self.de.currentData(),
            de_active_high=self.de_high.isChecked(), baud=self._baud(), fmt=self.fmt.currentData(),
            invert=self.invert.isChecked(), gap_chars=self.gap.value(), modbus=self.modbus.isChecked())

    def set_detected(self, res: SerialResult | None) -> None:
        if res is None:
            self.lbl_detect.setText("")
            self.lbl_detect.setVisible(False)
            return
        parts = [f"Baud {res.baud:g}" + (" (rilevato" + (f", misurato {res.detected_baud.baud:.0f}" if res.detected_baud
                                                           and res.detected_baud.nominal else "") + ")"
                                         if res.detected_baud else ""),
                 f"formato {_fmt_name(res.fmt)}" + (" (rilevato)" if res.detected_format else "")]
        fe = res.detected_format
        if fe is not None and fe.alternatives:
            parts.append("indistinguibile da " + ", ".join(_fmt_name(a) for a in fe.alternatives))
        elif fe is not None and fe.error_rate > 0:
            parts.append(f"{fe.error_rate * 100:.0f} % di frame con errore")
        self.lbl_detect.setText(" · ".join(parts))
        self.lbl_detect.setVisible(True)


def serial_summary(res: SerialResult) -> str:
    st = res.stats
    lines = [f"{st.n_messages} messaggi · {st.n_bytes} byte (TX {st.tx_bytes}, RX {st.rx_bytes}) · "
             f"{st.n_errors} con errore"]
    if st.bit_timing is not None:
        b = st.bit_timing
        lines.append(f"Baud misurato {b.baud_actual:.0f} ({b.error_pct:+.2f} % dal nominale {b.baud_nominal:g}) · "
                     f"jitter di bit {fmt_si(b.jitter_rms, 's')} rms, {fmt_si(b.jitter_pp, 's')} p-p")
    if st.turnaround_min is not None:
        lines.append(f"Turnaround min/mediano/max: {fmt_si(st.turnaround_min, 's')} / "
                     f"{fmt_si(st.turnaround_median, 's')} / {fmt_si(st.turnaround_max, 's')}")
    if st.de_lead_min is not None:
        lines.append(f"DE anticipo {fmt_si(st.de_lead_min, 's')}…{fmt_si(st.de_lead_max, 's')} · "
                     f"ritardo {fmt_si(st.de_lag_min, 's')}…{fmt_si(st.de_lag_max, 's')} · "
                     f"{st.de_faults} messaggi con DE fuori tempo")
    if st.undriven_events:
        lines.append(f"{st.undriven_events} finestre di bus non pilotato (A = B)")
    mb = getattr(res, "modbus", None)
    if mb is not None:
        m = mb.stats
        lines.append(f"Modbus RTU: {m.n_frames} frame ({m.n_requests} richieste, {m.n_responses} risposte, "
                     f"{m.n_exceptions} eccezioni) · {m.n_crc_errors} con CRC errato · {m.n_malformed} malformati"
                     + (f" · slave {', '.join(map(str, m.slaves))}" if m.slaves else ""))
        if m.resp_median is not None:
            lines.append(f"Tempo di risposta min/mediano/max: {fmt_si(m.resp_min, 's')} / "
                         f"{fmt_si(m.resp_median, 's')} / {fmt_si(m.resp_max, 's')} su {m.n_pairs} transazioni")
    lines += res.notes
    return "\n".join(lines)


class MessageTable(QTableWidget):
    """Messaggi del bus seriale; doppio clic = vai al messaggio."""

    MAX_ROWS = 5000
    activatedRange = Signal(int, int)
    HEADERS = ["Tempo", "Dir.", "Byte", "Pausa", "Turnaround", "DE anticipo", "DE ritardo", "Modbus", "Dati"]

    def __init__(self) -> None:
        super().__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((110, 50, 50, 90, 100, 100, 100)):
            self.setColumnWidth(i, w)
        self.setColumnWidth(7, 420)
        self.setColumnHidden(7, True)
        self._ranges: list[tuple[int, int]] = []
        self.cellDoubleClicked.connect(self._on_dbl)

    def set_data(self, msgs: list[Message], fs: float, modbus=None) -> bool:
        self.setColumnHidden(7, modbus is None)
        mframes = modbus.frames if modbus is not None else []
        truncated = len(msgs) > self.MAX_ROWS
        msgs = msgs[:self.MAX_ROWS]
        self.setUpdatesEnabled(False)
        self.clearContents()
        self.setRowCount(len(msgs))
        self._ranges = []
        for i, m in enumerate(msgs):
            mf = mframes[i] if i < len(mframes) else None
            bad = m.errors > 0 or m.de_fault or (mf is not None and not mf.ok)
            col = P.danger if bad else None
            cells = [fmt_si(m.start / fs, "s"), m.direction, str(len(m.data)),
                     fmt_si(m.gap_before, "s") if m.gap_before is not None else "—",
                     fmt_si(m.turnaround, "s") if m.turnaround is not None else "—",
                     fmt_si(m.de_lead, "s") if m.de_lead is not None else "—",
                     fmt_si(m.de_lag, "s") if m.de_lag is not None else "—",
                     mf.text if mf is not None else "",
                     m.data[:48].hex(" ").upper() + (" …" if len(m.data) > 48 else "")]
            for c, t in enumerate(cells):
                self.setItem(i, c, _item(t, right=0 < c < 7 and c != 1, color=col))
            self._ranges.append((m.start, m.end))
        self.setUpdatesEnabled(True)
        return truncated

    def _on_dbl(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._ranges):
            self.activatedRange.emit(*self._ranges[row])


# ================================== TABELLE ===================================
def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return t


def _item(text: str, right: bool = False, color: str | None = None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if color:
        it.setForeground(QColor(color))
    return it


class DecodeTable(QTableWidget):
    """Elenco cronologico delle annotazioni di tutti i decoder; doppio clic = vai al riquadro."""

    MAX_ROWS = 5000
    activatedRange = Signal(int, int)  # indici di campione (start, end)

    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Tempo", "Decoder", "Dato", "Dettaglio"])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnWidth(0, 110)
        self.setColumnWidth(1, 100)
        self.setColumnWidth(2, 120)
        self._ranges: list[tuple[int, int]] = []
        self.cellDoubleClicked.connect(self._on_dbl)

    def set_data(self, lanes: list[Lane], fs: float) -> bool:
        """Ritorna True se l'elenco è stato troncato a MAX_ROWS."""
        rows = [(a.start, lane.name, a) for lane in lanes for a in lane.annotations]
        rows.sort(key=lambda r: r[0])
        truncated = len(rows) > self.MAX_ROWS
        rows = rows[:self.MAX_ROWS]
        self.setUpdatesEnabled(False)
        self.clearContents()
        self.setRowCount(len(rows))
        self._ranges = []
        for i, (s, name, a) in enumerate(rows):
            col = P.danger if a.error else None
            self.setItem(i, 0, _item(fmt_si(s / fs, "s"), True))
            self.setItem(i, 1, _item(name, color=col))
            self.setItem(i, 2, _item(a.text, color=col))
            self.setItem(i, 3, _item(a.detail, color=col))
            self._ranges.append((a.start, a.end))
        self.setUpdatesEnabled(True)
        return truncated

    def _on_dbl(self, row: int, _col: int) -> None:
        if 0 <= row < len(self._ranges):
            self.activatedRange.emit(*self._ranges[row])


class MeasureTable(QTableWidget):
    HEADERS = ["Canale", "Salite", "Discese", "Frequenza", "Periodo", "Duty", "Jitter p-p",
               "Alto min", "Alto max", "Basso min", "Basso max"]

    def __init__(self) -> None:
        super().__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def set_data(self, stats: list[ChannelStats], names: list[str]) -> None:
        self.setRowCount(len(stats))
        for r, s in enumerate(stats):
            vals = [names[s.channel], str(s.n_rising), str(s.n_falling), fmt_si(s.frequency, "Hz"),
                    fmt_si(s.period, "s"), fmt_percent(s.duty), fmt_si(s.period_pp, "s"),
                    fmt_si(s.high_min, "s"), fmt_si(s.high_max, "s"), fmt_si(s.low_min, "s"),
                    fmt_si(s.low_max, "s")]
            for c, t in enumerate(vals):
                self.setItem(r, c, _item(t, right=c > 0))


class GlitchTable(QTableWidget):
    activatedSample = Signal(int)

    def __init__(self) -> None:
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["Canale", "Tempo", "Larghezza", "Livello"])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._idx: list[int] = []
        self.cellDoubleClicked.connect(lambda r, _c: self.activatedSample.emit(self._idx[r]) if r < len(self._idx) else None)

    def set_data(self, glitches: list[tuple[int, Glitch]], names: list[str], fs: float) -> None:
        self.setRowCount(len(glitches))
        self._idx = [g.index for _, g in glitches]
        for r, (ch, g) in enumerate(glitches):
            self.setItem(r, 0, _item(names[ch]))
            self.setItem(r, 1, _item(fmt_si(g.index / fs, "s"), True))
            self.setItem(r, 2, _item(fmt_si(g.width, "s"), True))
            self.setItem(r, 3, _item("alto" if g.level else "basso"))


def note(text: str = "") -> QLabel:
    lbl = dim(text)
    lbl.setVisible(bool(text))
    return lbl
