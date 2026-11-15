"""Installazione automatica di PlatformIO Core in un ambiente virtuale dedicato.

Evita i problemi tipici: Python troppo recente (3.13/3.14) per le dipendenze di PlatformIO, ``Scripts`` fuori dal PATH,
``pip`` non trovato. L'IDE sceglie da solo un Python adatto, crea un venv in una cartella dell'utente e vi installa ``platformio``.
Logica senza Qt: la GUI esegue i passi e mostra l'output.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

PREFERRED = ((3, 12), (3, 11), (3, 10), (3, 13), (3, 9))     # in ordine di preferenza; 3.14 solo se non c'è altro
MIN_OK, MAX_OK = (3, 6), (3, 13)
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def venv_dir() -> Path:
    """Cartella del venv di PlatformIO (per utente, fuori dal progetto)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "TornadoSuite" / "pio-venv"


def venv_python(vdir: Optional[Path] = None) -> Path:
    v = vdir or venv_dir()
    return v / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def venv_pio(vdir: Optional[Path] = None) -> Optional[Path]:
    """pio installato nel venv dedicato, oppure None."""
    v = vdir or venv_dir()
    for rel in (("Scripts", "pio.exe"), ("Scripts", "platformio.exe"), ("bin", "pio"), ("bin", "platformio")):
        p = v.joinpath(*rel)
        if p.is_file():
            return p
    return None


@dataclass(frozen=True)
class PyCand:
    exe: str
    version: tuple[int, int]

    @property
    def label(self) -> str:
        return f"Python {self.version[0]}.{self.version[1]}  ({self.exe})"

    def score(self) -> int:
        """Più basso = meglio. Fuori dall'intervallo supportato: in fondo."""
        if MIN_OK <= self.version <= MAX_OK:
            return PREFERRED.index(self.version) if self.version in PREFERRED else len(PREFERRED)
        return 100 + abs(self.version[1] - 12)


def _probe(exe: str, run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run) -> Optional[PyCand]:
    try:
        r = run([exe, "-c", "import sys;print('%d.%d'%sys.version_info[:2]);import venv"], capture_output=True, text=True, timeout=20,
                creationflags=_NOWIN)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    m = re.match(r"(\d+)\.(\d+)", (r.stdout or "").strip())
    return PyCand(exe, (int(m[1]), int(m[2]))) if m else None


def candidate_pythons(run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run) -> list[PyCand]:
    """Interpreti Python trovati, il migliore per primo. Esclude quello dell'exe PyInstaller (non ha venv/pip)."""
    paths: list[str] = []
    if sys.platform == "win32":
        try:
            r = run(["py", "-0p"], capture_output=True, text=True, timeout=15, creationflags=_NOWIN)
            for ln in (r.stdout or "").splitlines():
                m = re.search(r"([A-Za-z]:\\.*?python[\w.]*\.exe)\s*$", ln.strip(), re.I)
                if m:
                    paths.append(m[1])
        except (OSError, subprocess.SubprocessError):
            pass
    for name in ("python3.12", "python3.11", "python3.10", "python3.13", "python3", "python"):
        p = shutil.which(name)
        if p:
            paths.append(p)
    if not getattr(sys, "frozen", False):
        paths.append(sys.executable)
    seen: set[str] = set()
    out: list[PyCand] = []
    for p in paths:
        key = os.path.normcase(os.path.realpath(p))
        if key in seen or "WindowsApps" in p:        # gli alias del Microsoft Store non sono Python reali
            continue
        seen.add(key)
        c = _probe(p, run)
        if c:
            out.append(c)
    out.sort(key=lambda c: c.score())
    return out


def best_python(cands: Optional[Iterable[PyCand]] = None) -> Optional[PyCand]:
    cs = sorted(cands, key=lambda c: c.score()) if cands is not None else candidate_pythons()
    return cs[0] if cs else None


def install_steps(py: str, vdir: Optional[Path] = None) -> list[tuple[str, list[str]]]:
    """Comandi (descrizione, argv) per creare il venv e installare PlatformIO."""
    v = vdir or venv_dir()
    vp = str(venv_python(v))
    return [
        ("Creo l'ambiente virtuale", [py, "-m", "venv", str(v)]),
        ("Aggiorno pip", [vp, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]),
        ("Installo PlatformIO Core", [vp, "-m", "pip", "install", "--upgrade", "platformio"]),
        ("Verifico", [vp, "-m", "platformio", "--version"]),
    ]


def run_install(py: str, emit: Callable[[str], None], vdir: Optional[Path] = None,
                run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run) -> bool:
    """Esegue i passi (bloccante: chiamalo da un thread). ``emit`` riceve le righe di log."""
    v = vdir or venv_dir()
    v.parent.mkdir(parents=True, exist_ok=True)
    for desc, argv in install_steps(py, v):
        emit(f"▸ {desc}…")
        try:
            r = run(argv, capture_output=True, text=True, creationflags=_NOWIN)
        except OSError as e:
            emit(f"  errore: {e}")
            return False
        for ln in ((r.stdout or "") + (r.stderr or "")).splitlines()[-40:]:
            emit("  " + ln)
        if r.returncode != 0:
            emit(f"✖ «{desc}» non riuscito (codice {r.returncode})")
            return False
    return venv_pio(v) is not None or venv_python(v).is_file()


def pio_command(vdir: Optional[Path] = None) -> Optional[list[str]]:
    """argv per lanciare pio dal venv dedicato (``pio.exe`` se c'è, altrimenti ``python -m platformio``)."""
    p = venv_pio(vdir)
    if p:
        return [str(p)]
    vp = venv_python(vdir)
    if vp.is_file():
        r = None
        try:
            r = subprocess.run([str(vp), "-c", "import platformio"], capture_output=True, timeout=30, creationflags=_NOWIN)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0:
            return [str(vp), "-m", "platformio"]
    return None
