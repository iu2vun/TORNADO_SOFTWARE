"""«Intercetta porta»: man-in-the-middle fra un programma e un dispositivo tramite una coppia com0com."""
from __future__ import annotations

from typing import Optional

import serial.tools.list_ports
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .. import com0com
from ..config import PortConfig
from .portdef import BridgeDef, PortDef

HELP = ("Come funziona: il programma da osservare apre la porta virtuale «lato programma»; lo sniffer apre l'altro lato della coppia e il "
        "dispositivo reale, e inoltra i byte in entrambe le direzioni registrandoli.\n"
        "Limite: i cambi di baud/formato fatti dal programma sulla porta virtuale non vengono propagati alla porta reale: "
        "imposta qui la configurazione corretta.")


class InterceptDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], taken: set[str], last_config: str = "115200 8N1") -> None:
        super().__init__(parent)
        self.setWindowTitle("Intercetta porta (com0com)")
        self.resize(560, 0)
        self._taken = taken
        self._pairs: list[com0com.Pair] = []
        f = QFormLayout()
        self.lbl_state = QLabel("")
        self.lbl_state.setWordWrap(True)
        f.addRow(self.lbl_state)

        row = QHBoxLayout()
        self.ed_setupc = QLineEdit(com0com.find_setupc())
        self.ed_setupc.setPlaceholderText(r"…\com0com\setupc.exe")
        b = QPushButton("…"); b.setFixedWidth(30); b.clicked.connect(self._pick_setupc)
        row.addWidget(self.ed_setupc, 1); row.addWidget(b)
        f.addRow("setupc.exe", row)

        self.cmb_real = QComboBox()
        for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device):
            self.cmb_real.addItem(f"{p.device} — {p.description}", p.device)
        self.cmb_real.setEditable(True)
        f.addRow("Porta reale (dispositivo)", self.cmb_real)

        prow = QHBoxLayout()
        self.cmb_pair = QComboBox()
        self.btn_refresh = QPushButton("Aggiorna"); self.btn_refresh.clicked.connect(self._load_pairs)
        self.btn_new = QPushButton("Crea coppia…"); self.btn_new.clicked.connect(self._create)
        prow.addWidget(self.cmb_pair, 1); prow.addWidget(self.btn_refresh); prow.addWidget(self.btn_new)
        f.addRow("Coppia virtuale", prow)

        self.cmb_app = QComboBox()
        f.addRow("Il programma usa il lato", self.cmb_app)
        self.lbl_app = QLabel("")
        self.lbl_app.setStyleSheet("font-weight:bold")
        f.addRow("", self.lbl_app)

        self.ed_cfg = QLineEdit(last_config)
        self.ed_cfg.setToolTip("Baud e formato della porta reale, es. 115200 8N1")
        f.addRow("Baud / formato", self.ed_cfg)

        lay = QVBoxLayout(self)
        lay.addLayout(f)
        h = QLabel(HELP); h.setWordWrap(True); h.setObjectName("Dim")
        lay.addWidget(h)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.cmb_pair.currentIndexChanged.connect(self._pair_changed)
        self.cmb_app.currentIndexChanged.connect(self._update_hint)
        self.ed_setupc.editingFinished.connect(self._load_pairs)
        self._load_pairs()

    # ------------------------------------------------------------------ com0com
    def _pick_setupc(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "setupc.exe", self.ed_setupc.text(), "setupc.exe (setupc.exe);;Eseguibili (*.exe)")
        if p:
            self.ed_setupc.setText(p)
            self._load_pairs()

    def _load_pairs(self) -> None:
        exe = self.ed_setupc.text().strip().strip('"')
        self.cmb_pair.blockSignals(True)
        self.cmb_pair.clear()
        self._pairs = []
        ok = bool(exe) and __import__("os").path.isfile(exe)
        self.btn_new.setEnabled(ok); self.btn_refresh.setEnabled(ok)
        if not ok:
            self.lbl_state.setText("com0com non trovato. Installalo (driver firmato, come amministratore) oppure indica setupc.exe qui sotto.")
        else:
            try:
                self._pairs = com0com.list_pairs(exe)
            except Exception as e:  # noqa: BLE001
                self.lbl_state.setText(f"Impossibile leggere le coppie: {e}")
            else:
                usable = [p for p in self._pairs if p.a and p.b]
                self._pairs = usable
                self.lbl_state.setText(f"com0com rilevato: {len(usable)} coppie." if usable else
                                       "com0com rilevato ma senza coppie con nomi COM: creane una con «Crea coppia…».")
        for p in self._pairs:
            self.cmb_pair.addItem(p.label(), p)
        self.cmb_pair.blockSignals(False)
        self._pair_changed()

    def _create(self) -> None:
        exe = self.ed_setupc.text().strip().strip('"')
        try:
            pair, out = com0com.create_pair(exe)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "com0com", f"Creazione non riuscita: {e}\nServono i privilegi di amministratore.")
            return
        if pair is None:
            QMessageBox.warning(self, "com0com", "Creazione non riuscita.\n\n" + (out.strip() or "(nessun output)"))
            return
        self._load_pairs()
        i = self.cmb_pair.findText(pair.label())
        if i >= 0:
            self.cmb_pair.setCurrentIndex(i)

    def _pair_changed(self) -> None:
        p: Optional[com0com.Pair] = self.cmb_pair.currentData()
        self.cmb_app.blockSignals(True)
        self.cmb_app.clear()
        if p:
            self.cmb_app.addItem(f"{p.a}", "a")
            self.cmb_app.addItem(f"{p.b}", "b")
        self.cmb_app.blockSignals(False)
        self._update_hint()

    def _sides(self) -> Optional[tuple[str, str]]:
        """(porta per il programma, porta per lo sniffer)."""
        p: Optional[com0com.Pair] = self.cmb_pair.currentData()
        if not p:
            return None
        return (p.a, p.b) if self.cmb_app.currentData() == "a" else (p.b, p.a)

    def _update_hint(self) -> None:
        s = self._sides()
        self.lbl_app.setText(f"Nel programma da osservare apri: {s[0]}   (lo sniffer usa {s[1]})" if s else "")

    # ----------------------------------------------------------------- risultato
    def _real(self) -> str:
        i = self.cmb_real.currentIndex()
        if i >= 0 and self.cmb_real.itemText(i) == self.cmb_real.currentText():
            return str(self.cmb_real.itemData(i))
        return self.cmb_real.currentText().strip()

    def _accept(self) -> None:
        s = self._sides()
        try:
            if not s:
                raise ValueError("scegli o crea una coppia virtuale")
            if not self._real():
                raise ValueError("scegli la porta reale del dispositivo")
            if self._real().upper() in (s[0].upper(), s[1].upper()):
                raise ValueError("la porta reale non può far parte della coppia virtuale")
            PortConfig.parse(self.ed_cfg.text())
        except ValueError as e:
            QMessageBox.warning(self, "Dati non validi", str(e))
            return
        self.accept()

    def _unique(self, base: str) -> str:
        name, n = base, 1
        while name in self._taken:
            n += 1
            name = f"{base} {n}"
        return name

    def result(self) -> tuple[list[PortDef], BridgeDef, str]:
        """(due canali, bridge bidirezionale, porta da aprire nel programma)."""
        app_port, sniff_port = self._sides()  # type: ignore[misc]
        cfg = str(PortConfig.parse(self.ed_cfg.text()))
        dev = PortDef(self._unique(f"Dispositivo ({self._real()})"), self._real(), cfg)
        prog = PortDef(self._unique(f"Programma ({app_port})"), sniff_port, cfg)
        return [dev, prog], BridgeDef(dev.name, prog.name, True), app_port
