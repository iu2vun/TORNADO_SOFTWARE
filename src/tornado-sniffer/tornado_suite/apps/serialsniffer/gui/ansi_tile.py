"""Vista «Terminale ANSI/VT100»: emulazione completa (colori, cursore, cancellazioni, scroll) del flusso sniffato.

Riusa AnsiEngine/TerminalWidget di Tornado-Term. È passiva: nessun tasto o risposta del terminale
viene mai inviato alla porta. Mostra un solo canale alla volta (più flussi mescolati in un unico
schermo VT non avrebbero senso); RX+TX li alimenta entrambi nell'ordine di arrivo.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QScrollBar, QWidget
from PySide6.QtCore import Qt

from ...termwidget.ansi_engine import ENCODINGS, AnsiEngine, HexDumpMode
from ...termwidget.terminal_widget import ResizeMode, TerminalWidget
from ..chunk import GLOBAL, RX, Chunk
from .hub import DataHub
from .tiles import BaseTile

SIZES = [("Auto", None), ("80×24", (80, 24)), ("80×25", (80, 25)), ("132×24", (132, 24)), ("132×43", (132, 43))]


class AnsiTile(BaseTile):
    kind = "ansi"
    title = "Terminale ANSI/VT100"

    def __init__(self, hub: DataHub, parent: Optional[QWidget] = None) -> None:
        super().__init__(hub, parent)
        self.engine = AnsiEngine(cols=80, rows=24)
        self.term = TerminalWidget(self.engine, font_size=10)
        self.scroll = QScrollBar(Qt.Orientation.Vertical)
        self.scroll.setEnabled(False)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.term, 1)
        row.addWidget(self.scroll)
        self._root.addLayout(row, 1)

        self.cmb_enc = QComboBox()
        for name in ENCODINGS:
            self.cmb_enc.addItem(name)
        self.cmb_enc.setToolTip("Codifica dei caratteri; le sequenze ANSI/VT sono sempre interpretate")
        self.cmb_size = QComboBox()
        for label, val in SIZES:
            self.cmb_size.addItem(label, val)
        self.cmb_size.setToolTip("Dimensione dello schermo: Auto segue la vista; le altre la fissano e adattano il font")
        self.chk_hex = QCheckBox("Hex")
        self.chk_hex.setToolTip("Mostra i byte in esadecimale invece di interpretarli")
        for w in (self.cmb_enc, self.cmb_size, self.chk_hex):
            self._insert_extra(w)

        # Un nuovo riquadro mostra di default solo RX (il flusso che il terminale vedrebbe).
        self.dir_filter = RX
        self.cmb_dir.blockSignals(True)
        self.cmb_dir.setCurrentIndex(max(0, self.cmb_dir.findData(RX)))
        self.cmb_dir.blockSignals(False)

        self.cmb_enc.currentIndexChanged.connect(self._on_enc)
        self.cmb_size.currentIndexChanged.connect(self._on_size)
        self.chk_hex.toggled.connect(self._on_hex)
        self.scroll.valueChanged.connect(lambda v: self.engine.scroll_to(self.scroll.maximum() - v))
        self.engine.scrollChanged.connect(self._on_scroll_changed)
        self.rebuild()

    # ------------------------------------------------------------- canali
    def _refresh_channels(self) -> None:
        cur = self.channel_filter
        self.cmb_channel.blockSignals(True)
        self.cmb_channel.clear()
        for idx in sorted(self.hub.channels):
            self.cmb_channel.addItem(self.hub.name(idx), idx)
        pos = self.cmb_channel.findData(cur)
        if pos < 0:
            pos = 0
        self.cmb_channel.setCurrentIndex(pos)
        self.channel_filter = self.cmb_channel.currentData()
        self.cmb_channel.blockSignals(False)

    def _on_channel_changed(self) -> None:
        self.channel_filter = self.cmb_channel.currentData()
        self.rebuild()

    def accepts(self, c: Chunk) -> bool:
        if c.is_event or c.channel == GLOBAL:
            return False
        return super().accepts(c)

    # --------------------------------------------------------------- dati
    def append(self, chunks: list[Chunk]) -> None:
        for c in chunks:
            self.engine.feed(c.data)

    def reset(self) -> None:
        self.engine.reset()
        self.engine.feed(b"\x1b[2J\x1b[H")

    # ------------------------------------------------------------ opzioni
    def _on_enc(self) -> None:
        self.engine.encoding = ENCODINGS[self.cmb_enc.currentText()]
        self.rebuild()

    def _on_size(self) -> None:
        val = self.cmb_size.currentData()
        if val is None:
            self.term.resize_mode = ResizeMode.BUFFER
            self.engine.resize(max(1, self.term.width() // self.term._cell_w), max(1, self.term.height() // self.term._cell_h))
        else:
            self.engine.resize(*val)
            self.term.resize_mode = ResizeMode.FONT
        self.rebuild()

    def _on_hex(self, on: bool) -> None:
        self.engine.hex_mode = HexDumpMode.WIDE if on else HexDumpMode.OFF
        self.rebuild()

    def _on_scroll_changed(self, offset: int, max_offset: int) -> None:
        self.scroll.blockSignals(True)
        self.scroll.setEnabled(max_offset > 0)
        self.scroll.setRange(0, max_offset)
        self.scroll.setPageStep(max(1, self.engine.rows - 1))
        self.scroll.setValue(max_offset - offset)
        self.scroll.blockSignals(False)

    # --------------------------------------------------------- persistenza
    def get_state(self) -> dict:
        return {**super().get_state(), "enc": self.cmb_enc.currentText(), "size": self.cmb_size.currentText(),
                "hex": self.chk_hex.isChecked(), "font": self.term.font_size}

    def set_state(self, st: dict) -> None:
        super().set_state(st)
        for cmb, key in ((self.cmb_enc, "enc"), (self.cmb_size, "size")):
            i = cmb.findText(str(st.get(key, "")))
            cmb.blockSignals(True)
            cmb.setCurrentIndex(max(0, i))
            cmb.blockSignals(False)
        self.chk_hex.blockSignals(True); self.chk_hex.setChecked(bool(st.get("hex", False))); self.chk_hex.blockSignals(False)
        self.engine.encoding = ENCODINGS[self.cmb_enc.currentText()]
        self.engine.hex_mode = HexDumpMode.WIDE if self.chk_hex.isChecked() else HexDumpMode.OFF
        if isinstance(st.get("font"), int):
            self.term.font_size = st["font"]
        val = self.cmb_size.currentData()
        if val:
            self.engine.resize(*val); self.term.resize_mode = ResizeMode.FONT
        self.rebuild()
