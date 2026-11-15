"""Lettura/scrittura CSV di parametri S con riconoscimento automatico di colonne e formati.

Gestisce: separatori , ; TAB spazio; virgola decimale (locale italiano); righe di commento
(! # %); blocchi Keysight (BEGIN ... END); intestazioni tipo 'S11 (dB)', 'Re(S21)', 'S21_deg',
'Freq [MHz]', '|S11|', 'Phase(S11)'. Senza intestazione: frequenza + coppie (euristica).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

from ..core.network import Network
from .touchstone import read_text

_UNIT_RE = re.compile(r"(?:\(|\[|_|\s|^)\s*(ghz|mhz|khz|hz)\s*(?:\)|\]|$|\s)", re.I)
_FREQ_MULT = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
_S_RE = re.compile(r"S\s*\(?\s*(\d+)\s*[,_ ]?\s*(\d+)?\s*\)?", re.I)


class CsvError(ValueError):
    pass


def _split_ports(a: str, b: Optional[str], explicit_sep: bool) -> tuple[int, int]:
    if b is not None and (explicit_sep or len(a) + len(b) <= 2):
        return int(a), int(b)
    digits = a + (b or "")
    if len(digits) % 2 == 0:
        h = len(digits) // 2
        return int(digits[:h]), int(digits[h:])
    raise CsvError(f"indici di porta ambigui: S{digits}")


def _kind_from_header(h: str) -> str:
    t = h.lower()
    if "db" in t or "log" in t:
        return "db"
    if any(k in t for k in ("deg", "phase", "ang", "°", "arg", "fase")):
        return "rad" if ("rad" in t and "deg" not in t) else "deg"
    if "rad" in t:
        return "rad"
    if "imag" in t or re.search(r"(^|[^a-z])im([^a-z]|$)", t):
        return "im"
    if "real" in t or re.search(r"(^|[^a-z])re([^a-z]|$)", t):
        return "re"
    return "mag"


def _tok_ok(tok: str) -> bool:
    t = tok.strip().strip('"')
    if t == "":
        return True
    try:
        float(t.replace(",", "."))
        return True
    except ValueError:
        return False


def _detect_delim(lines: list[str]) -> str | None:
    """Ordine di preferenza ; TAB , spazi. Un candidato vale se tutte le righe campione hanno lo
    stesso numero (>=2) di campi e ogni campo e' numerico (ammessa la virgola decimale)."""
    for d in (";", "\t", ",", None):
        parts = [(ln.split(d) if d else ln.split()) for ln in lines]
        counts = [len([t for t in p if t.strip() != ""]) for p in parts]
        if min(counts) < 2 or len(set(counts)) != 1:
            continue
        if all(_tok_ok(t) for p in parts for t in p):
            return d
    raise CsvError("separatore non riconosciuto")


def _to_float(tok: str) -> float:
    return float(tok.strip().strip('"').replace(",", "."))


def parse_csv(text: str, name: str = "", path: Optional[str] = None, z0: float = 50.0,
              freq_unit: Optional[float] = None, assume_format: Optional[str] = None) -> Network:
    lines = [ln.rstrip("\r") for ln in text.splitlines()]
    comments: list[str] = []
    body: list[str] = []
    header: Optional[str] = None
    in_block = None
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        up = s.upper()
        if up.startswith("BEGIN"):
            if in_block is None:
                in_block = True
            else:
                break
            continue
        if up.startswith("END"):
            if in_block:
                break
            continue
        if s[0] in "!#%/":
            core = s.lstrip("!#%/ ").strip()
            if header is None and re.search(r"freq", core, re.I) and not re.match(r"^[\d.+-]", core):
                header = core
            else:
                comments.append(core)
            continue
        if re.match(r"^[A-Za-z\"']", s) and not re.match(r"^[+-]?\d", s):
            if header is None and re.search(r"freq|^s\d|s\s*\(", s, re.I):
                header = s
            else:
                comments.append(s)
            continue
        body.append(s)
    if not body:
        raise CsvError("nessun dato numerico")

    sample = body[: min(len(body), 20)]
    delim = _detect_delim(sample)
    # virgola decimale se il separatore non e' la virgola
    def split(ln: str) -> list[str]:
        return ln.split(delim) if delim else ln.split()

    rows = []
    ncol = None
    for ln in body:
        toks = split(ln)
        toks = [t for t in toks if t.strip() != ""]
        try:
            vals = [_to_float(t) for t in toks]
        except ValueError:
            continue
        if ncol is None:
            ncol = len(vals)
        if len(vals) != ncol:
            continue
        rows.append(vals)
    if not rows:
        raise CsvError("nessuna riga numerica valida")
    data = np.asarray(rows, float)
    ncol = data.shape[1]

    cols: list[tuple[int, int, str, int]] = []   # (i, j, kind, colindex)
    fcol = 0
    fmult = 1.0
    if header:
        hd = [h.strip().strip('"') for h in (header.split(delim) if delim else re.split(r"\s{2,}|\t", header))]
        if len(hd) != ncol:
            hd2 = header.split() if delim is None else hd
            hd = hd2 if len(hd2) == ncol else hd
        if len(hd) == ncol:
            fcol = next((k for k, h in enumerate(hd) if re.search(r"freq", h, re.I)), 0)
            m = _UNIT_RE.search(hd[fcol])
            if m:
                fmult = _FREQ_MULT[m.group(1).lower()]
            for k, h in enumerate(hd):
                if k == fcol:
                    continue
                m = _S_RE.search(h)
                if not m:
                    continue
                a, b = m.group(1), m.group(2)
                explicit = bool(re.search(r"S\s*\(?\s*\d+\s*[,_]\s*\d+", h, re.I))
                i, j = _split_ports(a, b, explicit)
                cols.append((i - 1, j - 1, _kind_from_header(h[m.end():] or h), k))
                # il testo prima di S (es. 'Re(S11)') puo' contenere il tipo
                pre = h[:m.start()]
                if pre.strip():
                    kd = _kind_from_header(pre)
                    if kd != "mag" or "mag" in pre.lower() or "|" in pre:
                        cols[-1] = (i - 1, j - 1, kd, k)
    if freq_unit is not None:
        fmult = freq_unit

    if not cols:
        # euristica senza intestazione: f + coppie
        if (ncol - 1) % 2 != 0 or ncol < 3:
            raise CsvError("intestazione assente e numero di colonne non compatibile con coppie (f + 2k valori)")
        npairs = (ncol - 1) // 2
        n = int(round(npairs ** 0.5))
        if n * n != npairs:
            raise CsvError("numero di coppie non quadrato: impossibile dedurre N porte")
        a = data[:, 1::2]
        b = data[:, 2::2]
        fmt = assume_format
        if fmt is None:
            if np.abs(b).max() > 3.3:
                fmt = "db" if a.min() < 0 else "ma"
            else:
                fmt = "ri"
        kind_a, kind_b = {"ri": ("re", "im"), "ma": ("mag", "deg"), "db": ("db", "deg")}[fmt]
        k = 1
        for i in range(n):
            for j in range(n):
                cols.append((i, j, kind_a, k))
                cols.append((i, j, kind_b, k + 1))
                k += 2

    n = max(max(c[0], c[1]) for c in cols) + 1
    freq = data[:, fcol] * fmult
    if freq_unit is None and not header and freq.max() < 1e3:
        freq = freq * 1e9   # senza unità e valori piccoli: GHz (convenzione Touchstone)
    parts: dict[tuple[int, int], dict[str, np.ndarray]] = {}
    for (i, j, kind, k) in cols:
        parts.setdefault((i, j), {})[kind] = data[:, k]
    s = np.zeros((freq.size, n, n), complex)
    missing = []
    for i in range(n):
        for j in range(n):
            p = parts.get((i, j))
            if not p:
                missing.append((i + 1, j + 1))
                continue
            if "re" in p and "im" in p:
                v = p["re"] + 1j * p["im"]
            else:
                if "db" in p:
                    mag = 10 ** (p["db"] / 20)
                elif "mag" in p:
                    mag = p["mag"]
                elif "re" in p or "im" in p:
                    raise CsvError(f"S{i + 1}{j + 1}: servono parte reale e immaginaria")
                else:
                    raise CsvError(f"S{i + 1}{j + 1}: manca la magnitudine")
                if "deg" in p:
                    ph = np.deg2rad(p["deg"])
                elif "rad" in p:
                    ph = p["rad"]
                else:
                    ph = np.zeros_like(mag)
                v = mag * np.exp(1j * ph)
            s[:, i, j] = v
    if missing:
        comments.append("ATTENZIONE: parametri assenti nel CSV (posti a 0): "
                        + ", ".join(f"S{a}{b}" for a, b in missing[:12]))
    return Network(freq, s, z0, name=name, path=path, comments=comments, source_format="CSV")


def read_csv(path: str | Path, **kw) -> Network:
    return parse_csv(read_text(path), name=Path(path).stem, path=str(path), **kw)


def format_csv(net: Network, fmt: str = "db", freq_unit: str = "Hz", sep: str = ",") -> str:
    """CSV completo di tutti i parametri S: fmt in {db, ma, ri}."""
    mult = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}[freq_unit]
    hdr = [f"Freq [{freq_unit}]"]
    cols = [net.freq / mult]
    for i in range(net.nports):
        for j in range(net.nports):
            v = net.s[:, i, j]
            lab = net.label(i, j)
            if fmt == "ri":
                hdr += [f"Re({lab})", f"Im({lab})"]
                cols += [v.real, v.imag]
            elif fmt == "ma":
                hdr += [f"|{lab}|", f"{lab} [deg]"]
                cols += [np.abs(v), np.degrees(np.angle(v))]
            else:
                hdr += [f"{lab} [dB]", f"{lab} [deg]"]
                cols += [20 * np.log10(np.maximum(np.abs(v), 1e-30)), np.degrees(np.angle(v))]
    out = [sep.join(hdr)]
    mat = np.column_stack(cols)
    for r in mat:
        out.append(sep.join(f"{x:.12g}" for x in r))
    return "\n".join(out) + "\n"


def write_csv(net: Network, path: str | Path, **kw) -> str:
    Path(path).write_text(format_csv(net, **kw), encoding="utf-8")
    return str(path)
