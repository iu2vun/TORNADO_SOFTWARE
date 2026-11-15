"""Memoria del firmware (senza GUI): storico delle build, sezioni e simboli dall'ELF, avanzamento di compilazione."""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HIST_DIR = ".tornado"
HIST_FILE = "size_history.json"
MAX_HISTORY = 200


# ------------------------------------------------------------------ storico

@dataclass
class Sample:
    t: float
    env: str
    ram_used: int = 0
    ram_total: int = 0
    flash_used: int = 0
    flash_total: int = 0
    secs: float = 0.0
    compiled: int = 0

    @property
    def ram_pct(self) -> float:
        return 100.0 * self.ram_used / self.ram_total if self.ram_total else 0.0

    @property
    def flash_pct(self) -> float:
        return 100.0 * self.flash_used / self.flash_total if self.flash_total else 0.0


class History:
    def __init__(self, root: str) -> None:
        self.path = Path(root) / HIST_DIR / HIST_FILE
        self.items: list[Sample] = []
        self.load()

    def load(self) -> None:
        self.items = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for d in data if isinstance(data, list) else []:
                self.items.append(Sample(**{k: d[k] for k in Sample.__dataclass_fields__ if k in d}))
        except (OSError, ValueError, TypeError):
            self.items = []

    def add(self, s: Sample) -> None:
        self.items.append(s)
        self.items = self.items[-MAX_HISTORY:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([x.__dict__ for x in self.items]), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def for_env(self, env: str) -> list[Sample]:
        return [s for s in self.items if s.env == env]

    def last_compiled(self, env: str) -> int:
        """Numero di file compilati nell'ultima build completa di questo ambiente (per stimare l'avanzamento)."""
        best = 0
        for s in self.for_env(env)[-5:]:
            best = max(best, s.compiled)
        return best


def delta(prev: Optional[Sample], cur: Sample) -> tuple[int, int]:
    """(variazione RAM, variazione Flash) in byte rispetto alla build precedente."""
    if prev is None:
        return 0, 0
    return cur.ram_used - prev.ram_used, cur.flash_used - prev.flash_used


# ------------------------------------------------------------------ avanzamento build

_COMPILE = re.compile(r"^(Compiling|Archiving|Indexing)\s+\S+")
_LINK = re.compile(r"^(Linking|Generating|Building)\s+\S+")


class BuildProgress:
    """Stima l'avanzamento di ``pio run`` contando le righe «Compiling …» rispetto alla build precedente."""

    def __init__(self, expected: int = 0) -> None:
        self.expected = expected
        self.compiled = 0
        self.linking = False

    def feed(self, line: str) -> None:
        if _COMPILE.match(line):
            self.compiled += 1
        elif _LINK.match(line):
            if line.startswith("Linking"):
                self.linking = True

    @property
    def known(self) -> bool:
        return self.expected > 0

    @property
    def percent(self) -> int:
        if self.linking:
            return 95
        if not self.known:
            return 0
        return min(90, int(90 * self.compiled / self.expected))


# ------------------------------------------------------------------ ELF

def find_elf(root: str, env: str) -> Optional[str]:
    for name in ("firmware.elf", "program.elf"):
        p = os.path.join(root, ".pio", "build", env, name)
        if os.path.isfile(p):
            return p
    hits = glob.glob(os.path.join(root, ".pio", "build", env, "*.elf"))
    return hits[0] if hits else None


def find_binutils(tool: str, platform_hint: str = "") -> Optional[str]:
    """``size`` / ``nm`` della toolchain di PlatformIO (xtensa, riscv, arm…) oppure quelli di sistema."""
    pkgs = Path(os.environ.get("PLATFORMIO_CORE_DIR") or Path.home() / ".platformio") / "packages"
    pats = [f"toolchain-*/bin/*-{tool}", f"toolchain-*/bin/*-{tool}.exe", f"toolchain-*/bin/{tool}", f"toolchain-*/bin/{tool}.exe"]
    cands: list[str] = []
    for pat in pats:
        cands += glob.glob(str(pkgs / pat))
    if platform_hint:
        pref = [c for c in cands if platform_hint in c]
        cands = pref or cands
    # esclude le varianti con prefisso gdb/addr2line ecc. già filtrate dal pattern; sceglie la più corta (es. xtensa-esp32-elf-nm)
    cands = [c for c in cands if os.path.basename(c).split(".")[0].endswith(tool)]
    if cands:
        return sorted(cands, key=lambda c: (len(os.path.basename(c)), c))[0]
    return shutil.which(tool) or shutil.which(f"gnu{tool}")


@dataclass(frozen=True)
class Section:
    name: str
    size: int
    addr: int


_SIZE_LINE = re.compile(r"^(\S+)\s+(\d+)\s+(\d+|0x[0-9a-fA-F]+)\s*$")


def parse_size_a(text: str) -> list[Section]:
    out: list[Section] = []
    for ln in text.splitlines():
        m = _SIZE_LINE.match(ln.strip())
        if m and not m[1].startswith("section") and int(m[2]) > 0:
            out.append(Section(m[1], int(m[2]), int(m[3], 0)))
    return out


def section_sizes(size_exe: str, elf: str) -> list[Section]:
    r = subprocess.run([size_exe, "-A", elf], capture_output=True, text=True, timeout=60, creationflags=_NOWIN)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return sorted(parse_size_a(r.stdout), key=lambda s: -s.size)


@dataclass(frozen=True)
class Sym:
    name: str
    size: int
    kind: str       # T/t codice, D/d dati, B/b bss, R/r sola lettura
    where: str = ""

    @property
    def area(self) -> str:
        k = self.kind.upper()
        return {"T": "Flash (codice)", "W": "Flash (codice)", "D": "RAM (dati)", "B": "RAM (bss)", "R": "Flash (const)", "V": "Flash (codice)"}.get(k, k)


_NM_LINE = re.compile(r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([A-Za-z])\s+(.*?)(?:\t(.*))?$")


def parse_nm(text: str) -> list[Sym]:
    out: list[Sym] = []
    for ln in text.splitlines():
        m = _NM_LINE.match(ln.rstrip())
        if m:
            size = int(m[2], 16)
            if size > 0 and m[3] in "TtDdBbRrWwVv":
                out.append(Sym(m[4].strip(), size, m[3], (m[5] or "").strip()))
    return out


def top_symbols(nm_exe: str, elf: str, n: int = 80) -> list[Sym]:
    r = subprocess.run([nm_exe, "-S", "-C", "-l", "--size-sort", "-r", elf], capture_output=True, text=True, timeout=120,
                       creationflags=_NOWIN, errors="replace")
    if r.returncode != 0:
        r = subprocess.run([nm_exe, "-S", "-C", "--size-sort", "-r", elf], capture_output=True, text=True, timeout=120,
                           creationflags=_NOWIN, errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip())
    return sorted(parse_nm(r.stdout), key=lambda s: -s.size)[:n]


def group_by_origin(syms: list[Sym]) -> list[tuple[str, int]]:
    """Somma delle dimensioni per origine (cartella libreria / file sorgente), dai percorsi ottenuti con nm -l."""
    acc: dict[str, int] = {}
    for s in syms:
        w = s.where.split(":")[0].replace("\\", "/")
        if not w:
            key = "(sconosciuto)"
        elif "/libdeps/" in w:
            key = "lib: " + w.split("/libdeps/")[1].split("/")[1] if w.count("/libdeps/") and len(w.split("/libdeps/")[1].split("/")) > 1 else "lib"
        elif "/framework-" in w:
            key = "framework: " + w.split("/framework-")[1].split("/")[0]
        elif "/src/" in w or w.startswith("src/"):
            key = "progetto: " + os.path.basename(w)
        else:
            key = os.path.basename(os.path.dirname(w)) or w
        acc[key] = acc.get(key, 0) + s.size
    return sorted(acc.items(), key=lambda kv: -kv[1])


def human(n: int) -> str:
    if abs(n) >= 1 << 20:
        return f"{n / (1 << 20):.2f} MB"
    if abs(n) >= 1 << 10:
        return f"{n / (1 << 10):.1f} KB"
    return f"{n} B"


def now() -> float:
    return time.time()
