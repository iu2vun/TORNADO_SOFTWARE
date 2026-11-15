"""
backend.py — gestione automatica del processo LibreVNA-GUI.

Cerca l'eseguibile, lo avvia nascosto (senza finestra visibile su Windows),
verifica che il server SCPI sia pronto e lo termina alla chiusura.
"""

import os
import sys
import time
import socket
import subprocess
import pathlib
from typing import Optional

# Percorsi di ricerca predefiniti (Windows)
_DEFAULT_SEARCH = [
    r"C:\Program Files\LibreVNA\LibreVNA-GUI.exe",
    r"C:\Program Files (x86)\LibreVNA\LibreVNA-GUI.exe",
]


def find_executable(extra_paths: list[str] | None = None) -> Optional[str]:
    """Cerca LibreVNA-GUI.exe nei percorsi standard + extra_paths."""
    candidates = list(extra_paths or []) + _DEFAULT_SEARCH

    # cerca anche nella stessa cartella dello script e nei PATH
    script_dir = pathlib.Path(sys.argv[0]).parent
    for name in ("LibreVNA-GUI.exe", "LibreVNA-GUI"):
        p = script_dir / name
        if p.exists():
            candidates.insert(0, str(p))

    for path in candidates:
        if path and pathlib.Path(path).exists():
            return str(path)
    return None


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


class BackendProcess:
    """Gestisce il processo LibreVNA-GUI come backend SCPI.

    Uso tipico:
        backend = BackendProcess()
        ok, msg = backend.ensure_running(host, port)
        # ... usa l'app ...
        backend.stop()
    """

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._we_started = False          # True se siamo stati noi ad avviarlo

    def ensure_running(
        self,
        host: str = "127.0.0.1",
        port: int = 19542,
        exe_path: str | None = None,
        timeout: float = 15.0,
    ) -> tuple[bool, str]:
        """Verifica che il server SCPI sia in ascolto; se no, avvia il GUI.

        Returns:
            (ok: bool, message: str)
        """
        # già in ascolto (avviato dall'utente o da run precedente)
        if _port_open(host, port):
            return True, f"Server SCPI già attivo su {host}:{port}"

        # cerca l'eseguibile
        exe = exe_path or find_executable()
        if not exe:
            return False, (
                "LibreVNA-GUI.exe non trovato.\n"
                "Installa LibreVNA o specifica il percorso con --backend-exe."
            )

        # avvia il processo nascosto (Windows: niente finestra console/GUI)
        try:
            kwargs: dict = dict(
                args=[exe],
                # su Windows non crea la finestra
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if sys.platform == "win32" else 0),
            )
            # su Windows il GUI ha una finestra Qt — la nascondiamo via
            # variabile d'ambiente che il GUI legge (nessun supporto ufficiale)
            # ma il server SCPI parte comunque anche con la finestra visibile.
            # Per nascondere la finestra usiamo STARTUPINFO.
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 6   # SW_MINIMIZE
                kwargs["startupinfo"] = si

            self._proc = subprocess.Popen(**kwargs)
            self._we_started = True
        except Exception as e:
            return False, f"Impossibile avviare {exe}: {e}"

        # attendi che il server SCPI sia pronto
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _port_open(host, port):
                return True, f"LibreVNA-GUI avviato (PID {self._proc.pid})"
            time.sleep(0.4)

        # timeout: il processo c'è ma il server non ha risposto
        return False, (
            f"LibreVNA-GUI avviato (PID {self._proc.pid}) ma il server SCPI\n"
            f"non ha risposto entro {timeout:.0f}s.\n"
            "Verifica: Edit → Preferences → SCPI Server → Enable server."
        )

    def stop(self, graceful_timeout: float = 3.0):
        """Termina il processo solo se siamo stati noi ad avviarlo."""
        if self._proc and self._we_started:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=graceful_timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except Exception:
                pass
            finally:
                self._proc = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
