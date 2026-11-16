"""Pannello «Flash» dell'IDE Embedded: scrive file già pronti (.bin/.hex), backup, cancellazione e fuse AVR.

Usa ``flash_core`` (nessuna logica di flash qui). Non sostituisce «Carica» (che passa da PlatformIO): serve per i binari
pronti, il backup della flash e i fuse. La porta è quella scelta nella barra dell'IDE; il monitor seriale viene staccato
prima di ogni operazione e riattaccato dopo, tramite i due hook ricevuti dall'IDE.
"""
from __future__ import annotations

import datetime
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ...core.theme import P
from ...modules.editor import mono_font
from ...tornado_diag import get_logger
from ..flash_core import (AvrdudeBackend, Backend, Cancelled, DeviceInfo, EsptoolBackend, FlashError, ImagePart, PROGRAMMERS,
                          RunContext, WriteRequest, candidates)
from ..flash_core.detect import list_ports as fc_list_ports

log = get_logger("espide.flash")

TARGETS = (                                   # (etichetta, id)
    ("Automatico (dalla porta)", "auto"),
    ("ESP32 / ESP8266 (esptool)", "esptool"),
    ("Arduino Uno/Nano – bootloader", "avr:arduino"),
    ("AVR – Arduino come ISP", "avr:arduinoisp"),
    ("AVR – USBasp", "avr:usbasp"),
    ("AVR – Atmel AVRISP mkII", "avr:avrispmkii"),
)
AVR_PARTS = ("m328p", "m162", "m328", "m168", "m88", "m8", "m16", "m32", "m2560", "m32u4")
FUSES = ("lfuse", "hfuse", "efuse", "lock")


def default_factory(kind: str, programmer: str, part: str) -> Backend:
    return EsptoolBackend() if kind == "esptool" else AvrdudeBackend(programmer, part)


class _Job(QThread):
    """Esegue ``fn(ctx)`` fuori dal thread GUI inoltrando log e avanzamento."""
    log = Signal(str)
    progress = Signal(int)
    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[RunContext], object], parent=None) -> None:
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()

    def run(self) -> None:
        ctx = RunContext(log=self.log.emit, progress=self.progress.emit, cancel=self.cancel)
        try:
            res = self.fn(ctx)
        except Cancelled:
            self.failed.emit("Annullato")
        except FlashError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("errore inatteso nel flash")
            self.failed.emit(f"Errore inatteso: {e!r}")
        else:
            self.ok.emit(res)


class FlashPanel(QWidget):
    def __init__(self, settings, port_getter: Callable[[], str], before: Callable[[], None] = lambda: None,
                 after: Callable[[], None] = lambda: None, busy: Callable[[], bool] = lambda: False,
                 backend_factory: Callable[[str, str, str], Backend] = default_factory, prefix: str = "espide/flash/") -> None:
        super().__init__()
        self.s, self._port, self._before, self._after, self._busy = settings, port_getter, before, after, busy
        self._factory, self._pfx = backend_factory, prefix
        self._job: Optional[_Job] = None
        self._on_ok: Optional[Callable[[object], None]] = None
        self._fuse_ref: dict[str, int] = {}

        self.cmb_target = QComboBox()
        for label, tid in TARGETS:
            self.cmb_target.addItem(label, tid)
        self.cmb_target.setCurrentIndex(max(0, self.cmb_target.findData(str(self.s.value(prefix + "target", "auto") or "auto"))))
        self.cmb_part = QComboBox()
        self.cmb_part.setEditable(True)
        self.cmb_part.addItems(AVR_PARTS)
        self.cmb_part.setCurrentText(str(self.s.value(prefix + "part", "m328p") or "m328p"))
        self.cmb_part.setToolTip("Id del chip per avrdude (es. m328p = ATmega328P, m162 = ATmega162)")
        self.lbl_part = QLabel("Chip AVR")

        self.files = QListWidget()
        self.files.setToolTip("File da scrivere con il loro offset. Doppio clic per cambiare l'offset (esptool).")
        self.files.itemDoubleClicked.connect(self._edit_offset)
        self.btn_add = QPushButton("Aggiungi file…")
        self.btn_add.clicked.connect(self._add_file)
        self.btn_del = QPushButton("Rimuovi")
        self.btn_del.clicked.connect(lambda: [self.files.takeItem(self.files.row(i)) for i in self.files.selectedItems()])

        self.chk_verify = QCheckBox("Verifica dopo la scrittura")
        self.chk_verify.setChecked(True)
        self.chk_erase = QCheckBox("Cancella tutto prima")

        self.btn_probe = QPushButton("Rileva chip")
        self.btn_probe.clicked.connect(self.probe)
        self.btn_write = QPushButton("Scrivi")
        self.btn_write.setProperty("primary", True)
        self.btn_write.clicked.connect(self.write)
        self.btn_read = QPushButton("Backup flash…")
        self.btn_read.clicked.connect(self.backup)
        self.btn_erase = QPushButton("Cancella chip")
        self.btn_erase.clicked.connect(self.erase)
        self.btn_cancel = QPushButton("Annulla")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.setEnabled(False)

        self.fuse_box = QGroupBox("Fuse (solo programmatore ISP)")
        fl = QGridLayout(self.fuse_box)
        self.fuse_edits: dict[str, QLineEdit] = {}
        for i, n in enumerate(FUSES):
            fl.addWidget(QLabel(n), 0, i, Qt.AlignmentFlag.AlignHCenter)
            e = QLineEdit()
            e.setMaximumWidth(64)
            e.setPlaceholderText("0x--")
            self.fuse_edits[n] = e
            fl.addWidget(e, 1, i)
        self.btn_fread = QPushButton("Leggi fuse")
        self.btn_fread.clicked.connect(self.read_fuses)
        self.btn_fwrite = QPushButton("Scrivi modificati")
        self.btn_fwrite.clicked.connect(self.write_fuses)
        self.chk_force = QCheckBox("Forza (pericoloso)")
        self.chk_force.setToolTip("Consente di disattivare l'ISP (SPIEN) o scrivere i lock bit: può rendere il chip non più programmabile.")
        fl.addWidget(self.btn_fread, 2, 0, 1, 2)
        fl.addWidget(self.btn_fwrite, 2, 2, 1, 2)
        fl.addWidget(self.chk_force, 3, 0, 1, 4)

        left = QWidget()
        g = QGridLayout(left)
        g.setContentsMargins(8, 8, 8, 8)
        g.addWidget(QLabel("Target"), 0, 0)
        g.addWidget(self.cmb_target, 0, 1, 1, 2)
        g.addWidget(self.lbl_part, 1, 0)
        g.addWidget(self.cmb_part, 1, 1, 1, 2)
        g.addWidget(self.files, 2, 0, 3, 2)
        g.addWidget(self.btn_add, 2, 2)
        g.addWidget(self.btn_del, 3, 2)
        g.addWidget(self.chk_verify, 5, 0, 1, 2)
        g.addWidget(self.chk_erase, 5, 2)
        row = QHBoxLayout()
        for b in (self.btn_probe, self.btn_write, self.btn_read, self.btn_erase, self.btn_cancel):
            row.addWidget(b)
        g.addLayout(row, 6, 0, 1, 3)
        g.addWidget(self.fuse_box, 7, 0, 1, 3)
        g.setRowStretch(4, 1)

        self.lbl_info = QLabel("Nessun chip rilevato")
        self.lbl_info.setStyleSheet(f"color:{P.accent};font-weight:600;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(5000)
        self.logw.setFont(mono_font(10))
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 8, 8, 8)
        rv.addWidget(self.lbl_info)
        rv.addWidget(self.bar)
        rv.addWidget(self.logw, 1)

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.addWidget(left)
        sp.addWidget(right)
        sp.setSizes([560, 560])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sp)

        self.cmb_target.currentIndexChanged.connect(self._target_changed)
        self._target_changed()

    # ------------------------------------------------------------------ stato UI
    def _tid(self) -> str:
        return str(self.cmb_target.currentData())

    def _target_changed(self, *_: object) -> None:
        tid = self._tid()
        avr = tid.startswith("avr:")
        self.lbl_part.setVisible(avr or tid == "auto")
        self.cmb_part.setVisible(avr or tid == "auto")
        self.fuse_box.setVisible(avr and tid != "avr:arduino")
        self.s.setValue(self._pfx + "target", tid)

    def _set_busy(self, busy: bool) -> None:
        for b in (self.btn_probe, self.btn_write, self.btn_read, self.btn_erase, self.btn_add, self.btn_del,
                  self.btn_fread, self.btn_fwrite, self.cmb_target, self.cmb_part):
            b.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)

    def _append(self, text: str) -> None:
        self.logw.appendPlainText(text)

    # ------------------------------------------------------------------ file
    def _add_file(self) -> None:
        last = str(self.s.value(self._pfx + "dir", "") or "")
        paths, _ = QFileDialog.getOpenFileNames(self, "File da scrivere", last, "Firmware (*.bin *.hex *.elf);;Tutti i file (*)")
        if not paths:
            return
        self.s.setValue(self._pfx + "dir", os.path.dirname(paths[0]))
        multi = self._tid() in ("esptool", "auto")
        if not multi:
            self.files.clear()
            paths = paths[:1]
        for p in paths:
            off = 0
            if multi:
                got = self._ask_offset(os.path.basename(p), "0x10000" if p.lower().endswith(".bin") else "0x0")
                if got is None:
                    continue
                off = got
            self._put(off, p)

    def _put(self, off: int, path: str) -> None:
        it = QListWidgetItem(f"0x{off:06x}   {os.path.basename(path)}")
        it.setData(Qt.ItemDataRole.UserRole, (off, path))
        it.setToolTip(path)
        self.files.addItem(it)

    def _ask_offset(self, name: str, default: str) -> Optional[int]:
        text, ok = QInputDialog.getText(self, "Offset", f"Offset di {name} (es. 0x10000; immagine completa 0x0):", text=default)
        if not ok:
            return None
        try:
            v = int(text.strip(), 0)
            if v < 0:
                raise ValueError
            return v
        except ValueError:
            QMessageBox.warning(self, "Offset", f"Offset non valido: {text}")
            return None

    def _edit_offset(self, it: QListWidgetItem) -> None:
        off, path = it.data(Qt.ItemDataRole.UserRole)
        new = self._ask_offset(os.path.basename(path), f"0x{off:x}")
        if new is not None:
            it.setData(Qt.ItemDataRole.UserRole, (new, path))
            it.setText(f"0x{new:06x}   {os.path.basename(path)}")

    def parts(self) -> list[ImagePart]:
        out = []
        for i in range(self.files.count()):
            off, path = self.files.item(i).data(Qt.ItemDataRole.UserRole)
            out.append(ImagePart(off, Path(path)))
        return out

    # ------------------------------------------------------------------ selezione backend / porta
    def _resolve(self) -> Optional[tuple[Backend, str, str]]:
        """(backend, porta, chip) oppure None dopo aver avvisato l'utente."""
        tid, port = self._tid(), self._port()
        if tid == "auto":
            tid = "esptool"
            for p in fc_list_ports():
                if p.device == port:
                    c = candidates(p)
                    if c and c[0] == "avrdude":
                        tid = "avr:arduino"          # Uno/cloni con bootloader; per ISP scegliere il target a mano
                    break
        avr = tid.startswith("avr:")
        prog = tid.split(":", 1)[1] if avr else ""
        part = self.cmb_part.currentText().strip()
        serial = tid == "esptool" or PROGRAMMERS[prog].serial
        if serial and not port:
            QMessageBox.information(self, "Flash", "Scegli la porta seriale nel menu della barra dell'IDE (non «Automatica»).")
            return None
        if avr and not part:
            QMessageBox.information(self, "Flash", "Indica il chip AVR (es. m328p, m162).")
            return None
        self.s.setValue(self._pfx + "part", part)
        return self._factory("avrdude" if avr else "esptool", prog, part), port, (part if avr else "auto")

    # ------------------------------------------------------------------ esecuzione
    def _start(self, title: str, fn: Callable[[RunContext], object], on_ok: Optional[Callable[[object], None]] = None) -> None:
        if self._job is not None:
            return
        if self._busy():
            QMessageBox.information(self, "Flash", "Attendere la fine della compilazione/caricamento in corso.")
            return
        try:
            self._before()
        except Exception:  # noqa: BLE001
            log.exception("hook before")
        self._set_busy(True)
        self.bar.setValue(0)
        self._append(f"▶ {title}")
        self._on_ok = on_ok
        job = _Job(fn, self)
        job.log.connect(lambda s: self._append("  " + s))
        job.progress.connect(self.bar.setValue)
        job.ok.connect(self._finish_ok)
        job.failed.connect(self._finish_fail)
        job.finished.connect(self._cleanup)
        self._job = job
        job.start()

    def _finish_ok(self, result: object) -> None:
        self._append("✔ Completato")
        if self._on_ok:
            try:
                self._on_ok(result)
            except Exception:  # noqa: BLE001
                log.exception("on_ok")

    def _finish_fail(self, msg: str) -> None:
        self._append(f"✘ {msg}")
        if msg != "Annullato":
            QMessageBox.warning(self, "Flash", msg)

    def _cleanup(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.deleteLater()
        self._set_busy(False)
        try:
            self._after()
        except Exception:  # noqa: BLE001
            log.exception("hook after")

    def cancel(self) -> None:
        if self._job is not None:
            self._job.cancel.set()

    def shutdown(self) -> None:
        if self._job is not None:
            self._job.cancel.set()
            self._job.wait(3000)

    # ------------------------------------------------------------------ operazioni
    def probe(self) -> None:
        r = self._resolve()
        if not r:
            return
        be, port, _ = r

        def done(info: object) -> None:
            if isinstance(info, DeviceInfo):
                extra = info.details.get("flash_size") or info.details.get("signature") or ""
                self.lbl_info.setText(f"{info.chip}  {extra}  ({info.port})".replace("  ", " ").strip())
                if info.details.get("part"):
                    self.cmb_part.setCurrentText(info.details["part"])
            else:
                self.lbl_info.setText("Nessuna risposta dal chip")

        self._start(f"Rilevamento su {port}", lambda ctx: be.probe(port, ctx), done)

    def write(self) -> None:
        r = self._resolve()
        if not r:
            return
        be, port, chip = r
        parts = self.parts()
        if not parts:
            QMessageBox.information(self, "Flash", "Aggiungi almeno un file da scrivere.")
            return
        if self.chk_erase.isChecked() and QMessageBox.question(
                self, "Cancella tutto", "Verrà cancellata TUTTA la flash prima della scrittura. Continuare?") != QMessageBox.StandardButton.Yes:
            return
        req = WriteRequest(port, parts, 0, self.chk_erase.isChecked(), self.chk_verify.isChecked(), chip)
        self._start(f"Scrittura di {len(parts)} file su {port}", lambda ctx: be.write(req, ctx))

    def backup(self) -> None:
        r = self._resolve()
        if not r:
            return
        be, port, chip = r
        ext = "hex" if self._tid().startswith("avr:") else "bin"
        last = str(self.s.value(self._pfx + "dir", "") or "")
        name = f"backup_{datetime.datetime.now():%Y%m%d_%H%M}.{ext}"
        dest, _ = QFileDialog.getSaveFileName(self, "Salva backup della flash", os.path.join(last, name), f"Backup (*.{ext});;Tutti i file (*)")
        if not dest:
            return
        self._start(f"Backup di {port}", lambda ctx: be.read(port, Path(dest), ctx, 0, chip),
                    lambda _r: self._append(f"  Salvato in {dest}"))

    def erase(self) -> None:
        r = self._resolve()
        if not r:
            return
        be, port, chip = r
        if QMessageBox.question(self, "Cancella chip", f"Cancellare TUTTA la flash su {port}?") != QMessageBox.StandardButton.Yes:
            return
        self._start(f"Cancellazione di {port}", lambda ctx: be.erase(port, ctx, 0, chip))

    # ------------------------------------------------------------------ fuse
    def _fuse_backend(self):
        r = self._resolve()
        if not r:
            return None
        be, port, chip = r
        if not hasattr(be, "read_fuses"):
            QMessageBox.information(self, "Fuse", "I fuse si gestiscono solo con un programmatore AVR (ISP).")
            return None
        return be, port, chip

    def read_fuses(self) -> None:
        r = self._fuse_backend()
        if not r:
            return
        be, port, chip = r

        self._start(f"Lettura fuse su {port}", lambda ctx: be.read_fuses(port, ctx, chip), self._fuses_read_back)

    def write_fuses(self) -> None:
        r = self._fuse_backend()
        if not r:
            return
        be, port, chip = r
        changes: dict[str, int] = {}
        try:
            for n, e in self.fuse_edits.items():
                t = e.text().strip()
                if t and n in self._fuse_ref and int(t, 0) != self._fuse_ref[n]:
                    changes[n] = int(t, 0)
        except ValueError:
            QMessageBox.warning(self, "Fuse", "Valore esadecimale non valido.")
            return
        if not self._fuse_ref:
            QMessageBox.information(self, "Fuse", "Leggi prima i fuse attuali.")
            return
        if not changes:
            QMessageBox.information(self, "Fuse", "Nessun fuse modificato.")
            return
        txt = ", ".join(f"{n}: 0x{self._fuse_ref[n]:02x} → 0x{v:02x}" for n, v in changes.items())
        if QMessageBox.question(self, "Scrivi fuse", f"Scrivere i fuse?\n{txt}\n\nUn valore errato può bloccare il chip.") != QMessageBox.StandardButton.Yes:
            return
        force = self.chk_force.isChecked()

        def run(ctx: RunContext) -> dict:
            for n, v in changes.items():
                be.write_fuse(port, n, v, ctx, chip, force)
            return be.read_fuses(port, ctx, chip)

        self._start(f"Scrittura fuse su {port}", run, self._fuses_read_back)

    def _fuses_read_back(self, vals: object) -> None:
        self._fuse_ref = dict(vals)  # type: ignore[arg-type]
        for n, v in self._fuse_ref.items():
            self.fuse_edits[n].setText(f"0x{v:02x}")
            self._append(f"  riletto {n:<6} = 0x{v:02x}")
