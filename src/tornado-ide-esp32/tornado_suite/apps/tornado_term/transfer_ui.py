"""Finestra di trasferimento: invio ASCII, invio XMODEM/YMODEM, ricezione XMODEM/YMODEM."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
)

from .transfer import AsciiSender, XYModem

EOLS = {"Come nel file": None, "CR": b"\r", "CR+LF": b"\r\n", "LF": b"\n"}
TITLES = {"ascii": "Invia file ASCII", "xsend": "Invia XMODEM/YMODEM", "xrecv": "Ricevi XMODEM/YMODEM"}


def safe_name(name: str, fallback: str) -> str:
    """Solo il nome base, senza percorsi ne' caratteri non ammessi."""
    base = re.split(r"[\\/]", name.strip())[-1]
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip(" .")
    return base or fallback


def unique_path(folder: str, name: str) -> str:
    path = os.path.join(folder, name)
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}_{i}{ext}")
        i += 1
    return path


class TransferDialog(QDialog):
    def __init__(self, kind: str, ascii_tx: AsciiSender, xy: XYModem, settings: QSettings, parent=None):
        super().__init__(parent)
        self.kind, self.asc, self.xy, self.s = kind, ascii_tx, xy, settings
        self.setWindowTitle(TITLES[kind])
        self.setMinimumWidth(480)
        self._files: list[str] = []
        self._saved: list[str] = []
        self._running = False
        k = "transfer/" + kind + "/"

        self.ed_file = QLineEdit()
        self.ed_file.setReadOnly(kind != "xrecv")
        btn_browse = QPushButton("Sfoglia...")
        row = QHBoxLayout(); row.addWidget(self.ed_file, 1); row.addWidget(btn_browse)
        form = QFormLayout()

        self.cb_var = QComboBox(); self.cb_var.addItems(XYModem.VARIANTS)
        self.cb_eol = QComboBox(); self.cb_eol.addItems(EOLS)
        self.sp_char = QSpinBox(); self.sp_char.setRange(0, 5000); self.sp_char.setSuffix(" ms")
        self.sp_line = QSpinBox(); self.sp_line.setRange(0, 60000); self.sp_line.setSuffix(" ms")
        self.chk_strip = QCheckBox("Elimina il riempimento 0x1A finale (XMODEM)")
        self.ed_name = QLineEdit(); self.ed_name.setPlaceholderText("nome file (solo XMODEM)")

        if kind == "ascii":
            form.addRow("File", row)
            form.addRow("Fine riga", self.cb_eol)
            form.addRow("Ritardo per carattere", self.sp_char)
            form.addRow("Ritardo per riga", self.sp_line)
        elif kind == "xsend":
            form.addRow("File (piu' di uno solo con YMODEM)", row)
            form.addRow("Protocollo", self.cb_var)
        else:
            form.addRow("Cartella di destinazione", row)
            form.addRow("Protocollo", self.cb_var)
            form.addRow("Nome file", self.ed_name)
            form.addRow(self.chk_strip)

        self.bar = QProgressBar(); self.bar.setRange(0, 100)
        self.lbl = QLabel("Pronto")
        self.lbl.setWordWrap(True)
        self.btn_go = QPushButton("Avvia")
        self.btn_cancel = QPushButton("Annulla trasferimento")
        self.btn_cancel.setEnabled(False)
        self.btn_close = QPushButton("Chiudi")
        btns = QHBoxLayout()
        btns.addStretch(1); btns.addWidget(self.btn_go); btns.addWidget(self.btn_cancel); btns.addWidget(self.btn_close)
        lay = QVBoxLayout(self)
        lay.addLayout(form); lay.addWidget(self.bar); lay.addWidget(self.lbl); lay.addLayout(btns)

        # valori salvati
        self.cb_var.setCurrentText(self.s.value(k + "variant", "XMODEM-CRC", str))
        self.cb_eol.setCurrentText(self.s.value(k + "eol", "CR", str))
        self.sp_char.setValue(self.s.value(k + "char_ms", 0, int))
        self.sp_line.setValue(self.s.value(k + "line_ms", 0, int))
        self.chk_strip.setChecked(self.s.value(k + "strip", False, bool))
        if kind == "xrecv":
            self.ed_file.setText(self.s.value(k + "folder", os.path.expanduser("~"), str))
        self.cb_var.currentTextChanged.connect(self._variant_changed)
        self._variant_changed()

        btn_browse.clicked.connect(self._browse)
        self.btn_go.clicked.connect(self._start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_close.clicked.connect(self.close)

        if kind == "ascii":
            self.asc.progress.connect(self._on_progress)
            self.asc.finished.connect(self._on_finished)
        else:
            self.xy.progress.connect(self._on_progress)
            self.xy.status.connect(self.lbl.setText)
            self.xy.finished.connect(self._on_finished)
            self.xy.file_received.connect(self._on_file)

    # --- UI ---------------------------------------------------------------
    def _variant_changed(self, *_):
        ym = self.cb_var.currentText() == "YMODEM"
        self.ed_name.setEnabled(not ym)
        self.chk_strip.setEnabled(not ym)

    def _browse(self):
        if self.kind == "xrecv":
            d = QFileDialog.getExistingDirectory(self, "Cartella di destinazione", self.ed_file.text())
            if d:
                self.ed_file.setText(d)
            return
        multi = self.kind == "xsend"
        start = self.s.value("transfer/last_dir", "", str)
        if multi:
            files, _ = QFileDialog.getOpenFileNames(self, "Scegli i file", start)
        else:
            f, _ = QFileDialog.getOpenFileName(self, "Scegli il file", start, "Testo (*.txt *.cmd *.scr *.hex *.log);;Tutti (*)")
            files = [f] if f else []
        if files:
            self._files = files
            self.s.setValue("transfer/last_dir", os.path.dirname(files[0]))
            self.ed_file.setText("; ".join(os.path.basename(f) for f in files))

    def _set_running(self, on: bool):
        self._running = on
        self.btn_go.setEnabled(not on)
        self.btn_cancel.setEnabled(on)
        for w in (self.cb_var, self.cb_eol, self.sp_char, self.sp_line, self.chk_strip):
            w.setEnabled(not on)
        if not on:
            self._variant_changed()

    # --- avvio ------------------------------------------------------------
    def _start(self):
        if self.asc.active or self.xy.active:
            QMessageBox.information(self, self.windowTitle(), "Un altro trasferimento e' gia' in corso.")
            return
        k = "transfer/" + self.kind + "/"
        self.s.setValue(k + "variant", self.cb_var.currentText())
        self.s.setValue(k + "eol", self.cb_eol.currentText())
        self.s.setValue(k + "char_ms", self.sp_char.value())
        self.s.setValue(k + "line_ms", self.sp_line.value())
        self.s.setValue(k + "strip", self.chk_strip.isChecked())
        self._saved.clear()
        self.bar.setValue(0)
        if self.kind == "xrecv":
            folder = self.ed_file.text().strip()
            if not os.path.isdir(folder):
                QMessageBox.warning(self, self.windowTitle(), "La cartella di destinazione non esiste.")
                return
            self.s.setValue(k + "folder", folder)
            ok = self.xy.receive(self.cb_var.currentText(), self.chk_strip.isChecked())
        else:
            if not self._files:
                QMessageBox.warning(self, self.windowTitle(), "Scegli prima il file da inviare.")
                return
            try:
                blobs = [(os.path.basename(f), open(f, "rb").read()) for f in self._files]
            except OSError as ex:
                QMessageBox.warning(self, self.windowTitle(), f"Impossibile leggere il file: {ex}")
                return
            if self.kind == "ascii":
                ok = self.asc.start(blobs[0][1], self.sp_char.value(), self.sp_line.value(),
                                    EOLS[self.cb_eol.currentText()])
            else:
                ok = self.xy.send(blobs, self.cb_var.currentText())
        if ok:
            self.lbl.setText("In corso...")
            self._set_running(True)

    def _cancel(self):
        if self.kind == "ascii":
            self.asc.cancel()
        else:
            self.xy.abort()

    # --- eventi motore ----------------------------------------------------
    def _on_progress(self, done: int, total: int):
        if not self._running:
            return
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, done * 100 // total))
            if self.kind == "ascii":
                self.lbl.setText(f"{done} / {total} byte")
        else:
            self.bar.setRange(0, 0)
            self.lbl.setText(f"{done} byte ricevuti")

    def _on_file(self, name: str, data: bytes):
        if self.kind != "xrecv" or not self._running:
            return
        n = len(self._saved) + 1
        fname = safe_name(self.ed_name.text() or name, f"ricevuto_{n}.bin")
        path = unique_path(self.ed_file.text().strip(), fname)
        try:
            with open(path, "wb") as f:
                f.write(data)
            self._saved.append(f"{os.path.basename(path)} ({len(data)} B)")
        except OSError as ex:
            self._saved.append(f"ERRORE salvataggio {fname}: {ex}")

    def _on_finished(self, ok: bool, msg: str):
        if not self._running:
            return
        self._set_running(False)
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
        if ok:
            self.bar.setValue(100)
        txt = ("Completato. " if ok else "Fallito: ") + msg
        if self._saved:
            txt += "\nSalvati: " + ", ".join(self._saved)
        self.lbl.setText(txt)

    def closeEvent(self, e):
        if self._running:
            r = QMessageBox.question(self, self.windowTitle(), "Trasferimento in corso: annullarlo e chiudere?")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self._cancel()
        pairs = [(self.asc.progress, self._on_progress), (self.asc.finished, self._on_finished)] if self.kind == "ascii" else [
            (self.xy.progress, self._on_progress), (self.xy.status, self.lbl.setText),
            (self.xy.finished, self._on_finished), (self.xy.file_received, self._on_file)]
        for sig, slot in pairs:
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(e)
