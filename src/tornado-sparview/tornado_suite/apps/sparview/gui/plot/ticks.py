"""Generazione tick 'gradevoli' per assi lineari e logaritmici."""
from __future__ import annotations

import math

import numpy as np


def nice_step(span: float, target: int) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag * 1.0000001:
            return m * mag
    return 10 * mag


def linear_ticks(lo: float, hi: float, target: int = 8) -> tuple[np.ndarray, float]:
    """Ritorna (tick maggiori, passo)."""
    if hi < lo:
        lo, hi = hi, lo
    step = nice_step(hi - lo, target)
    first = math.ceil(lo / step - 1e-9) * step
    n = int(math.floor((hi - first) / step + 1e-9)) + 1
    ticks = first + step * np.arange(max(n, 0))
    return ticks, step


def minor_ticks(lo: float, hi: float, step: float, sub: int = 5) -> np.ndarray:
    ms = step / sub
    first = math.ceil(lo / ms - 1e-9) * ms
    n = int(math.floor((hi - first) / ms + 1e-9)) + 1
    return first + ms * np.arange(max(n, 0))


def log_ticks(lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Tick maggiori (decadi) e minori (2..9) per asse log. lo, hi > 0."""
    lo = max(lo, 1e-300)
    d0, d1 = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    major, minor = [], []
    for d in range(d0, d1 + 1):
        major.append(10.0 ** d)
        for m in range(2, 10):
            minor.append(m * 10.0 ** d)
    major, minor = np.array(major), np.array(minor)
    return major[(major >= lo * 0.999) & (major <= hi * 1.001)], minor[(minor >= lo) & (minor <= hi)]


def decimals_for(step: float) -> int:
    if step <= 0 or not math.isfinite(step):
        return 0
    for d in range(0, 12):
        if abs(round(step, d) - step) < step * 1e-6:
            return d
    return 6


def fmt_tick(v: float, decimals: int) -> str:
    if abs(v) < 10 ** (-decimals) * 0.01:
        v = 0.0
    return f"{v:.{decimals}f}"
