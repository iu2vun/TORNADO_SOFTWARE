"""Finestra guidata calibrazione OSL / SOLT / Normalization."""
from PySide6.QtCore import Qt, Signal, Slot
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QHeaderView, QDialog,
    QDoubleSpinBox, QFormLayout, QGroupBox, QMessageBox,
)
from benchvna.cal.standards import CAL_KIT_PRESETS, CalKit

# (sezione, chiave, etichetta, unita')
KIT_FIELDS = [
    ("open",  "delay_ps", "Open  offset delay",   "ps"),
    ("open",  "loss",     "Open  offset loss",    "GOhm/s"),
    ("open",  "C0",       "Open  C0",             "fF"),
    ("open",  "C1",       "Open  C1",             "1e-27 F/Hz"),
    ("open",  "C2",       "Open  C2",             "1e-36 F/Hz2"),
    ("open",  "C3",       "Open  C3",             "1e-45 F/Hz3"),
    ("short", "delay_ps", "Short offset delay",   "ps"),
    ("short", "loss",     "Short offset loss",    "GOhm/s"),
    ("short", "L0",       "Short L0",             "pH"),
    ("short", "L1",       "Short L1",             "1e-24 H/Hz"),
    ("short", "L2",       "Short L2",             "1e-33 H/Hz2"),
    ("short", "L3",       "Short L3",             "1e-42 H/Hz3"),
    ("load",  "resistance","Load  R",             "Ohm"),
    ("load",  "Lseries",  "Load  L serie",        "pH"),
    ("load",  "Cparallel","Load  C parallelo",    "fF"),
    ("load",  "delay_ps", "Load  offset delay",   "ps"),
    ("through","delay_ps","Through delay",        "ps"),
    ("through","loss",    "Through loss",         "dB/sqrt(GHz)"),
]


class KitEditor(QDialog):
    """Editor dei parametri del kit di calibrazione."""
    def __init__(self, parent, kit: dict):
        super().__init__(parent)
        self.setWindowTitle("Definizione kit di calibrazione")
        self.resize(420, 640)
        k = CalKit.from_dict(kit).to_dict()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self._sp = {}
        for sec, key, label, unit in KIT_FIELDS:
            sp = QDoubleSpinBox()
            sp.setRange(-1e9, 1e9); sp.setDecimals(5)
            sp.setValue(float(k[sec].get(key, 0.0)))
            sp.setSuffix(f"  {unit}")
            self._sp[(sec, key)] = sp
            form.addRow(label, sp)
        lay.addLayout(form)
        row = QHBoxLayout()
        ok = QPushButton("OK"); ok.setProperty("primary", True); ok.clicked.connect(self.accept)
        ko = QPushButton("Annulla"); ko.clicked.connect(self.reject)
        row.addStretch(1); row.addWidget(ko); row.addWidget(ok)
        lay.addLayout(row)

    def result_kit(self) -> dict:
        out = {"open": {}, "short": {}, "load": {}, "through": {}}
        for (sec, key), sp in self._sp.items():
            out[sec][key] = sp.value()
        return out

CAL_RECIPES = {
    "OSL — porta 1":          ("OSL",  [("Open",[1]),("Short",[1]),("Load",[1])]),
    "OSL — porta 2":          ("OSL",  [("Open",[2]),("Short",[2]),("Load",[2])]),
    "SOLT — 2 porte":         ("SOLT", [("Open",[1]),("Short",[1]),("Load",[1]),
                                        ("Open",[2]),("Short",[2]),("Load",[2]),
                                        ("Through",[1,2])]),
    "Normalization (Through)":("ThroughNormalization",[("Through",[1,2])]),
}


class CalibrationDialog(QWidget):
    setupReq   = Signal(list)
    measureReq = Signal(int)
    activateReq= Signal(str)
    saveReq    = Signal(str)
    loadReq    = Signal(str)
    kitReq     = Signal(dict)

    def __init__(self, parent, worker):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Calibrazione guidata")
        self.resize(520, 500)
        self._steps = []; self._short = None; self._cur = 0
        self._worker = worker   # tieni un riferimento, evita GC prematura
        self._kit = CAL_KIT_PRESETS["Ideale (tutti zero)"]
        if hasattr(worker, "calSetKit"):
            self.kitReq.connect(worker.calSetKit)

        self.setupReq.connect(worker.calSetup)
        self.measureReq.connect(worker.calMeasureStep)
        self.activateReq.connect(worker.calActivate)
        self.saveReq.connect(worker.calSave)
        self.loadReq.connect(worker.calLoad)
        worker.calStepDone.connect(self._on_step_done)
        worker.calFinished.connect(self._on_finished)

        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Tipo:"))
        self.combo = QComboBox(); self.combo.addItems(list(CAL_RECIPES))
        row.addWidget(self.combo, 1)
        btn_prep = QPushButton("Prepara"); btn_prep.clicked.connect(self._prepare)
        row.addWidget(btn_prep)
        lay.addLayout(row)

        krow = QHBoxLayout()
        krow.addWidget(QLabel("Kit:"))
        self.kit_combo = QComboBox(); self.kit_combo.addItems(list(CAL_KIT_PRESETS))
        self.kit_combo.currentTextChanged.connect(self._kit_preset)
        krow.addWidget(self.kit_combo, 1)
        b_ke = QPushButton("Modifica…"); b_ke.clicked.connect(self._kit_edit)
        b_ks = QPushButton("Salva kit"); b_ks.clicked.connect(self._kit_save)
        b_kl = QPushButton("Carica kit"); b_kl.clicked.connect(self._kit_load)
        for b in (b_ke, b_ks, b_kl): krow.addWidget(b)
        lay.addLayout(krow)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Standard","Porte","Stato"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)

        self.hint = QLabel("Scegli il tipo e premi «Prepara».")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        # etichetta di stato dedicata: mostra i messaggi del worker (status
        # signal) qui, dentro il dialog — la status bar della finestra
        # principale può restare nascosta dietro questa finestra.
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet(
            "color:#6fbf73; font-size:11px; font-family:Consolas,monospace;")
        lay.addWidget(self.status_lbl)
        worker.status.connect(self._on_status)

        brow = QHBoxLayout()
        self.btn_meas = QPushButton("Misura step corrente")
        self.btn_meas.setEnabled(False)
        self.btn_meas.clicked.connect(self._measure)
        brow.addWidget(self.btn_meas)
        self.btn_act = QPushButton("Attiva cal")
        self.btn_act.setEnabled(False)
        self.btn_act.clicked.connect(lambda: self.activateReq.emit(self._short))
        brow.addWidget(self.btn_act)
        lay.addLayout(brow)

        frow = QHBoxLayout()
        b_s = QPushButton("Salva .cal"); b_s.clicked.connect(self._save)
        b_l = QPushButton("Carica .cal"); b_l.clicked.connect(self._load)
        frow.addWidget(b_s); frow.addWidget(b_l)
        lay.addLayout(frow)

    @Slot(str)
    def _on_status(self, msg: str):
        self.status_lbl.setText(msg)

    def _kit_preset(self, name: str):
        self._kit = CAL_KIT_PRESETS.get(name, self._kit)

    def _kit_edit(self):
        dlg = KitEditor(self, self._kit)
        if dlg.exec():
            self._kit = dlg.result_kit()
            self.kit_combo.blockSignals(True)
            self.kit_combo.setCurrentIndex(-1)
            self.kit_combo.blockSignals(False)

    def _kit_save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Salva kit", "kit.json", "Kit (*.json)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(CalKit.from_dict(self._kit).to_dict(), f, indent=2)

    def _kit_load(self):
        p, _ = QFileDialog.getOpenFileName(self, "Carica kit", "", "Kit (*.json)")
        if p:
            try:
                with open(p, encoding="utf-8") as f:
                    self._kit = CalKit.from_dict(json.load(f)).to_dict()
                self.kit_combo.blockSignals(True)
                self.kit_combo.setCurrentIndex(-1)
                self.kit_combo.blockSignals(False)
            except Exception as e:
                QMessageBox.warning(self, "Kit", f"File non valido: {e}")

    def _prepare(self):
        self.kitReq.emit(CalKit.from_dict(self._kit).to_dict())
        self._short, self._steps = CAL_RECIPES[self.combo.currentText()]
        self._cur = 0
        self.table.setRowCount(len(self._steps))
        for r, (t, p) in enumerate(self._steps):
            self.table.setItem(r,0,QTableWidgetItem(t))
            self.table.setItem(r,1,QTableWidgetItem(",".join(map(str,p))))
            self.table.setItem(r,2,QTableWidgetItem("—"))
        self.status_lbl.setText("Invio configurazione al worker…")
        self.setupReq.emit(self._steps)
        self.btn_meas.setEnabled(True); self.btn_act.setEnabled(False)
        self._update_hint()

    def _measure(self):
        if self._cur < len(self._steps):
            self.table.setItem(self._cur, 2, QTableWidgetItem("…"))
            self.status_lbl.setText(f"Misura step {self._cur+1} in corso…")
            self.btn_meas.setEnabled(False)
            self.measureReq.emit(self._cur)

    @Slot(int, bool)
    def _on_step_done(self, idx, ok):
        self.table.setItem(idx, 2, QTableWidgetItem("✓" if ok else "✗"))
        self.btn_meas.setEnabled(True)
        if ok:
            self._cur += 1
        if self._cur >= len(self._steps):
            self.btn_meas.setEnabled(False); self.btn_act.setEnabled(True)
        self._update_hint()

    @Slot(bool, str)
    def _on_finished(self, ok, detail):
        self.hint.setText(f"Cal {'attiva' if ok else 'ERRORE'}: {detail}")

    def _update_hint(self):
        if self._cur < len(self._steps):
            t, p = self._steps[self._cur]
            self.hint.setText(
                f"Step {self._cur+1}/{len(self._steps)}: "
                f"connetti <b>{t}</b> alla porta {p}, poi «Misura».")
            self.table.selectRow(self._cur)
        else:
            self.hint.setText("Tutti gli step completati. Premi «Attiva cal».")

    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self,"Salva .cal","cal.cal","Cal (*.cal)")
        if p: self.saveReq.emit(p)

    def _load(self):
        p, _ = QFileDialog.getOpenFileName(self,"Carica .cal","","Cal (*.cal)")
        if p: self.loadReq.emit(p)
