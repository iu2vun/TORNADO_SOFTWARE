"""Pannelli laterali: canali, invio, riproduzione."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QSlider, QSpinBox, QStyle, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ..formats import parse_payload, render
from ..player import FINISHED, PAUSED, PLAYING, STOPPED


def fmt_bytes(n: int) -> str:
    if n < 10_000:
        return f"{n} B"
    if n < 10_000_000:
        return f"{n / 1024:.1f} kB"
    return f"{n / 1048576:.1f} MB"


def fmt_time(ns: int) -> str:
    s, ms = divmod(int(ns // 1_000_000), 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms:03d}"


# ====================================================================== canali
class ChannelsPanel(QWidget):
    add_clicked = Signal()
    edit_clicked = Signal(int)
    remove_clicked = Signal(int)
    bridge_clicked = Signal()
    intercept_clicked = Signal()

    HEAD = ["Nome", "Porta", "Config", "Raggr.", "Stato", "RX", "TX"]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.table = QTableWidget(0, len(self.HEAD))
        self.table.setHorizontalHeaderLabels(self.HEAD)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)
        self.table.doubleClicked.connect(lambda _: self._edit())
        self.btn_add = QPushButton("Aggiungi…")
        self.btn_edit = QPushButton("Modifica…")
        self.btn_del = QPushButton("Rimuovi")
        self.btn_bridge = QPushButton("Bridge…")
        self.btn_intercept = QPushButton("Intercetta porta…")
        self.btn_intercept.setToolTip("Man-in-the-middle fra un programma e un dispositivo con una coppia com0com")
        self.btn_intercept.clicked.connect(self.intercept_clicked)
        self.btn_add.clicked.connect(self.add_clicked)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_del.clicked.connect(self._remove)
        self.btn_bridge.clicked.connect(self.bridge_clicked)
        row = QHBoxLayout()
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_bridge):
            row.addWidget(b)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_intercept)
        self.lbl_bridges = QLabel("")
        self.lbl_bridges.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        lay.addLayout(row2)
        lay.addWidget(self.lbl_bridges)

    def _edit(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.edit_clicked.emit(r)

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.remove_clicked.emit(r)

    def set_editable(self, on: bool) -> None:
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_bridge, self.btn_intercept):
            b.setEnabled(on)

    def set_bridges_text(self, text: str) -> None:
        self.lbl_bridges.setText(f"Bridge: {text}" if text else "")

    def set_rows(self, rows: list[tuple]) -> None:
        sel = self.table.currentRow()
        if self.table.rowCount() != len(rows):
            self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                text = str(val)
                item = self.table.item(r, c)
                if item is None:
                    item = QTableWidgetItem(text)
                    if c >= 5:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(r, c, item)
                elif item.text() != text:
                    item.setText(text)
        if 0 <= sel < len(rows):
            self.table.selectRow(sel)


# ======================================================================== invio
class SendPanel(QWidget):
    send_requested = Signal(str, bytes)

    EOLS = [("Nessuno", b""), ("CR", b"\r"), ("LF", b"\n"), ("CRLF", b"\r\n")]
    FORMATS = [("ASCII (con \\r \\n \\xNN)", "escaped"), ("Hex", "hex"), ("Dec", "dec"), ("Bin", "bin")]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cmb_ch = QComboBox()
        self.cmb_fmt = QComboBox()
        for label, key in self.FORMATS:
            self.cmb_fmt.addItem(label, key)
        self.cmb_eol = QComboBox()
        for label, val in self.EOLS:
            self.cmb_eol.addItem(label, val)
        self.cmb_eol.setToolTip("Terminatore aggiunto in coda")
        self.cmb_text = QComboBox()
        self.cmb_text.setEditable(True)
        self.cmb_text.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.cmb_text.lineEdit().setPlaceholderText("testo da inviare, Invio per trasmettere")
        self.cmb_text.lineEdit().returnPressed.connect(self.send_now)
        self.cmb_text.lineEdit().textChanged.connect(self._preview)
        self.cmb_fmt.currentIndexChanged.connect(self._preview)
        self.cmb_eol.currentIndexChanged.connect(self._preview)
        self.btn_send = QPushButton("Invia")
        self.btn_send.clicked.connect(self.send_now)
        self.lbl_prev = QLabel("")
        self.lbl_prev.setWordWrap(True)
        self.chk_rep = QCheckBox("Ripeti ogni")
        self.spn_rep = QSpinBox()
        self.spn_rep.setRange(10, 600_000)
        self.spn_rep.setValue(1000)
        self.spn_rep.setSuffix(" ms")
        self.chk_rep.toggled.connect(self._repeat_toggled)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.send_now)

        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Canale"))
        r1.addWidget(self.cmb_ch, 1)
        r1.addWidget(self.cmb_fmt, 1)
        r1.addWidget(self.cmb_eol)
        r2 = QHBoxLayout()
        r2.addWidget(self.cmb_text, 1)
        r2.addWidget(self.btn_send)
        r3 = QHBoxLayout()
        r3.addWidget(self.chk_rep)
        r3.addWidget(self.spn_rep)
        r3.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(r1)
        lay.addLayout(r2)
        lay.addWidget(self.lbl_prev)
        lay.addLayout(r3)
        lay.addStretch(1)
        self.set_live(False)

    def set_channels(self, names: list[str]) -> None:
        cur = self.cmb_ch.currentText()
        self.cmb_ch.clear()
        self.cmb_ch.addItems(names)
        pos = self.cmb_ch.findText(cur)
        if pos >= 0:
            self.cmb_ch.setCurrentIndex(pos)

    def set_live(self, live: bool) -> None:
        for w in (self.cmb_ch, self.cmb_fmt, self.cmb_eol, self.cmb_text, self.btn_send, self.chk_rep, self.spn_rep):
            w.setEnabled(live)
        if not live:
            self.chk_rep.setChecked(False)

    def _data(self) -> bytes:
        return parse_payload(self.cmb_text.currentText(), self.cmb_fmt.currentData()) + self.cmb_eol.currentData()

    def _preview(self) -> None:
        try:
            data = self._data()
            self.lbl_prev.setStyleSheet("")
            self.lbl_prev.setText(f"→ {len(data)} byte: {render(data[:48], 'hex')}{' …' if len(data) > 48 else ''}")
        except ValueError as exc:
            self.lbl_prev.setStyleSheet("color: #FFFF00;")
            self.lbl_prev.setText(f"⚠ {exc}")

    def send_now(self) -> None:
        name = self.cmb_ch.currentText()
        if not name:
            return
        try:
            data = self._data()
        except ValueError:
            self.chk_rep.setChecked(False)
            return
        if not data:
            return
        self.send_requested.emit(name, data)
        text = self.cmb_text.currentText()
        if text and self.cmb_text.findText(text) < 0:
            self.cmb_text.insertItem(0, text)

    def _repeat_toggled(self, on: bool) -> None:
        if on:
            self._timer.start(self.spn_rep.value())
        else:
            self._timer.stop()


# =================================================================== riproduzione
class ReplayPanel(QWidget):
    open_clicked = Signal()
    close_clicked = Signal()
    play_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()
    seek_requested = Signal(int)
    speed_changed = Signal(float)
    loop_changed = Signal(bool)
    gap_changed = Signal(object)  # float secondi | None
    outputs_clicked = Signal()

    SPEEDS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 100.0, 1000.0]
    SLIDER_MAX = 10_000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        st = self.style()
        self.lbl_file = QLabel("Nessuna registrazione")
        self.lbl_file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_open = QPushButton("Apri…")
        self.btn_close = QPushButton("Chiudi riproduzione")
        self.btn_play = QToolButton()
        self.btn_play.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.btn_play.setToolTip("Riproduci")
        self.btn_pause = QToolButton()
        self.btn_pause.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.btn_pause.setToolTip("Pausa")
        self.btn_stop = QToolButton()
        self.btn_stop.setIcon(st.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.btn_stop.setToolTip("Stop (torna all'inizio)")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self.SLIDER_MAX)
        self.lbl_time = QLabel("00:00.000 / 00:00.000")
        self.cmb_speed = QComboBox()
        for s in self.SPEEDS:
            self.cmb_speed.addItem(f"{s:g}×", s)
        self.cmb_speed.setCurrentIndex(self.SPEEDS.index(1.0))
        self.chk_loop = QCheckBox("Loop")
        self.chk_gap = QCheckBox("Comprimi pause oltre")
        self.spn_gap = QDoubleSpinBox()
        self.spn_gap.setRange(0.01, 3600)
        self.spn_gap.setValue(1.0)
        self.spn_gap.setSuffix(" s")
        self.btn_out = QPushButton("Uscite…")
        self.btn_out.setToolTip("Rilancia i dati registrati su porte seriali reali")
        self._duration = 0
        self._dragging = False

        self.btn_open.clicked.connect(self.open_clicked)
        self.btn_close.clicked.connect(self.close_clicked)
        self.btn_play.clicked.connect(self.play_clicked)
        self.btn_pause.clicked.connect(self.pause_clicked)
        self.btn_stop.clicked.connect(self.stop_clicked)
        self.btn_out.clicked.connect(self.outputs_clicked)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderReleased.connect(self._released)
        self.cmb_speed.currentIndexChanged.connect(lambda _: self.speed_changed.emit(self.cmb_speed.currentData()))
        self.chk_loop.toggled.connect(self.loop_changed)
        self.chk_gap.toggled.connect(self._gap_changed)
        self.spn_gap.editingFinished.connect(self._gap_changed)

        r0 = QHBoxLayout()
        r0.addWidget(self.btn_open)
        r0.addWidget(self.lbl_file, 1)
        r0.addWidget(self.btn_close)
        r1 = QHBoxLayout()
        for b in (self.btn_play, self.btn_pause, self.btn_stop):
            r1.addWidget(b)
        r1.addWidget(self.slider, 1)
        r1.addWidget(self.lbl_time)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Velocità"))
        r2.addWidget(self.cmb_speed)
        r2.addWidget(self.chk_loop)
        r2.addWidget(self.chk_gap)
        r2.addWidget(self.spn_gap)
        r2.addStretch(1)
        r2.addWidget(self.btn_out)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(r0)
        lay.addLayout(r1)
        lay.addLayout(r2)
        self.set_loaded(False)

    # ---------------------------------------------------------------------- API
    def speed(self) -> float:
        return float(self.cmb_speed.currentData())

    def loop(self) -> bool:
        return self.chk_loop.isChecked()

    def gap(self) -> Optional[float]:
        return float(self.spn_gap.value()) if self.chk_gap.isChecked() else None

    def set_loaded(self, loaded: bool, name: str = "", duration_ns: int = 0) -> None:
        self._duration = duration_ns
        for w in (self.btn_play, self.btn_pause, self.btn_stop, self.slider, self.cmb_speed, self.chk_loop,
                  self.chk_gap, self.spn_gap, self.btn_out, self.btn_close):
            w.setEnabled(loaded)
        self.lbl_file.setText(name if loaded else "Nessuna registrazione")
        self.set_position(0)

    def set_state(self, state: str) -> None:
        self.btn_play.setEnabled(state in (STOPPED, PAUSED, FINISHED))
        self.btn_pause.setEnabled(state == PLAYING)
        self.btn_stop.setEnabled(state in (PLAYING, PAUSED, FINISHED))

    def set_position(self, ns: int) -> None:
        self.lbl_time.setText(f"{fmt_time(ns)} / {fmt_time(self._duration)}")
        if not self._dragging:
            self.slider.blockSignals(True)
            self.slider.setValue(0 if self._duration <= 0 else int(ns / self._duration * self.SLIDER_MAX))
            self.slider.blockSignals(False)

    def _released(self) -> None:
        self._dragging = False
        if self._duration > 0:
            self.seek_requested.emit(int(self.slider.value() / self.SLIDER_MAX * self._duration))

    def _gap_changed(self) -> None:
        self.gap_changed.emit(self.gap())
