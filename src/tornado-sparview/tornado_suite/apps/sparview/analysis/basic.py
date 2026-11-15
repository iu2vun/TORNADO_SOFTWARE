"""Analisi di tracce scalari: soglie/bande, picchi, larghezza di banda, Q, ripple."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import find_peaks


def crossings(f: np.ndarray, y: np.ndarray, level: float) -> np.ndarray:
    """Frequenze (interpolate linearmente) in cui y attraversa `level`."""
    d = y - level
    ok = np.isfinite(d)
    out = []
    idx = np.flatnonzero(ok[:-1] & ok[1:] & (np.sign(d[:-1]) * np.sign(d[1:]) < 0))
    for i in idx:
        t = d[i] / (d[i] - d[i + 1])
        out.append(f[i] + t * (f[i + 1] - f[i]))
    zero = np.flatnonzero(ok & (d == 0))
    out.extend(f[zero].tolist())
    return np.array(sorted(out))


def bands_where(f: np.ndarray, y: np.ndarray, lo: Optional[float] = None,
                hi: Optional[float] = None) -> list[tuple[float, float]]:
    """Intervalli di frequenza in cui lo <= y <= hi (estremi interpolati)."""
    ok = np.isfinite(y)
    cond = ok.copy()
    if lo is not None:
        cond &= y >= lo
    if hi is not None:
        cond &= y <= hi
    if not cond.any():
        return []
    d = np.diff(np.concatenate(([0], cond.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    out = []
    for a, b in zip(starts, ends):
        fa, fb = float(f[a]), float(f[b])
        if a > 0 and ok[a - 1]:
            lvl = lo if (lo is not None and y[a - 1] < lo) else hi
            if lvl is not None and y[a] != y[a - 1]:
                fa = float(f[a - 1] + (lvl - y[a - 1]) / (y[a] - y[a - 1]) * (f[a] - f[a - 1]))
        if b < f.size - 1 and ok[b + 1]:
            lvl = lo if (lo is not None and y[b + 1] < lo) else hi
            if lvl is not None and y[b + 1] != y[b]:
                fb = float(f[b] + (lvl - y[b]) / (y[b + 1] - y[b]) * (f[b + 1] - f[b]))
        out.append((fa, fb))
    return out


def peak_indices(y: np.ndarray, mode: str = "max", prominence: Optional[float] = None,
                 min_distance: int = 1) -> np.ndarray:
    """Indici dei massimi ('max') o minimi ('min') locali, in ordine di frequenza."""
    yy = y.copy()
    bad = ~np.isfinite(yy)
    if bad.all():
        return np.array([], int)
    yy[bad] = np.nanmin(yy) if mode == "max" else np.nanmax(yy)
    if mode == "min":
        yy = -yy
    if prominence is None:
        rng = np.ptp(yy)
        prominence = 0.02 * rng if rng > 0 else None
    idx, _ = find_peaks(yy, prominence=prominence, distance=max(1, int(min_distance)))
    return idx


def next_peak(f: np.ndarray, y: np.ndarray, from_idx: int, direction: int, mode: str = "max",
              prominence: Optional[float] = None) -> Optional[int]:
    """Prossimo picco a destra (+1) o a sinistra (-1) dell'indice dato."""
    pk = peak_indices(y, mode, prominence)
    if pk.size == 0:
        return None
    if direction > 0:
        c = pk[pk > from_idx]
        return int(c[0]) if c.size else None
    c = pk[pk < from_idx]
    return int(c[-1]) if c.size else None


@dataclass
class BandInfo:
    f_peak: float
    y_peak: float
    level: float
    f_low: Optional[float]
    f_high: Optional[float]

    @property
    def bandwidth(self) -> Optional[float]:
        if self.f_low is None or self.f_high is None:
            return None
        return self.f_high - self.f_low

    @property
    def center(self) -> Optional[float]:
        if self.f_low is None or self.f_high is None:
            return None
        return 0.5 * (self.f_low + self.f_high)

    @property
    def q(self) -> Optional[float]:
        bw = self.bandwidth
        if not bw:
            return None
        return self.f_peak / bw


def bandwidth_around(f: np.ndarray, y: np.ndarray, drop: float = 3.0, mode: str = "max",
                     index: Optional[int] = None) -> Optional[BandInfo]:
    """Banda a -`drop` dB (rispetto al picco) attorno al picco globale (max) o al minimo (min).

    Per mode='min' (es. S11 in dB) la banda è dove y <= minimo + drop.
    """
    if not np.isfinite(y).any():
        return None
    if index is None:
        index = int(np.nanargmax(y) if mode == "max" else np.nanargmin(y))
    y0 = float(y[index])
    level = y0 - drop if mode == "max" else y0 + drop
    f_low = f_high = None
    # discesa a sinistra
    for k in range(index, 0, -1):
        a, b = y[k - 1], y[k]
        if np.isfinite(a) and ((mode == "max" and a < level) or (mode == "min" and a > level)):
            f_low = float(f[k - 1] + (level - a) / (b - a) * (f[k] - f[k - 1]))
            break
    for k in range(index, y.size - 1):
        a, b = y[k], y[k + 1]
        if np.isfinite(b) and ((mode == "max" and b < level) or (mode == "min" and b > level)):
            f_high = float(f[k] + (level - a) / (b - a) * (f[k + 1] - f[k]))
            break
    return BandInfo(float(f[index]), y0, level, f_low, f_high)


def range_stats(f: np.ndarray, y: np.ndarray, f1: Optional[float] = None,
                f2: Optional[float] = None) -> dict:
    m = np.isfinite(y)
    if f1 is not None:
        m &= f >= f1
    if f2 is not None:
        m &= f <= f2
    if not m.any():
        return {}
    yy, ff = y[m], f[m]
    imin, imax = int(np.argmin(yy)), int(np.argmax(yy))
    return {"n": int(m.sum()), "min": float(yy[imin]), "f_min": float(ff[imin]),
            "max": float(yy[imax]), "f_max": float(ff[imax]), "mean": float(yy.mean()),
            "std": float(yy.std()), "ripple": float(yy[imax] - yy[imin]),
            "f1": float(ff[0]), "f2": float(ff[-1])}


def q_from_phase_slope(f: np.ndarray, s: np.ndarray, f0: float) -> Optional[float]:
    """Q caricato dalla pendenza della fase di un riflessivo: Q = (f0/2)|dphi/df|."""
    ph = np.unwrap(np.angle(s))
    i = int(np.argmin(np.abs(f - f0)))
    lo, hi = max(i - 2, 0), min(i + 3, f.size)
    if hi - lo < 3:
        return None
    slope = np.polyfit(f[lo:hi], ph[lo:hi], 1)[0]
    return abs(f0 / 2 * slope)
