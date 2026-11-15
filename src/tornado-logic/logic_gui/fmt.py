"""Formattazione dei valori con prefissi SI (nessuna dipendenza da Qt)."""
from __future__ import annotations

import math

_PREFIXES = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"))


def fmt_si(value: float | None, unit: str = "", digits: int = 4) -> str:
    """12345.6 Hz -> '12.35 kHz'. None/NaN -> '—'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    if value == 0:
        return f"0 {unit}".strip()
    a = abs(value)
    scale, prefix = _PREFIXES[-1]
    for s, p in _PREFIXES:
        if a >= s:
            scale, prefix = s, p
            break
    v = value / scale
    txt = f"{v:.{digits}g}"
    if "e" in txt:
        txt = f"{v:.0f}"
    return f"{txt} {prefix}{unit}".strip()


def fmt_percent(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f} %"
