"""Sessione di debug con ``pio debug --interface=gdb`` (gdb in modalità testo, comandi via stdin).

NON verificata su hardware reale: richiede una sonda JTAG (o l'USB-JTAG integrato di S3/C3/C6) e le opzioni debug_* in platformio.ini.
"""
from __future__ import annotations

import codecs
import re
import sys
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

_STOP = re.compile(r"\bat\s+(?P<f>(?:[A-Za-z]:)?[^\s:][^:\n]*?):(?P<l>\d+)\s*$")
_FRAME = re.compile(r"^#\d+\s+.*\bat\s+(?P<f>(?:[A-Za-z]:)?[^\s:][^:\n]*?):(?P<l>\d+)\s*$")
_SRCLINE = re.compile(r"^(?P<l>\d+)\t")


def parse_stop(line: str) -> Optional[tuple[str, int]]:
    """Posizione di arresto da una riga di gdb: «Breakpoint 1, loop () at src/main.cpp:12» -> ('src/main.cpp', 12)."""
    s = line.rstrip()
    if s.startswith("#") and not s.startswith("#0"):
        return None
    m = _FRAME.match(s) or (_STOP.search(s) if ("breakpoint" in s.lower() or s.startswith("#0") or "Temporary" in s) else None)
    return (m["f"].strip(), int(m["l"])) if m else None


class DebugSession(QObject):
    output = Signal(str)
    stopped = Signal(str, int)
    ready = Signal()
    finished = Signal(int)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.proc: Optional[QProcess] = None
        self._queue: list[str] = []
        self._ready = False
        self._buf = ""
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def start(self, argv: list[str], cwd: str) -> None:
        if self.running:
            return
        self._ready, self._buf = False, ""
        self._dec.reset()
        p = QProcess(self)
        p.setWorkingDirectory(cwd)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUTF8", "1")
        p.setProcessEnvironment(env)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._err)
        self.proc = p
        p.start(argv[0], argv[1:])
        QTimer.singleShot(6000, self._force_ready)         # se gdb non stampa mai un prompt (stdin non tty)

    def send(self, cmd: str) -> None:
        if not self.running:
            return
        if not self._ready:
            self._queue.append(cmd)
            return
        self._write(cmd)

    def _write(self, cmd: str) -> None:
        self.output.emit(f"(gdb) {cmd}")
        self.proc.write((cmd + "\n").encode("utf-8"))      # type: ignore[union-attr]

    def stop(self) -> None:
        if not self.running:
            return
        self.proc.write(b"quit\ny\n")                       # type: ignore[union-attr]
        QTimer.singleShot(1500, self._kill)

    def _kill(self) -> None:
        p = self.proc
        if p is not None and p.state() != QProcess.ProcessState.NotRunning:
            pid = int(p.processId())
            if sys.platform == "win32" and pid:
                QProcess.startDetached("taskkill", ["/PID", str(pid), "/T", "/F"])
            else:
                p.kill()

    def _force_ready(self) -> None:
        if self.running and not self._ready:
            self._mark_ready()

    def _mark_ready(self) -> None:
        self._ready = True
        self.ready.emit()
        q, self._queue = self._queue, []
        for c in q:
            self._write(c)

    def _read(self) -> None:
        if self.proc is None:
            return
        self._buf += self._dec.decode(bytes(self.proc.readAllStandardOutput()))
        *lines, self._buf = self._buf.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for ln in lines:
            self._line(ln)
        if self._buf.rstrip().endswith("(gdb)"):          # prompt senza a capo
            if not self._ready:
                self._mark_ready()

    def _line(self, ln: str) -> None:
        self.output.emit(ln)
        st = parse_stop(ln)
        if st:
            self.stopped.emit(*st)
        if not self._ready and ("Remote debugging using" in ln or ln.startswith("(gdb)")):
            self._mark_ready()

    def _done(self, code: int, _st) -> None:
        p, self.proc = self.proc, None
        if p is not None:
            p.deleteLater()
        self.finished.emit(code)

    def _err(self, e) -> None:
        if e == QProcess.ProcessError.FailedToStart:
            self.output.emit("Impossibile avviare «pio debug».")
            p, self.proc = self.proc, None
            if p is not None:
                p.deleteLater()
            self.finished.emit(-1)
