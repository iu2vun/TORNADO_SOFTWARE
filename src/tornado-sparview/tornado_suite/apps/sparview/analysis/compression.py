"""Punto di compressione (P1dB) da sweep di potenza.

Il guadagno small-signal è la media dei primi N punti (potenze più basse); il P1dB è la potenza
in ingresso a cui il guadagno scende di `drop` dB rispetto a quel riferimento (interpolazione lineare
tra i due punti che attraversano la soglia). La potenza di uscita al P1dB è Pin + G(P1dB).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..core.network import Network


@dataclass
class P1dBResult:
    found: bool
    g_ref: float                     # guadagno small-signal [dB]
    level: float                     # g_ref - drop [dB]
    pin: Optional[float] = None      # IP1dB [dBm]
    pout: Optional[float] = None     # OP1dB [dBm]
    g_at: Optional[float] = None
    n_ref: int = 0
    note: str = ""


def p1db(pin: np.ndarray, gain_db: np.ndarray, n_ref: int = 5, drop: float = 1.0) -> P1dBResult:
    """pin [dBm] (qualsiasi ordine), gain_db [dB]."""
    pin = np.asarray(pin, float)
    g = np.asarray(gain_db, float)
    m = np.isfinite(pin) & np.isfinite(g)
    pin, g = pin[m], g[m]
    if pin.size < 3:
        return P1dBResult(False, float("nan"), float("nan"), note="servono almeno 3 punti validi")
    o = np.argsort(pin, kind="stable")
    pin, g = pin[o], g[o]
    n = int(min(max(1, n_ref), max(1, pin.size - 1)))
    g_ref = float(np.mean(g[:n]))
    level = g_ref - drop
    below = np.flatnonzero(g[n - 1:] <= level)
    if below.size == 0:
        return P1dBResult(False, g_ref, level, n_ref=n,
                          note="compressione non raggiunta nel range misurato")
    k = int(below[0]) + n - 1
    if k == 0:
        return P1dBResult(False, g_ref, level, n_ref=n, note="già in compressione al primo punto")
    g0, g1 = g[k - 1], g[k]
    t = 0.0 if g1 == g0 else (level - g0) / (g1 - g0)
    pin1 = float(pin[k - 1] + t * (pin[k] - pin[k - 1]))
    return P1dBResult(True, g_ref, level, pin1, pin1 + level, level, n)


def gain_from_pout(pin: np.ndarray, pout: np.ndarray) -> np.ndarray:
    return np.asarray(pout, float) - np.asarray(pin, float)


_PWR_RE = re.compile(r"(?<![\d.])(-?\d+(?:[.,]\d+)?)\s*(?:_|-)?\s*dbm", re.I)
_PWR_RE2 = re.compile(r"(?:^|[_\-\s])p(m?-?\d+(?:[.,]\d+)?)(?:$|[_\-\s.])", re.I)


def power_from_name(name: str) -> Optional[float]:
    """Estrae la potenza [dBm] dal nome file: '-10dBm', 'P-5', 'pm10' (m = meno)."""
    stem = Path(name).stem
    m = _PWR_RE.search(stem)
    if m:
        return float(m.group(1).replace(",", "."))
    m = _PWR_RE2.search(stem)
    if m:
        s = m.group(1).lower().replace(",", ".")
        return -float(s[1:]) if s.startswith("m") else float(s)
    return None


@dataclass
class SweepSet:
    freq: np.ndarray                 # (nf,) griglia comune
    power: np.ndarray                # (np,) dBm, crescente
    gain_db: np.ndarray              # (np, nf)


def sweep_from_networks(nets: Sequence[Network], powers: Sequence[float], i: int = 1, j: int = 0) -> SweepSet:
    """Guadagno |Sij| [dB] per ogni potenza sulla griglia di frequenza in comune (interpolata)."""
    if len(nets) != len(powers) or len(nets) < 3:
        raise ValueError("servono almeno 3 reti con una potenza ciascuna")
    lo = max(n.fmin for n in nets)
    hi = min(n.fmax for n in nets)
    if lo >= hi:
        raise ValueError("le reti non hanno frequenze in comune")
    base = nets[0].cropped(lo, hi).freq
    rows = []
    for n in nets:
        nn = n if (n.nfreq == base.size and np.allclose(n.freq, base)) else n.interpolated(base)
        with np.errstate(divide="ignore"):
            rows.append(20 * np.log10(np.maximum(np.abs(nn.s[:, i, j]), 1e-30)))
    p = np.asarray(powers, float)
    o = np.argsort(p)
    return SweepSet(base, p[o], np.asarray(rows)[o])


def p1db_vs_freq(sw: SweepSet, n_ref: int = 5, drop: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(IP1dB, OP1dB, G_small_signal) per frequenza; nan dove la compressione non è raggiunta."""
    nf = sw.freq.size
    ip, op, gr = (np.full(nf, np.nan) for _ in range(3))
    for k in range(nf):
        r = p1db(sw.power, sw.gain_db[:, k], n_ref, drop)
        gr[k] = r.g_ref
        if r.found:
            ip[k], op[k] = r.pin, r.pout
    return ip, op, gr


def read_power_sweep(path: str | Path) -> tuple[list[str], np.ndarray]:
    """CSV/testo tabellare con intestazione opzionale. Ritorna (nomi colonne, matrice float)."""
    txt = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    lines = [l for l in txt.splitlines() if l.strip() and not l.lstrip().startswith(("!", "#", "//"))]
    if not lines:
        raise ValueError("file vuoto")
    sample = lines[:20]
    sep = max((";", "\t", ",", " "), key=lambda c: sum(l.count(c) for l in sample))
    dec_comma = sep == ";" and any("," in l for l in sample)

    def cells(l: str) -> list[str]:
        return [c.strip().strip('"') for c in (l.split() if sep == " " else l.split(sep))]

    def num(c: str) -> float:
        try:
            return float(c.replace(",", ".") if dec_comma else c)
        except ValueError:
            return float("nan")

    first = cells(lines[0])
    header = all(np.isnan(num(c)) for c in first if c)
    names = first if header else [f"col{k + 1}" for k in range(len(first))]
    rows = [[num(c) for c in cells(l)] for l in lines[1 if header else 0:]]
    w = max(len(r) for r in rows)
    arr = np.array([r + [float("nan")] * (w - len(r)) for r in rows])
    arr = arr[~np.all(np.isnan(arr), axis=1)]
    names = (names + [f"col{k + 1}" for k in range(len(names), w)])[:w]
    if arr.shape[0] < 3:
        raise ValueError("troppi pochi punti numerici")
    return names, arr
