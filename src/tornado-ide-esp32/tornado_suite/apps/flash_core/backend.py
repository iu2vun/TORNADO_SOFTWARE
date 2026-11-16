"""Interfaccia dei backend (plugin) e utilità per lanciare tool esterni con output in streaming."""
from __future__ import annotations

import os
import subprocess
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Sequence

from .models import Cancelled, DeviceInfo, FlashError, RunContext, WriteRequest


class Backend(ABC):
    id: str = ""
    name: str = ""

    @abstractmethod
    def probe(self, port: str, ctx: RunContext) -> DeviceInfo | None:
        """Identifica il dispositivo sulla porta; None se non è di questo backend (non solleva per 'non trovato')."""

    @abstractmethod
    def write(self, req: WriteRequest, ctx: RunContext) -> None: ...

    @abstractmethod
    def read(self, port: str, dest: Path, ctx: RunContext, baud: int = 0, chip: str = "auto") -> None:
        """Backup completo della flash in ``dest``."""

    @abstractmethod
    def erase(self, port: str, ctx: RunContext, baud: int = 0, chip: str = "auto") -> None: ...


def run_streaming(argv: Sequence[str], ctx: RunContext, on_line: Callable[[str], None] | None = None,
                  tail_lines: int = 12) -> list[str]:
    """Esegue ``argv`` inoltrando ogni riga a ``ctx.log``/``on_line``.

    Il testo è in modalità universal-newlines, quindi anche gli avanzamenti con ``\\r`` diventano righe.
    Solleva Cancelled se ``ctx.cancel`` scatta (processo terminato) e FlashError se il codice di uscita != 0.
    Ritorna tutte le righe emesse.
    """
    ctx.check_cancel()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
    except OSError as e:
        raise FlashError(f"Impossibile avviare {argv[0]}: {e}") from e

    done = threading.Event()

    def watch() -> None:
        while not done.is_set():
            if ctx.cancel.wait(0.1):
                if proc.poll() is None:
                    proc.kill()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            lines.append(line)
            ctx.log(line)
            if on_line:
                on_line(line)
        rc = proc.wait()
    finally:
        done.set()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if ctx.cancel.is_set():
        raise Cancelled("Operazione annullata")
    if rc != 0:
        tail = "\n".join(lines[-tail_lines:])
        raise FlashError(f"{os.path.basename(str(argv[0]))} è terminato con codice {rc}\n{tail}")
    return lines
