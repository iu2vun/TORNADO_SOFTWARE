"""Log di sessione con ora (testo o esadecimale) e finestra di vista esadecimale in tempo reale."""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ...core.theme import MONO_FAMILY, P

_ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>MDEHc78]")


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def hexdump_lines(data: bytes, width: int = 16) -> list[str]:
    out = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hx = " ".join(f"{b:02X}" for b in chunk).ljust(width * 3 - 1)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"{off:06X}  {hx}  |{asc}|")
    return out


class SessionLog(QObject):
    """Scrive RX/TX su file. Modo testo: una riga per riga ricevuta/inviata; modo hex: un dump per blocco."""

    PARTIAL_MS = 600            # una riga senza a capo (es. il prompt) viene scritta dopo questa pausa

    def __init__(self, parent=None):
        super().__init__(parent)
        self._f = None
        self.path = ""
        self.hex = False
        self.raw = False
        self._buf = {"RX": b"", "TX": b""}
        self._t0 = {"RX": "", "TX": ""}
        self._last: Optional[str] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.PARTIAL_MS)
        self._timer.timeout.connect(self.flush)

    @property
    def active(self) -> bool:
        return self._f is not None

    def start(self, path: str, hex_mode: bool = False, raw: bool = False) -> None:
        self.stop()
        self._f = open(path, "a", encoding="utf-8", buffering=1)
        self.path, self.hex, self.raw = path, hex_mode, raw
        self._f.write(f"# log avviato {stamp()} ({'hex' if hex_mode else 'testo'})\n")

    def stop(self) -> None:
        if self._f:
            self.flush()
            self._f.write(f"# log chiuso {stamp()}\n")
            self._f.close()
        self._f = None

    def note(self, text: str) -> None:
        if self._f:
            self.flush()
            self._f.write(f"[{stamp()}] -- {text}\n")

    def rx(self, data: bytes) -> None:
        self._add("RX", data)

    def tx(self, data: bytes) -> None:
        self._add("TX", data)

    def _add(self, d: str, data: bytes) -> None:
        if not self._f or not data:
            return
        if self.hex:
            ts = stamp()
            for ln in hexdump_lines(data):
                self._f.write(f"[{ts}] {d} {ln}\n")
            return
        if self._last and self._last != d:
            self._flush_dir(self._last)
        self._last = d
        if not self._buf[d]:
            self._t0[d] = stamp()
        self._buf[d] += data
        while True:
            i = self._buf[d].find(b"\n")
            if i < 0:
                break
            line, self._buf[d] = self._buf[d][:i], self._buf[d][i + 1:]
            self._write_line(d, line)
            self._t0[d] = stamp()
        self._timer.start()

    def _write_line(self, d: str, line: bytes) -> None:
        if not self.raw:
            line = _ANSI.sub(b"", line).replace(b"\r", b"")
        txt = line.decode("utf-8", "replace") if not self.raw else line.decode("latin-1")
        if txt or self.raw:
            self._f.write(f"[{self._t0[d] or stamp()}] {d}: {txt}\n")

    def _flush_dir(self, d: str) -> None:
        if self._buf[d]:
            self._write_line(d, self._buf[d])
            self._buf[d] = b""

    def flush(self) -> None:
        if self._f:
            for d in ("RX", "TX"):
                self._flush_dir(d)


class HexWindow(QDialog):
    """Vista esadecimale live di tutto il traffico (RX in verde, TX in arancione)."""

    MAX_LINES = 20000

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Esadecimale — {title}")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(760, 480)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(self.MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        f = QFont(); f.setFamilies([s.strip('"') for s in MONO_FAMILY.split(",")])
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.view.setFont(f)
        self.chk_tx = QCheckBox("Mostra TX"); self.chk_tx.setChecked(True)
        self.btn_pause = QPushButton("Pausa"); self.btn_pause.setCheckable(True)
        btn_clear = QPushButton("Pulisci")
        btn_save = QPushButton("Salva…")
        self.lbl = QLabel("")
        row = QHBoxLayout()
        for w in (self.chk_tx, self.btn_pause, btn_clear, btn_save):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.lbl)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.view)
        btn_clear.clicked.connect(self.view.clear)
        btn_save.clicked.connect(self._save)
        self._n = {"RX": 0, "TX": 0}

    def feed(self, d: str, data: bytes) -> None:
        if self.btn_pause.isChecked() or (d == "TX" and not self.chk_tx.isChecked()) or not data:
            return
        self._n[d] += len(data)
        col = P.ok if d == "RX" else P.accent
        ts = stamp()[11:]
        for ln in hexdump_lines(data):
            self.view.appendHtml(f'<span style="color:{P.text_dim}">{ts}</span> '
                                 f'<span style="color:{col}">{d}</span> <span style="white-space:pre">{html.escape(ln)}</span>')
        self.lbl.setText(f"RX {self._n['RX']}  TX {self._n['TX']} byte")

    def _save(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Salva dump", "dump.txt", "Testo (*.txt)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.view.toPlainText())
