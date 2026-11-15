"""Grandezze visualizzabili (tracce) derivate da una rete.

`trace_values(net, i, j, kind)` ritorna un array reale (nf,) per la grandezza richiesta.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .network import Network

VSWR_CAP = 1000.0


def group_delay(freq: np.ndarray, s: np.ndarray, aperture: int = 1) -> np.ndarray:
    """Ritardo di gruppo [s] = -d(phi)/d(omega), differenza centrata su ±aperture punti."""
    n = freq.size
    if n < 2:
        return np.zeros(n)
    ph = np.unwrap(np.angle(s))
    w = 2 * np.pi * freq
    a = max(1, int(aperture))
    idx = np.arange(n)
    hi = np.clip(idx + a, 0, n - 1)
    lo = np.clip(idx - a, 0, n - 1)
    return -(ph[hi] - ph[lo]) / (w[hi] - w[lo])


def _db(x: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return 20 * np.log10(np.maximum(np.abs(x), 1e-30))


def vswr_from_gamma(g: np.ndarray) -> np.ndarray:
    m = np.abs(g)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (1 + m) / (1 - m)
    v = np.where(m < 1, v, VSWR_CAP)
    return np.minimum(v, VSWR_CAP)


def _zin(net: Network, i: int, j: int) -> np.ndarray:
    """Impedenza: da S_ii per i==j (altre porte adattate), altrimenti elemento Z_ij."""
    if i == j:
        z0 = net.z0[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            return z0 * (1 + net.s[:, i, i]) / (1 - net.s[:, i, i])
    return net.z[:, i, j]


def _yin(net: Network, i: int, j: int) -> np.ndarray:
    if i == j:
        z0 = net.z0[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            return (1 - net.s[:, i, i]) / (1 + net.s[:, i, i]) / z0
    return net.y[:, i, j]


# kind -> (etichetta, unità asse y, funzione(net, i, j, opts) -> float array)
TraceFn = Callable[[Network, int, int, dict], np.ndarray]

KINDS: dict[str, tuple[str, str, TraceFn]] = {
    "db": ("Magnitudine [dB]", "dB", lambda n, i, j, o: _db(n.s[:, i, j])),
    "mag": ("Magnitudine (lineare)", "", lambda n, i, j, o: np.abs(n.s[:, i, j])),
    "phase": ("Fase [°]", "°", lambda n, i, j, o: np.degrees(np.angle(n.s[:, i, j]))),
    "phase_unwrap": ("Fase unwrapped [°]", "°",
                     lambda n, i, j, o: np.degrees(np.unwrap(np.angle(n.s[:, i, j])))),
    "gd": ("Ritardo di gruppo [ns]", "ns",
           lambda n, i, j, o: group_delay(n.freq, n.s[:, i, j], o.get("gd_aperture", 1)) * 1e9),
    "re": ("Parte reale", "", lambda n, i, j, o: n.s[:, i, j].real),
    "im": ("Parte immaginaria", "", lambda n, i, j, o: n.s[:, i, j].imag),
    "vswr": ("VSWR", "", lambda n, i, j, o: vswr_from_gamma(n.s[:, i, j])),
    "rl": ("Return loss [dB]", "dB", lambda n, i, j, o: -_db(n.s[:, i, j])),
    "il": ("Insertion loss [dB]", "dB", lambda n, i, j, o: -_db(n.s[:, i, j])),
    "z_re": ("Z: parte reale [Ω]", "Ω", lambda n, i, j, o: _zin(n, i, j).real),
    "z_im": ("Z: parte immaginaria [Ω]", "Ω", lambda n, i, j, o: _zin(n, i, j).imag),
    "z_mag": ("|Z| [Ω]", "Ω", lambda n, i, j, o: np.abs(_zin(n, i, j))),
    "y_re": ("Y: parte reale [mS]", "mS", lambda n, i, j, o: _yin(n, i, j).real * 1e3),
    "y_im": ("Y: parte immaginaria [mS]", "mS", lambda n, i, j, o: _yin(n, i, j).imag * 1e3),
    "y_mag": ("|Y| [mS]", "mS", lambda n, i, j, o: np.abs(_yin(n, i, j)) * 1e3),
    "ls": ("L serie [nH]", "nH",
           lambda n, i, j, o: _zin(n, i, j).imag / (2 * np.pi * n.freq) * 1e9),
    "cs": ("C serie [pF]", "pF",
           lambda n, i, j, o: -1.0 / (2 * np.pi * n.freq * _zin(n, i, j).imag) * 1e12),
    "cp": ("C parallelo [pF]", "pF",
           lambda n, i, j, o: _yin(n, i, j).imag / (2 * np.pi * n.freq) * 1e12),
    "lp": ("L parallelo [nH]", "nH",
           lambda n, i, j, o: -1.0 / (2 * np.pi * n.freq * _yin(n, i, j).imag) * 1e9),
    "val": ("Valore (tracce derivate)", "", lambda n, i, j, o: n.s[:, i, j].real),
    "q": ("Q = |X|/R", "",
          lambda n, i, j, o: np.abs(_zin(n, i, j).imag) / np.maximum(np.abs(_zin(n, i, j).real), 1e-12)),
}

KIND_ORDER = ["db", "mag", "phase", "phase_unwrap", "gd", "re", "im", "vswr", "rl", "il",
              "z_re", "z_im", "z_mag", "y_re", "y_im", "y_mag", "ls", "cs", "cp", "lp", "q", "val"]


def trace_values(net: Network, i: int, j: int, kind: str, **opts) -> np.ndarray:
    if kind not in KINDS:
        raise KeyError(kind)
    with np.errstate(all="ignore"):
        y = np.asarray(KINDS[kind][2](net, i, j, opts), dtype=float)
    y = np.where(np.isfinite(y), y, np.nan)
    return y


def kind_label(kind: str) -> str:
    return KINDS[kind][0]


def complex_trace(net: Network, i: int, j: int) -> np.ndarray:
    return net.s[:, i, j]
