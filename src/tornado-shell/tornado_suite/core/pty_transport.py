"""Trasporto pseudo-terminale: Windows via pywinpty (ConPTY), POSIX via pty. Emette byte letti dal processo."""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal


class PtyTransport(QObject):
    dataReceived = Signal(bytes)
    exited = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._proc = None
        self._fd: Optional[int] = None
        self._popen = None
        self._thread: Optional[threading.Thread] = None
        self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive

    # ------------------------------------------------------------------ avvio
    def start(self, argv: list[str], cwd: str, env: Optional[dict] = None, cols: int = 100, rows: int = 30) -> None:
        self.stop()
        e = dict(os.environ)
        e.update(env or {})
        e.setdefault("TERM", "xterm-256color")
        if sys.platform == "win32":
            try:
                from winpty import PtyProcess
            except ImportError as ex:
                raise RuntimeError(f"Manca pywinpty ({ex}).\r\nInstallalo nello STESSO Python che esegue la suite:\r\n    \"{sys.executable}\" -m pip install pywinpty") from ex
            self._proc = PtyProcess.spawn(argv, cwd=cwd, env=e, dimensions=(rows, cols))
            self._alive = True
            self._thread = threading.Thread(target=self._read_win, daemon=True)
        else:
            import fcntl
            import pty
            import struct
            import subprocess
            import termios
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            self._popen = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=e, start_new_session=True, close_fds=True)
            os.close(slave)
            self._fd = master
            self._alive = True
            self._thread = threading.Thread(target=self._read_posix, daemon=True)
        self._thread.start()

    def _read_win(self) -> None:
        p = self._proc
        try:
            while self._alive and p.isalive():
                s = p.read(65536)
                if s:
                    self.dataReceived.emit(s.encode("utf-8", "replace") if isinstance(s, str) else s)
        except EOFError:
            pass
        except Exception:  # noqa: BLE001
            pass
        self._alive = False
        try:
            code = p.exitstatus or 0
        except Exception:  # noqa: BLE001
            code = 0
        self.exited.emit(int(code))

    def _read_posix(self) -> None:
        try:
            while self._alive:
                b = os.read(self._fd, 65536)
                if not b:
                    break
                self.dataReceived.emit(b)
        except OSError:
            pass
        self._alive = False
        code = 0
        try:
            code = self._popen.wait(timeout=1) if self._popen else 0
        except Exception:  # noqa: BLE001
            pass
        self.exited.emit(int(code or 0))

    # --------------------------------------------------------------------- IO
    def write(self, data: bytes) -> None:
        if not self._alive:
            return
        try:
            if self._proc is not None:
                self._proc.write(data.decode("utf-8", "replace"))
            elif self._fd is not None:
                os.write(self._fd, data)
        except Exception:  # noqa: BLE001
            pass

    def resize(self, cols: int, rows: int) -> None:
        if not self._alive:
            return
        try:
            if self._proc is not None:
                self._proc.setwinsize(rows, cols)
            elif self._fd is not None:
                import fcntl
                import struct
                import termios
                fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._alive = False
        try:
            if self._proc is not None:
                self._proc.terminate(force=True)
            if self._popen is not None:
                self._popen.terminate()
            if self._fd is not None:
                os.close(self._fd)
        except Exception:  # noqa: BLE001
            pass
        self._proc = self._popen = None
        self._fd = None
