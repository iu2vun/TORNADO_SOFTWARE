"""Integrazione con com0com (coppie di porte seriali virtuali, Windows): rilevamento, elenco, creazione.

Sintassi di setupc.exe (da ReadMe di com0com): `list`, `install <PortName=COM#|-> <PortName=COM#|->`,
`change CNCA0 PortName=COM8`. Va eseguito dalla cartella di installazione e con privilegi di amministratore.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Pair:
    index: int
    a: str = ""      # nome COM del lato A (CNCA<index>), "" se senza nome COM
    b: str = ""

    def label(self) -> str:
        return f"{self.a or '?'} ⇄ {self.b or '?'}   (coppia {self.index})"


def find_setupc() -> str:
    cands = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            cands.append(os.path.join(base, "com0com", "setupc.exe"))
    cands += [r"C:\Program Files (x86)\com0com\setupc.exe", r"C:\Program Files\com0com\setupc.exe"]
    for c in cands:
        if os.path.isfile(c):
            return c
    return ""


def run(setupc: str, *args: str, timeout: float = 60.0) -> tuple[int, str]:
    """Esegue setupc.exe con cwd = cartella di installazione. Ritorna (codice, output)."""
    p = subprocess.run([setupc, "--silent", *args] if args and args[0] != "list" else [setupc, *args],
                       cwd=os.path.dirname(setupc), capture_output=True, timeout=timeout,
                       creationflags=_NOWIN if sys.platform.startswith("win") else 0)
    out = (p.stdout or b"") + (p.stderr or b"")
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError:
        text = out.decode("cp1252", "replace")
    return p.returncode, text


_RE_LINE = re.compile(r"^\s*CNC([AB])(\d+)\s+(.*)$", re.I)


def parse_list(text: str) -> list[Pair]:
    """Righe tipo 'CNCA0 PortName=COM#,RealPortName=COM3' / 'CNCB0 PortName=COM4' / 'CNCB0 PortName=-'."""
    pairs: dict[int, Pair] = {}
    for ln in text.splitlines():
        m = _RE_LINE.match(ln)
        if not m:
            continue
        side, idx, params = m.group(1).upper(), int(m.group(2)), m.group(3)
        kv = dict(p.split("=", 1) for p in re.split(r"[,\s]+", params.strip()) if "=" in p)
        name = kv.get("RealPortName") or kv.get("PortName") or ""
        if name in ("-", "COM#"):
            name = kv.get("RealPortName", "") if name == "COM#" else ""
        pr = pairs.setdefault(idx, Pair(idx))
        if side == "A":
            pr.a = name
        else:
            pr.b = name
    return [pairs[k] for k in sorted(pairs)]


def list_pairs(setupc: str) -> list[Pair]:
    code, out = run(setupc, "list")
    if code != 0 and not out.strip():
        raise RuntimeError("setupc.exe list non ha prodotto output (servono i privilegi di amministratore?)")
    return parse_list(out)


def create_pair(setupc: str, name_a: str = "", name_b: str = "") -> tuple[Optional[Pair], str]:
    """Crea una coppia. Nomi vuoti = prossimo COM libero. Ritorna (nuova coppia o None, output)."""
    before = {p.index for p in list_pairs(setupc)}
    pa = f"PortName={name_a}" if name_a else "PortName=COM#"
    pb = f"PortName={name_b}" if name_b else "PortName=COM#"
    code, out = run(setupc, "install", pa, pb)
    after = list_pairs(setupc)
    new = [p for p in after if p.index not in before]
    if code != 0 or not new:
        return None, out
    return new[-1], out
