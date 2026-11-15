"""Formattazione e parsing di grandezze con prefissi ingegneristici."""
from __future__ import annotations

import math
import re

FREQ_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}

_PREFIXES = [
    (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
    (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f"),
]


def eng_split(value: float) -> tuple[float, str, float]:
    """Ritorna (valore scalato, prefisso, fattore) per un valore reale."""
    if value == 0 or not math.isfinite(value):
        return value, "", 1.0
    a = abs(value)
    for factor, prefix in _PREFIXES:
        if a >= factor * 0.99999999:
            return value / factor, prefix, factor
    factor, prefix = _PREFIXES[-1]
    return value / factor, prefix, factor


def fmt_eng(value: float, unit: str = "", digits: int = 5) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    v, p, _ = eng_split(value)
    s = f"{v:.{digits}g}"
    return f"{s} {p}{unit}".strip()


def fmt_freq(f: float, digits: int = 7) -> str:
    return fmt_eng(f, "Hz", digits)


_FREQ_RE = re.compile(
    r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([kKmMgGtT]?)\s*(?:[hH][zZ])?\s*$"
)
_MULT = {"": 1.0, "k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}


def parse_freq(text: str, default_unit: float = 1.0) -> float:
    """'2.4 GHz', '100M', '1e9', '868k' -> Hz. Senza suffisso usa default_unit."""
    m = _FREQ_RE.match(text.replace(",", "."))
    if not m:
        raise ValueError(f"frequenza non valida: {text!r}")
    num, pre = m.group(1), m.group(2).lower()
    if pre == "":
        return float(num) * default_unit
    return float(num) * _MULT[pre]


def choose_freq_unit(fmax: float) -> tuple[str, float]:
    """Unità di visualizzazione adatta a un asse con estremo fmax."""
    a = abs(fmax)
    if a >= 1e9:
        return "GHz", 1e9
    if a >= 1e6:
        return "MHz", 1e6
    if a >= 1e3:
        return "kHz", 1e3
    return "Hz", 1.0


def choose_time_unit(tmax: float) -> tuple[str, float]:
    a = abs(tmax)
    if a >= 1.0:
        return "s", 1.0
    if a >= 1e-3:
        return "ms", 1e-3
    if a >= 1e-6:
        return "µs", 1e-6
    if a >= 1e-9:
        return "ns", 1e-9
    return "ps", 1e-12
