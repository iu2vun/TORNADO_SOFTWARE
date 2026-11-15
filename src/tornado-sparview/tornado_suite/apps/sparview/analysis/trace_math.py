"""Matematica sulle tracce: operazioni tra tracce complesse, tracce derivate, medie."""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ..core.network import Network

BINARY_OPS = {
    "A − B": (lambda a, b: a - b, "{a} − {b}"),
    "A + B": (lambda a, b: a + b, "{a} + {b}"),
    "A × B": (lambda a, b: a * b, "{a} × {b}"),
    "A ÷ B": (lambda a, b: a / b, "{a} ÷ {b}"),
    "(A + B) / 2": (lambda a, b: 0.5 * (a + b), "({a} + {b})/2"),
}

UNARY_OPS = ("copia (memoria)", "coniugato", "1 / A", "|A| (solo modulo)", "ritardo elettrico",
             "offset di fase", "moltiplica (lineare)", "moltiplica (dB)", "smoothing")


def make_trace_network(freq: np.ndarray, values: np.ndarray, label: str, name: Optional[str] = None,
                       z0: float = 50.0, comments: Optional[list[str]] = None) -> Network:
    """Rete a 1 porta che contiene una traccia derivata; l'etichetta compare nella legenda."""
    v = np.asarray(values, complex).reshape(-1, 1, 1)
    return Network(freq, v, z0, name=name or label, custom_labels={(0, 0): label},
                   comments=comments or [], source_format="traccia derivata")


def align(a_net: Network, b_net: Network) -> tuple[np.ndarray, Network, Network]:
    """Ritorna freq comune (griglia di A ristretta alla sovrapposizione) e le due reti sulla griglia."""
    lo, hi = max(a_net.fmin, b_net.fmin), min(a_net.fmax, b_net.fmax)
    if lo >= hi:
        raise ValueError("le tracce non hanno frequenze in comune")
    a = a_net.cropped(lo, hi)
    b = b_net if (b_net.nfreq == a.nfreq and np.allclose(b_net.freq, a.freq)) else b_net.interpolated(a.freq)
    return a.freq, a, b


def smooth_complex(v: np.ndarray, n: int) -> np.ndarray:
    n = max(1, int(n))
    if n == 1:
        return v.copy()
    k = np.ones(n) / n
    pad = n // 2
    re = np.convolve(np.pad(v.real, (pad, n - 1 - pad), mode="edge"), k, mode="valid")
    im = np.convolve(np.pad(v.imag, (pad, n - 1 - pad), mode="edge"), k, mode="valid")
    return re + 1j * im


def binary_op(a_net: Network, ai: int, aj: int, b_net: Network, bi: int, bj: int, op: str) -> Network:
    if op not in BINARY_OPS:
        raise KeyError(op)
    fn, fmt = BINARY_OPS[op]
    f, a, b = align(a_net, b_net)
    with np.errstate(all="ignore"):
        v = fn(a.s[:, ai, aj], b.s[:, bi, bj])
    label = fmt.format(a=f"{a_net.name}:{a_net.label(ai, aj)}", b=f"{b_net.name}:{b_net.label(bi, bj)}")
    return make_trace_network(f, v, label, name=label, z0=a_net.z0[ai])


def unary_op(net: Network, i: int, j: int, op: str, param: float = 0.0) -> Network:
    f = net.freq
    v = net.s[:, i, j].copy()
    lab = net.label(i, j)
    if op == "copia (memoria)":
        out, label = v, f"{lab} [mem]"
    elif op == "coniugato":
        out, label = np.conj(v), f"conj({lab})"
    elif op == "1 / A":
        with np.errstate(all="ignore"):
            out = 1.0 / v
        label = f"1/{lab}"
    elif op == "|A| (solo modulo)":
        out, label = np.abs(v).astype(complex), f"|{lab}|"
    elif op == "ritardo elettrico":
        out, label = v * np.exp(-1j * 2 * np.pi * f * param * 1e-12), f"{lab} delay {param:g}ps"
    elif op == "offset di fase":
        out, label = v * np.exp(1j * np.deg2rad(param)), f"{lab} {param:+g}°"
    elif op == "moltiplica (lineare)":
        out, label = v * param, f"{lab}×{param:g}"
    elif op == "moltiplica (dB)":
        out, label = v * 10 ** (param / 20), f"{lab} {param:+g} dB"
    elif op == "smoothing":
        out, label = smooth_complex(v, int(param)), f"{lab} smooth({int(param)})"
    else:
        raise KeyError(op)
    return make_trace_network(f, out, f"{net.name}: {label}", name=f"{net.name}_{label}", z0=net.z0[i])


def average_networks(nets: Sequence[Network]) -> Network:
    if len(nets) < 2:
        raise ValueError("servono almeno 2 reti")
    base = nets[0]
    for n in nets[1:]:
        if n.nports != base.nports:
            raise ValueError("le reti devono avere lo stesso numero di porte")
    lo = max(n.fmin for n in nets)
    hi = min(n.fmax for n in nets)
    if lo >= hi:
        raise ValueError("nessuna sovrapposizione di frequenza")
    ref = base.cropped(lo, hi)
    acc = np.zeros_like(ref.s)
    for n in nets:
        acc += (n if (n.nfreq == ref.nfreq and np.allclose(n.freq, ref.freq)) else n.interpolated(ref.freq)).s
    return ref.copy(s=acc / len(nets), name=f"media({len(nets)})",
                    comments=[f"media complessa di: " + ", ".join(n.name for n in nets)])
