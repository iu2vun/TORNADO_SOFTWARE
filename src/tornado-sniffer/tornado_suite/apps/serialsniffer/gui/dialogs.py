"""Dialoghi: configurazione porta, bridge, export."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import serial.tools.list_ports
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .. import decoders
from ..autobaud import COMMON_BAUDS, detect_baud
from ..config import FLOW_MODES, PortConfig
from ..exporters import EXPORT_KINDS, export
from ..formats import FORMATS
from ..ssnfile import SsnError, read_ssn
from .portdef import BridgeDef, PortDef

_PARITIES = [("Nessuna (N)", "N"), ("Pari (E)", "E"), ("Dispari (O)", "O"), ("Mark (M)", "M"), ("Space (S)", "S")]
_STOPS = [("1", 1.0), ("1.5", 1.5), ("2", 2.0)]


class _BaudWorker(QThread):
    progress = Signal(int, int)  # baud, byte ricevuti
    results = Signal(list)

    def __init__(self, url: str, framing: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.url = url
        self.framing = framing
        self._stop_evt = threading.Event()

    def cancel(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        try:
            res = detect_baud(
                self.url, framing=self.framing, sample_s=0.6, stop=self._stop_evt,
                on_progress=lambda r: self.progress.emit(r.baudrate, r.nbytes),
            )
        except Exception:
            res = []
        self.results.emit(res)


class PortDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], taken_names: set[str], pd: Optional[PortDef] = None,
                 default_name: str = "CH1") -> None:
        super().__init__(parent)
        self.setWindowTitle("Porta seriale")
        self.setMinimumWidth(440)
        self._taken = set(taken_names)
        self._worker: Optional[_BaudWorker] = None
        cfg = PortConfig.parse(pd.config) if pd else PortConfig()

        self.ed_name = QLineEdit(pd.name if pd else default_name)
        self.cmb_port = QComboBox()
        self.cmb_port.setEditable(True)
        self.cmb_port.setToolTip("Porta (COM3, /dev/ttyUSB0) oppure URL: loop://, socket://host:porta, rfc2217://host:porta")
        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(32)
        btn_refresh.setToolTip("Aggiorna l'elenco delle porte")
        btn_refresh.clicked.connect(self._fill_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self.cmb_port, 1)
        port_row.addWidget(btn_refresh)

        self.cmb_baud = QComboBox()
        self.cmb_baud.setEditable(True)
        self.cmb_baud.setValidator(QIntValidator(1, 100_000_000, self))
        for b in COMMON_BAUDS:
            self.cmb_baud.addItem(str(b))
        self.cmb_baud.setEditText(str(cfg.baudrate))
        self.cmb_bits = QComboBox()
        for b in (5, 6, 7, 8):
            self.cmb_bits.addItem(str(b), b)
        self.cmb_bits.setCurrentIndex(self.cmb_bits.findData(cfg.bytesize))
        self.cmb_parity = QComboBox()
        for label, v in _PARITIES:
            self.cmb_parity.addItem(label, v)
        self.cmb_parity.setCurrentIndex(self.cmb_parity.findData(cfg.parity))
        self.cmb_stop = QComboBox()
        for label, v in _STOPS:
            self.cmb_stop.addItem(label, v)
        self.cmb_stop.setCurrentIndex(self.cmb_stop.findData(cfg.stopbits))
        self.cmb_flow = QComboBox()
        for f in FLOW_MODES:
            self.cmb_flow.addItem(f, f)
        self.cmb_flow.setCurrentIndex(self.cmb_flow.findData(cfg.flow))

        self.btn_auto = QPushButton("Rileva baud…")
        self.btn_auto.setToolTip("Prova i baud più comuni ascoltando la linea (solo flussi di testo, la linea deve trasmettere)")
        self.btn_auto.clicked.connect(self._toggle_autobaud)
        self.lbl_auto = QLabel("")
        self.lbl_auto.setWordWrap(True)
        baud_row = QHBoxLayout()
        baud_row.addWidget(self.cmb_baud, 1)
        baud_row.addWidget(self.btn_auto)

        self.cmb_gap = QComboBox()
        self.cmb_gap.addItem("Automatico (≥ 20 ms)", "auto")
        self.cmb_gap.addItem("Disattivato (un chunk per lettura)", "off")
        self.cmb_gap.addItem("Personalizzato", "custom")
        self.spn_gap = QDoubleSpinBox()
        self.spn_gap.setRange(0.1, 5000.0)
        self.spn_gap.setDecimals(1)
        self.spn_gap.setSuffix(" ms")
        self.spn_gap.setValue(50.0)
        self.cmb_gap.setToolTip(
            "I byte separati da meno di questo intervallo formano un unico messaggio.\n"
            "Con adattatori FTDI il latency timer (fino a 16 ms) può spezzare i messaggi: usa ≥ 20 ms.")
        if pd is not None and pd.idle_gap_ms is not None:
            if pd.idle_gap_ms == 0:
                self.cmb_gap.setCurrentIndex(1)
            else:
                self.cmb_gap.setCurrentIndex(2)
                self.spn_gap.setValue(pd.idle_gap_ms)
        gap_row = QHBoxLayout()
        gap_row.addWidget(self.cmb_gap, 1)
        gap_row.addWidget(self.spn_gap)
        self.cmb_gap.currentIndexChanged.connect(self._gap_changed)
        self._gap_changed()

        self.chk_reconnect = QCheckBox("Riconnetti automaticamente se la porta sparisce")
        self.chk_reconnect.setChecked(pd.reconnect if pd else True)
        self.chk_lines = QCheckBox("Monitora le linee modem (CTS, DSR, RI, CD)")
        self.chk_lines.setChecked(pd.monitor_lines if pd else False)

        form = QFormLayout()
        form.addRow("Nome", self.ed_name)
        form.addRow("Porta", port_row)
        form.addRow("Baud", baud_row)
        form.addRow("", self.lbl_auto)
        form.addRow("Bit dati", self.cmb_bits)
        form.addRow("Parità", self.cmb_parity)
        form.addRow("Bit di stop", self.cmb_stop)
        form.addRow("Controllo di flusso", self.cmb_flow)
        form.addRow("Raggruppa byte", gap_row)
        form.addRow("", self.chk_reconnect)
        form.addRow("", self.chk_lines)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

        self._fill_ports()
        if pd is not None:
            pos = self.cmb_port.findData(pd.url)
            if pos >= 0:
                self.cmb_port.setCurrentIndex(pos)
            else:
                self.cmb_port.setEditText(pd.url)

    # -------------------------------------------------------------------- porte
    def _fill_ports(self) -> None:
        current = self._port_value()
        self.cmb_port.clear()
        for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device):
            self.cmb_port.addItem(f"{p.device} — {p.description}", p.device)
        self.cmb_port.addItem("loop:// — porta virtuale di prova", "loop://")
        if current:
            pos = self.cmb_port.findData(current)
            if pos >= 0:
                self.cmb_port.setCurrentIndex(pos)
            else:
                self.cmb_port.setEditText(current)

    def _port_value(self) -> str:
        idx = self.cmb_port.currentIndex()
        if idx >= 0 and self.cmb_port.itemText(idx) == self.cmb_port.currentText():
            return str(self.cmb_port.itemData(idx))
        return self.cmb_port.currentText().strip()

    def _gap_changed(self) -> None:
        self.spn_gap.setEnabled(self.cmb_gap.currentData() == "custom")

    # ------------------------------------------------------------------ autobaud
    def _framing(self) -> str:
        stop = self.cmb_stop.currentData()
        return f"{self.cmb_bits.currentData()}{self.cmb_parity.currentData()}{'1.5' if stop == 1.5 else int(stop)}"

    def _toggle_autobaud(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.btn_auto.setEnabled(False)
            return
        url = self._port_value()
        if not url:
            self.lbl_auto.setText("Scegli prima la porta.")
            return
        self._worker = _BaudWorker(url, self._framing(), self)
        self._worker.progress.connect(lambda b, n: self.lbl_auto.setText(f"Provo {b} baud… ({n} byte)"))
        self._worker.results.connect(self._autobaud_done)
        self.btn_auto.setText("Annulla")
        self._worker.start()

    def _autobaud_done(self, results: list) -> None:
        self.btn_auto.setText("Rileva baud…")
        self.btn_auto.setEnabled(True)
        best = results[0] if results else None
        if best is not None and best.score >= 0.8 and best.nbytes >= 4:
            self.cmb_baud.setEditText(str(best.baudrate))
            alts = ", ".join(f"{r.baudrate} ({r.score:.2f})" for r in results[1:3] if r.score > 0)
            self.lbl_auto.setText(f"Rilevato {best.baudrate} baud (punteggio {best.score:.2f})" + (f"; altri: {alts}" if alts else ""))
        else:
            self.lbl_auto.setText("Nessun baud affidabile: la linea trasmette testo durante la prova?")

    def _stop_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)

    def reject(self) -> None:
        self._stop_worker()
        super().reject()

    def done(self, r: int) -> None:
        self._stop_worker()
        super().done(r)

    # ----------------------------------------------------------------- risultato
    def _config(self) -> PortConfig:
        text = self.cmb_baud.currentText().strip()
        if not text.isdigit():
            raise ValueError("baud non valido")
        return PortConfig(int(text), self.cmb_bits.currentData(), self.cmb_parity.currentData(),
                          self.cmb_stop.currentData(), self.cmb_flow.currentData())

    def _accept(self) -> None:
        name = self.ed_name.text().strip()
        try:
            if not name:
                raise ValueError("il nome non può essere vuoto")
            if name in self._taken:
                raise ValueError(f"esiste già un canale chiamato {name!r}")
            if not self._port_value():
                raise ValueError("scegli o scrivi la porta")
            self._config()
        except ValueError as exc:
            QMessageBox.warning(self, "Dati non validi", str(exc))
            return
        self.accept()

    def result_def(self) -> PortDef:
        mode = self.cmb_gap.currentData()
        gap = None if mode == "auto" else (0.0 if mode == "off" else float(self.spn_gap.value()))
        return PortDef(
            name=self.ed_name.text().strip(), url=self._port_value(), config=str(self._config()),
            idle_gap_ms=gap, reconnect=self.chk_reconnect.isChecked(), monitor_lines=self.chk_lines.isChecked(),
        )


class BridgeDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], port_names: list[str], bridges: list[BridgeDef]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bridge fra porte")
        self.setMinimumWidth(420)
        self._bridges = [BridgeDef(b.a, b.b, b.bidirectional) for b in bridges]
        info = QLabel("Il PC sta in mezzo alla linea: i byte letti da una porta vengono inoltrati subito all'altra "
                      "(senza attendere il raggruppamento) e registrati come RX di chi li riceve.")
        info.setWordWrap(True)
        self.lst = QListWidget()
        self.cmb_a = QComboBox()
        self.cmb_b = QComboBox()
        for n in port_names:
            self.cmb_a.addItem(n)
            self.cmb_b.addItem(n)
        if len(port_names) > 1:
            self.cmb_b.setCurrentIndex(1)
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("A ⇄ B (bidirezionale)", "both")
        self.cmb_mode.addItem("A → B", "ab")
        self.cmb_mode.addItem("B → A", "ba")
        btn_add = QPushButton("Aggiungi")
        btn_del = QPushButton("Rimuovi selezionato")
        btn_add.clicked.connect(self._add)
        btn_del.clicked.connect(self._remove)
        row = QHBoxLayout()
        for w in (QLabel("A"), self.cmb_a, QLabel("B"), self.cmb_b, self.cmb_mode, btn_add):
            row.addWidget(w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.lst, 1)
        lay.addWidget(btn_del)
        lay.addLayout(row)
        lay.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self.lst.clear()
        for b in self._bridges:
            self.lst.addItem(b.text())

    def _add(self) -> None:
        a, b, mode = self.cmb_a.currentText(), self.cmb_b.currentText(), self.cmb_mode.currentData()
        if not a or not b or a == b:
            QMessageBox.warning(self, "Bridge", "Scegli due porte diverse.")
            return
        if mode == "ba":
            a, b, mode = b, a, "ab"
        new = BridgeDef(a, b, mode == "both")
        if any({x.a, x.b} == {new.a, new.b} and (x.bidirectional or new.bidirectional or (x.a, x.b) == (new.a, new.b))
               for x in self._bridges):
            QMessageBox.information(self, "Bridge", "Esiste già un bridge fra queste porte.")
            return
        self._bridges.append(new)
        self._reload()

    def _remove(self) -> None:
        row = self.lst.currentRow()
        if row >= 0:
            del self._bridges[row]
            self._reload()

    def bridges(self) -> list[BridgeDef]:
        return self._bridges


class OutputsDialog(QDialog):
    """Associa i canali di una registrazione a porte seriali reali su cui rilanciare i dati."""

    def __init__(self, parent: Optional[QWidget], names: list[str], current: dict[str, str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Uscite di riproduzione")
        self.setMinimumWidth(480)
        info = QLabel("Scegli, per ciascun canale, la porta su cui rilanciare i dati registrati con i tempi originali "
                      "(es. un adattatore collegato al dispositivo da stimolare). Formato: PORTA oppure PORTA@115200,8N1. "
                      "Senza @ vale la configurazione registrata. Le porte devono essere diverse da quelle in acquisizione.")
        info.setWordWrap(True)
        ports = sorted(serial.tools.list_ports.comports(), key=lambda x: x.device)
        self._combos: dict[str, QComboBox] = {}
        form = QFormLayout()
        for name in names:
            cmb = QComboBox()
            cmb.setEditable(True)
            cmb.addItem("(nessuna: solo a schermo)", "")
            for p in ports:
                cmb.addItem(f"{p.device} — {p.description}", p.device)
            spec = current.get(name, "")
            pos = cmb.findData(spec)
            if pos >= 0:
                cmb.setCurrentIndex(pos)
            else:
                cmb.setEditText(spec)
            self._combos[name] = cmb
            form.addRow(name, cmb)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def mapping(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, cmb in self._combos.items():
            idx = cmb.currentIndex()
            text = str(cmb.itemData(idx)) if idx >= 0 and cmb.itemText(idx) == cmb.currentText() else cmb.currentText().strip()
            if text:
                out[name] = text
        return out


_KIND_LABELS = {
    "csv": "CSV (tabella, apribile in Excel)", "txt": "TXT (una riga per messaggio)",
    "hex": "HEX (hexdump per messaggio)", "jsonl": "JSONL (un oggetto JSON per record)",
    "raw": "RAW (un .bin per flusso, solo byte)", "pcap": "PCAP (Wireshark, DLT_USER0)",
    "decoded": "Decodificato (CSV o JSONL: un messaggio di protocollo per riga)",
    "stats": "Statistiche (CSV o JSON: frequenza, jitter, latenze, errori)",
}
_KIND_EXT = {"csv": ".csv", "txt": ".txt", "hex": ".hex.txt", "jsonl": ".jsonl", "raw": "", "pcap": ".pcap",
             "decoded": ".decoded.csv", "stats": ".stats.csv"}


class ExportDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], source: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Esporta registrazione")
        self.setMinimumWidth(560)
        self._out_touched = False

        self.ed_src = QLineEdit(source)
        btn_src = QPushButton("Sfoglia…")
        btn_src.clicked.connect(self._browse_src)
        src_row = QHBoxLayout()
        src_row.addWidget(self.ed_src, 1)
        src_row.addWidget(btn_src)

        self.cmb_kind = QComboBox()
        for k in EXPORT_KINDS:
            self.cmb_kind.addItem(_KIND_LABELS.get(k, k), k)
        self.cmb_view = QComboBox()
        for f in FORMATS:
            self.cmb_view.addItem(f, f)
        self.cmb_view.setCurrentIndex(self.cmb_view.findData("escaped"))
        self.cmb_view.setToolTip("Vista testuale dei dati nelle colonne/righe di testo (CSV e TXT)")
        self.cmb_proto = QComboBox()
        self.cmb_proto.setToolTip("Decoder per i formati Decodificato e Statistiche")
        for name in sorted(decoders.DECODERS, key=lambda n: (n != "auto", n)):
            self.cmb_proto.addItem(decoders.DECODERS[name].label or name, name)
        self.cmb_dirs = QComboBox()
        for label, v in (("RX e TX", None), ("Solo RX", "rx"), ("Solo TX", "tx")):
            self.cmb_dirs.addItem(label, v)
        self.ed_channels = QLineEdit()
        self.ed_channels.setPlaceholderText("tutti (oppure nomi/indici separati da virgola)")
        self.chk_events = QCheckBox("Includi eventi (apertura, errori, marker)")
        self.chk_events.setChecked(True)

        self.ed_out = QLineEdit()
        btn_out = QPushButton("Salva come…")
        btn_out.clicked.connect(self._browse_out)
        out_row = QHBoxLayout()
        out_row.addWidget(self.ed_out, 1)
        out_row.addWidget(btn_out)

        form = QFormLayout()
        form.addRow("Registrazione (.ssn)", src_row)
        form.addRow("Formato", self.cmb_kind)
        form.addRow("Vista testo", self.cmb_view)
        form.addRow("Protocollo", self.cmb_proto)
        form.addRow("Direzione", self.cmb_dirs)
        form.addRow("Canali", self.ed_channels)
        form.addRow("", self.chk_events)
        form.addRow("File di uscita", out_row)

        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        self.lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_go = QPushButton("Esporta")
        btn_go.setDefault(True)
        btn_go.clicked.connect(self._export)
        btn_close = QPushButton("Chiudi")
        btn_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_go)
        row.addWidget(btn_close)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.lbl)
        lay.addLayout(row)

        self.ed_src.textChanged.connect(self._suggest_out)
        self.cmb_kind.currentIndexChanged.connect(self._on_kind)
        self.ed_out.textEdited.connect(lambda _: setattr(self, "_out_touched", True))
        self._on_kind()
        self._suggest_out()

    def _on_kind(self) -> None:
        self.cmb_view.setEnabled(self.cmb_kind.currentData() in ("csv", "txt"))
        self.cmb_proto.setEnabled(self.cmb_kind.currentData() in ("decoded", "stats"))
        self._suggest_out()

    def _suggest_out(self) -> None:
        if self._out_touched:
            return
        src = self.ed_src.text().strip()
        if not src:
            return
        p = Path(src)
        self.ed_out.setText(str(p.with_suffix("")) + _KIND_EXT[self.cmb_kind.currentData()])

    def _browse_src(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Registrazione", self.ed_src.text(), "Registrazioni (*.ssn);;Tutti i file (*)")
        if path:
            self.ed_src.setText(path)

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "File di uscita", self.ed_out.text())
        if path:
            self.ed_out.setText(path)
            self._out_touched = True

    def _export(self) -> None:
        src, out = self.ed_src.text().strip(), self.ed_out.text().strip()
        if not src or not out:
            self.lbl.setText("Indica la registrazione e il file di uscita.")
            return
        try:
            rec = read_ssn(src)
            toks = [t.strip() for t in self.ed_channels.text().split(",") if t.strip()]
            channels = [rec.resolve_channel(t) for t in toks] or None
            d = self.cmb_dirs.currentData()
            directions = None if d is None else [0 if d == "rx" else 1]
            paths = export(rec, self.cmb_kind.currentData(), out, channels=channels, directions=directions,
                           fmt=self.cmb_view.currentData(), include_events=self.chk_events.isChecked(),
                           proto=self.cmb_proto.currentData())
        except (SsnError, ValueError, OSError) as exc:
            self.lbl.setText(f"Errore: {exc}")
            return
        note = f"\n(file troncato: {rec.error}; esportati i record validi)" if rec.truncated else ""
        self.lbl.setText("Creato:\n" + "\n".join(str(p) for p in paths) + note)


class TriggerDialog(QDialog):
    """Editor delle regole di trigger. Le regole valgono solo sull'acquisizione live."""

    COLS = ("Attivo", "Nome", "Pattern", "Modo", "Aa", "Canale", "Dir", "Azione", "Pausa s")

    def __init__(self, rules: list, channel_names: list[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QTableWidget, QAbstractItemView  # locale: serve solo qui
        self.setWindowTitle("Trigger")
        self.resize(980, 380)
        self._names = channel_names
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 220)
        btn_add = QPushButton("Aggiungi")
        btn_del = QPushButton("Rimuovi")
        btn_add.clicked.connect(lambda: self._add_row(None))
        btn_del.clicked.connect(self._del_row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        hint = QLabel("Quando un messaggio ricevuto/inviato contiene il pattern, esegue l'azione (al massimo una volta "
                      "ogni «Pausa» secondi). Testo: \\r \\n \\xNN ammessi; Hex: «41 54 ?? 0D». Solo in acquisizione live.")
        hint.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        lay.addWidget(bb)
        for r in rules:
            self._add_row(r)
        self.result_rules: list = []

    def _add_row(self, rule) -> None:
        from PySide6.QtWidgets import QTableWidgetItem
        from ..search import MODE_LABELS, MODES
        from ..triggers import ACTION_LABELS, ACTIONS, TriggerRule
        rule = rule or TriggerRule(name=f"trigger {self.table.rowCount() + 1}")
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
        self.table.setItem(r, 0, chk)
        self.table.setItem(r, 1, QTableWidgetItem(rule.name))
        self.table.setItem(r, 2, QTableWidgetItem(rule.pattern))
        cm = QComboBox()
        for m in MODES:
            cm.addItem(MODE_LABELS[m], m)
        cm.setCurrentIndex(max(0, cm.findData(rule.mode)))
        self.table.setCellWidget(r, 3, cm)
        ci = QCheckBox()
        ci.setChecked(rule.ignore_case)
        self.table.setCellWidget(r, 4, ci)
        cc = QComboBox()
        cc.addItem("Qualsiasi", None)
        for i, n in enumerate(self._names):
            cc.addItem(n, i)
        cc.setCurrentIndex(max(0, cc.findData(rule.channel)))
        self.table.setCellWidget(r, 5, cc)
        cd = QComboBox()
        for label, val in (("RX+TX", None), ("RX", 0), ("TX", 1)):
            cd.addItem(label, val)
        cd.setCurrentIndex(max(0, cd.findData(rule.direction)))
        self.table.setCellWidget(r, 6, cd)
        ca = QComboBox()
        for a in ACTIONS:
            ca.addItem(ACTION_LABELS[a], a)
        ca.setCurrentIndex(max(0, ca.findData(rule.action)))
        self.table.setCellWidget(r, 7, ca)
        sp = QDoubleSpinBox()
        sp.setRange(0, 3600)
        sp.setDecimals(1)
        sp.setValue(rule.cooldown_s)
        self.table.setCellWidget(r, 8, sp)

    def _del_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _accept(self) -> None:
        from ..search import PatternError, compile_pattern
        from ..triggers import TriggerRule
        rules = []
        for r in range(self.table.rowCount()):
            g = lambda c: self.table.cellWidget(r, c)  # noqa: E731
            rule = TriggerRule(
                name=self.table.item(r, 1).text().strip() or f"trigger {r + 1}", pattern=self.table.item(r, 2).text(),
                mode=g(3).currentData(), ignore_case=g(4).isChecked(), channel=g(5).currentData(),
                direction=g(6).currentData(), action=g(7).currentData(), cooldown_s=g(8).value(),
                enabled=self.table.item(r, 0).checkState() == Qt.CheckState.Checked,
            )
            try:
                compile_pattern(rule.pattern, rule.mode, rule.ignore_case)
            except PatternError as exc:
                QMessageBox.warning(self, "Trigger", f"Riga {r + 1} ({rule.name}): {exc}")
                return
            rules.append(rule)
        self.result_rules = rules
        self.accept()
