"""Cerca e sostituisci in tutti i file del progetto (senza GUI)."""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

SKIP = {".pio", ".git", ".vscode", "__pycache__", ".idea", ".tornado", "build", "node_modules"}
TEXT_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ino", ".ini", ".txt", ".md", ".csv", ".json", ".py",
            ".html", ".js", ".css", ".cfg", ".yml", ".yaml", ".s", ".ld", ".tcc", ""}
MAX_SIZE = 2_000_000


@dataclass(frozen=True)
class Hit:
    file: str
    line: int
    col: int
    text: str
    length: int


def compile_pattern(query: str, case: bool = False, regex: bool = False, whole: bool = False) -> re.Pattern:
    if not query:
        raise ValueError("testo da cercare vuoto")
    pat = query if regex else re.escape(query)
    if whole:
        pat = rf"\b(?:{pat})\b"
    return re.compile(pat, 0 if case else re.IGNORECASE)


def iter_files(root: str, globs: str = "", include_pio: bool = False) -> Iterable[str]:
    pats = [g.strip() for g in re.split(r"[;,]", globs) if g.strip()]
    skip = SKIP - {".pio"} if include_pio else SKIP
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in skip)
        for f in sorted(fns):
            if pats:
                if not any(fnmatch.fnmatch(f.lower(), p.lower()) for p in pats):
                    continue
            elif os.path.splitext(f)[1].lower() not in TEXT_EXT:
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) > MAX_SIZE:
                    continue
            except OSError:
                continue
            yield p


def _read(path: str) -> Optional[str]:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def search(root: str, query: str, case: bool = False, regex: bool = False, whole: bool = False, globs: str = "",
           limit: int = 2000, overrides: Optional[dict[str, str]] = None) -> list[Hit]:
    """Occorrenze. ``overrides``: testo non salvato dei file aperti nell'editor (percorso -> testo)."""
    pat = compile_pattern(query, case, regex, whole)
    hits: list[Hit] = []
    ov = {os.path.normcase(os.path.abspath(k)): v for k, v in (overrides or {}).items()}
    for p in iter_files(root, globs):
        text = ov.get(os.path.normcase(os.path.abspath(p)))
        if text is None:
            text = _read(p)
        if text is None:
            continue
        for n, ln in enumerate(text.splitlines(), 1):
            for m in pat.finditer(ln):
                if m.end() == m.start():
                    continue
                hits.append(Hit(p, n, m.start() + 1, ln.strip()[:240], m.end() - m.start()))
                if len(hits) >= limit:
                    return hits
    return hits


def replace_in_files(files: Iterable[str], query: str, repl: str, case: bool = False, regex: bool = False,
                     whole: bool = False) -> dict[str, int]:
    """Sostituisce in ciascun file (riscrittura atomica, stesso encoding e fine riga). Ritorna {file: n sostituzioni}."""
    pat = compile_pattern(query, case, regex, whole)
    out: dict[str, int] = {}
    for p in files:
        try:
            raw = Path(p).read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            enc, text = "cp1252", raw.decode("cp1252", errors="replace")
        new, n = pat.subn((lambda m: m.expand(repl)) if regex else (lambda m: repl), text)
        if n:
            tmp = p + ".tmp~"
            with open(tmp, "w", encoding=enc, newline="") as f:     # newline="" mantiene i fine riga originali
                f.write(new)
            os.replace(tmp, p)
            out[p] = n
    return out
