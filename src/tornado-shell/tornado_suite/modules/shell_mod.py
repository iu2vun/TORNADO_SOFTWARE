"""Shell di sistema: PowerShell / cmd / WSL / Git Bash (Windows) o bash/zsh (POSIX) in una scheda, con terminale VT completo."""
from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..apps.termwidget.ansi_engine import AnsiEngine
from ..apps.termwidget.terminal_widget import ResizeMode, TerminalWidget
from ..core.icons import icon
from ..core.pty_transport import PtyTransport
from ..core.registry import ModuleSpec, register, settings
from ..core.theme import P


def profiles() -> dict[str, list[str]]:
    p: dict[str, list[str]] = {}
    if sys.platform == "win32":
        for name, exe, extra in (("PowerShell 7", "pwsh.exe", ["-NoLogo"]), ("Windows PowerShell", "powershell.exe", ["-NoLogo"]),
                                 ("Prompt dei comandi", "cmd.exe", []), ("WSL", "wsl.exe", [])):
            w = shutil.which(exe)
            if w:
                p[name] = [w] + extra
        for gb in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if os.path.exists(gb):
                p["Git Bash"] = [gb, "--login", "-i"]
                break
    else:
        for name in ("bash", "zsh", "fish", "sh"):
            w = shutil.which(name)
            if w:
                p[name] = [w]
    return p


class ShellModulo(QWidget):
    title_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.cwd = str(settings().value("shell/cwd", "") or os.path.expanduser("~"))
        self.profs = profiles()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        bar = QWidget(); bh = QHBoxLayout(bar); bh.setContentsMargins(8, 6, 8, 6); bh.setSpacing(6)
        self.cmb = QComboBox(); self.cmb.addItems(list(self.profs) or ["(nessuna shell trovata)"])
        last = str(settings().value("shell/profilo", "") or "")
        if last in self.profs:
            self.cmb.setCurrentText(last)
        self.cmb.activated.connect(lambda _i: self.avvia())
        b1 = QPushButton(icon("folder", size=16), " Cartella…"); b1.clicked.connect(self.scegli_cartella)
        b2 = QPushButton(icon("play", size=16), " Riavvia"); b2.clicked.connect(lambda: self.avvia())
        b3 = QPushButton("A−"); b3.clicked.connect(lambda: self._font(-1))
        b4 = QPushButton("A+"); b4.clicked.connect(lambda: self._font(+1))
        self.lbl = QLabel(self.cwd); self.lbl.setObjectName("Dim")
        for w in (self.cmb, b1, b2, b3, b4):
            bh.addWidget(w)
        bh.addWidget(self.lbl, 1)
        v.addWidget(bar)
        line = QFrame(); line.setFixedHeight(2); line.setStyleSheet(f"background:{P.accent};border:none")   # filo arancione sotto la barra
        v.addWidget(line)
        self.engine = AnsiEngine(cols=120, rows=30)
        fam = "Cascadia Mono" if sys.platform == "win32" else None
        self.term = TerminalWidget(self.engine, font_family=fam, font_size=int(settings().value("shell/font", 11)))
        self.term._resize_mode = ResizeMode.BUFFER
        self.pty = PtyTransport()
        self.pty.dataReceived.connect(self.engine.feed)
        self.pty.exited.connect(self._exited)
        self.term.dataToSend.connect(self.pty.write)
        self.engine.bufferSizeChanged.connect(lambda c, r: self.pty.resize(c, r))
        self.engine.titleChanged.connect(lambda t: self.title_changed.emit(t[:28] or self.cmb.currentText()))
        v.addWidget(self.term, 1)
        QTimer.singleShot(100, self.avvia)

    def _font(self, d: int) -> None:
        self.term.font_size = max(7, min(32, self.term.font_size + d))
        settings().setValue("shell/font", self.term.font_size)

    def scegli_cartella(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Cartella di lavoro", self.cwd)
        if d:
            self.cwd = d
            self.avvia()

    def avvia(self) -> None:
        name = self.cmb.currentText()
        argv = self.profs.get(name)
        self.engine.reset()
        self.lbl.setText(self.cwd)
        if not argv:
            self.engine.feed(b"Nessuna shell trovata su questo sistema.\r\n")
            return
        settings().setValue("shell/profilo", name); settings().setValue("shell/cwd", self.cwd)
        self.title_changed.emit(name)
        try:
            self.pty.start(argv, self.cwd, cols=self.engine.cols, rows=self.engine.rows)
        except Exception as e:  # noqa: BLE001
            self.engine.feed(f"\x1b[31m{e}\x1b[0m\r\n".encode())
        self.term.setFocus()

    def _exited(self, code: int) -> None:
        w = self.window()
        if hasattr(w, "_widgets"):                      # dentro la suite: chiude la scheda
            if self in w._widgets:
                QTimer.singleShot(0, lambda: w.close_tab(w._widgets.index(self)) if self in w._widgets else None)
        elif w is not self:                             # programma indipendente: chiude la finestra
            QTimer.singleShot(0, w.close)

    def on_activated(self) -> None:
        self.term.setFocus()

    def can_close(self) -> bool:
        self.pty.stop()
        return True

    def contesto_assistente(self) -> str:
        return ""


register(ModuleSpec("shell", "Shell di sistema", "PowerShell, cmd, WSL, Git Bash (o bash/zsh) in una scheda", "terminale",
                    "Sistema", ShellModulo, (), multi=True, ordine=60))
