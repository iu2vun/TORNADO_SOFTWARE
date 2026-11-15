"""Macro per Tornado-Term: sintassi, esecuzione temporizzata, archivio ed editor.

Sintassi del testo di una macro:
    \\r \\n \\t \\e(ESC) \\b \\0 \\\\    caratteri di controllo
    \\xHH                              byte esadecimale (es. \\x1b)
    {enter}                           sequenza di Invio configurata nel terminale
    {delay N} / {wait N}              pausa di N millisecondi
Gli a-capo scritti nell'editor sono ignorati (servono solo a rendere leggibile la macro).
"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

PORTABLE = QKeySequence.SequenceFormat.PortableText

SYNTAX_HELP = (
    "Sintassi: \\r \\n \\t \\e (ESC) \\b \\0 \\\\ \\xHH (byte esadecimale), {enter} (Invio del terminale), "
    "{delay N} (pausa in ms). Gli a-capo nell'editor sono ignorati.\n"
    "Esempio:  F3400\\r{delay 500}S\\r"
)

_ESC = {"r": b"\r", "n": b"\n", "t": b"\t", "e": b"\x1b", "b": b"\x08", "0": b"\x00", "\\": b"\\"}
_DELAY = re.compile(r"\{(?:delay|wait)\s+(\d+)\s*\}", re.IGNORECASE)
_ENTER = re.compile(r"\{enter\}", re.IGNORECASE)
_HEX2 = re.compile(r"[0-9a-fA-F]{2}")


@dataclass
class Macro:
    name: str = "Nuova macro"
    text: str = ""
    hotkey: str = ""      # formato portabile, es. "Ctrl+F1"


def parse_macro(text: str, enter: bytes = b"\r") -> list[tuple[str, object]]:
    """Ritorna una lista di passi: ("send", bytes) oppure ("delay", ms)."""
    steps: list[tuple[str, object]] = []
    buf = bytearray()

    def flush():
        if buf:
            steps.append(("send", bytes(buf)))
            buf.clear()

    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\r\n":
            i += 1
        elif c == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "x" and _HEX2.fullmatch(text[i + 2:i + 4]):
                buf.append(int(text[i + 2:i + 4], 16))
                i += 4
            elif nxt in _ESC:
                buf += _ESC[nxt]
                i += 2
            else:
                buf += b"\\"
                i += 1
        elif c == "{":
            m = _DELAY.match(text, i)
            if m:
                flush()
                steps.append(("delay", int(m.group(1))))
                i = m.end()
                continue
            m = _ENTER.match(text, i)
            if m:
                buf += enter
                i = m.end()
                continue
            buf += b"{"
            i += 1
        else:
            buf += c.encode("utf-8", "replace")
            i += 1
    flush()
    return steps


class MacroRunner(QObject):
    """Esegue i passi di una macro: invii immediati e pause."""

    sent = Signal(bytes)
    finished = Signal(bool)    # True se completata, False se annullata

    def __init__(self, write: Callable[[bytes], object], enter: Callable[[], bytes] = lambda: b"\r", parent=None):
        super().__init__(parent)
        self._write = write
        self._enter = enter
        self._steps: deque = deque()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next)
        self.busy = False

    def run(self, text: str) -> bool:
        if self.busy:
            return False
        steps = parse_macro(text, self._enter())
        if not steps:
            return False
        self._steps = deque(steps)
        self.busy = True
        self._next()
        return True

    def cancel(self):
        if self.busy:
            self._timer.stop()
            self._steps.clear()
            self.busy = False
            self.finished.emit(False)

    def _next(self):
        while self._steps:
            kind, val = self._steps.popleft()
            if kind == "send":
                self._write(val)
                self.sent.emit(val)
            else:
                self._timer.start(max(1, int(val)))
                return
        self.busy = False
        self.finished.emit(True)


def load_macros(settings) -> list[Macro]:
    raw = settings.value("macros", "", str)
    try:
        return [Macro(**{k: str(v) for k, v in d.items() if k in ("name", "text", "hotkey")})
                for d in json.loads(raw)] if raw else []
    except (ValueError, TypeError, AttributeError):
        return []


def save_macros(settings, macros: list[Macro]):
    settings.setValue("macros", json.dumps([asdict(m) for m in macros], ensure_ascii=False))


class MacroEditor(QDialog):
    def __init__(self, macros: list[Macro], test_cb: Callable[[str], object] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Macro")
        self.resize(760, 460)
        self._macros = [Macro(**asdict(m)) for m in macros]
        self._row = -1
        self._test_cb = test_cb

        self.lst = QListWidget()
        self.lst.setMaximumWidth(220)
        self.ed_name = QLineEdit()
        self.ed_key = QKeySequenceEdit()
        self.ed_key.setMaximumSequenceLength(1)
        self.btn_nokey = QPushButton("Nessuno")
        self.ed_text = QPlainTextEdit()
        self.ed_text.setPlaceholderText("Testo della macro, es.  F3400\\r{delay 500}S\\r")
        help_lbl = QLabel(SYNTAX_HELP)
        help_lbl.setWordWrap(True)

        btn_new, btn_del = QPushButton("Nuova"), QPushButton("Elimina")
        btn_up, btn_dn = QPushButton("Su"), QPushButton("Giu'")
        self.btn_test = QPushButton("Prova (invia ora)")
        self.btn_test.setEnabled(test_cb is not None)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        left = QVBoxLayout()
        left.addWidget(self.lst)
        r1 = QHBoxLayout(); r1.addWidget(btn_new); r1.addWidget(btn_del)
        r2 = QHBoxLayout(); r2.addWidget(btn_up); r2.addWidget(btn_dn)
        left.addLayout(r1); left.addLayout(r2)
        form = QFormLayout()
        form.addRow("Nome", self.ed_name)
        krow = QHBoxLayout(); krow.addWidget(self.ed_key, 1); krow.addWidget(self.btn_nokey)
        form.addRow("Tasto rapido", krow)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(QLabel("Testo"))
        right.addWidget(self.ed_text, 1)
        right.addWidget(help_lbl)
        right.addWidget(self.btn_test)
        top = QHBoxLayout(); top.addLayout(left); top.addLayout(right, 1)
        root = QVBoxLayout(self); root.addLayout(top); root.addWidget(bb)

        for m in self._macros:
            self.lst.addItem(m.name)
        self.lst.currentRowChanged.connect(self._on_row)
        self.ed_name.textEdited.connect(self._name_edited)
        self.btn_nokey.clicked.connect(self.ed_key.clear)
        btn_new.clicked.connect(self._new)
        btn_del.clicked.connect(self._delete)
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_dn.clicked.connect(lambda: self._move(1))
        self.btn_test.clicked.connect(lambda: self._test_cb(self.ed_text.toPlainText()))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        if self._macros:
            self.lst.setCurrentRow(0)
        else:
            self._set_enabled(False)

    def _set_enabled(self, on: bool):
        for w in (self.ed_name, self.ed_key, self.btn_nokey, self.ed_text):
            w.setEnabled(on)
        self.btn_test.setEnabled(on and self._test_cb is not None)

    def _commit(self):
        if 0 <= self._row < len(self._macros):
            m = self._macros[self._row]
            m.name = self.ed_name.text().strip() or "Macro"
            m.text = self.ed_text.toPlainText()
            m.hotkey = self.ed_key.keySequence().toString(PORTABLE)

    def _on_row(self, row: int):
        self._commit()
        self._row = row
        self._set_enabled(row >= 0)
        if row >= 0:
            m = self._macros[row]
            self.ed_name.setText(m.name)
            self.ed_text.setPlainText(m.text)
            self.ed_key.setKeySequence(QKeySequence(m.hotkey))
        else:
            self.ed_name.clear(); self.ed_text.clear(); self.ed_key.clear()

    def _name_edited(self, t: str):
        if self._row >= 0:
            self.lst.item(self._row).setText(t or "Macro")

    def _new(self):
        self._commit()
        self._macros.append(Macro(name=f"Macro {len(self._macros) + 1}"))
        self.lst.addItem(self._macros[-1].name)
        self.lst.setCurrentRow(len(self._macros) - 1)
        self.ed_name.setFocus()
        self.ed_name.selectAll()

    def _delete(self):
        r = self._row
        if r < 0:
            return
        self._row = -1
        self._macros.pop(r)
        self.lst.blockSignals(True)
        self.lst.takeItem(r)
        self.lst.blockSignals(False)
        if self._macros:
            self.lst.setCurrentRow(min(r, len(self._macros) - 1))
            self._row = -1
            self._on_row(self.lst.currentRow())
        else:
            self._on_row(-1)

    def _move(self, d: int):
        self._commit()
        r, t = self._row, self._row + d
        if r < 0 or not 0 <= t < len(self._macros):
            return
        self._macros[r], self._macros[t] = self._macros[t], self._macros[r]
        a, b = self.lst.item(r).text(), self.lst.item(t).text()
        self.lst.item(r).setText(b)
        self.lst.item(t).setText(a)
        self._row = t
        self.lst.blockSignals(True)
        self.lst.setCurrentRow(t)
        self.lst.blockSignals(False)

    def accept(self):
        self._commit()
        keys = [m.hotkey for m in self._macros if m.hotkey]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:
            QMessageBox.warning(self, "Tasti rapidi duplicati",
                                "Lo stesso tasto e' assegnato a piu' macro: " + ", ".join(dup))
            return
        super().accept()

    def result_macros(self) -> list[Macro]:
        return self._macros
