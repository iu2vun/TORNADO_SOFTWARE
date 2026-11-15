"""Modello dati di una rete N-porte: frequenza (nf,), S (nf, N, N), z0 (N,)."""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from . import params

_uid = itertools.count(1)


@dataclass
class NoiseData:
    freq: np.ndarray        # Hz
    nfmin_db: np.ndarray    # dB
    gamma_opt: np.ndarray   # complex
    rn: np.ndarray          # ohm (denormalizzato)


class Network:
    """Rete N-porte con parametri S su griglia di frequenza crescente."""

    def __init__(self, freq, s, z0=50.0, name: str = "", path: Optional[str] = None,
                 comments: Optional[list[str]] = None, noise: Optional[NoiseData] = None,
                 port_info: Optional[list[tuple[str, int]]] = None,
                 custom_labels: Optional[dict] = None, source_format: str = ""):
        freq = np.asarray(freq, dtype=float).ravel()
        s = np.asarray(s, dtype=complex)
        if s.ndim != 3 or s.shape[1] != s.shape[2]:
            raise ValueError("S deve avere shape (nf, N, N)")
        if s.shape[0] != freq.size:
            raise ValueError("lunghezza frequenze e S non coerenti")
        if freq.size == 0:
            raise ValueError("rete senza punti")
        if np.any(np.diff(freq) <= 0):
            order = np.argsort(freq, kind="stable")
            freq, s = freq[order], s[order]
            keep = np.concatenate(([True], np.diff(freq) > 0))
            freq, s = freq[keep], s[keep]
        n = s.shape[1]
        self.freq = freq
        self.s = s
        self.z0 = np.broadcast_to(np.asarray(z0, dtype=float), (n,)).copy()
        self.name = name or "rete"
        self.path = path
        self.comments = list(comments or [])
        self.noise = noise
        self.port_info = port_info          # es. [("d",1),("d",2),("c",1),("c",2)]
        self.custom_labels = custom_labels  # {(i,j): "testo"}
        self.source_format = source_format
        self.uid = next(_uid)

    # ------------------------------------------------------------------ info
    @property
    def nports(self) -> int:
        return self.s.shape[1]

    @property
    def nfreq(self) -> int:
        return self.freq.size

    @property
    def fmin(self) -> float:
        return float(self.freq[0])

    @property
    def fmax(self) -> float:
        return float(self.freq[-1])

    def is_uniform(self, rtol: float = 1e-6) -> bool:
        if self.nfreq < 3:
            return True
        d = np.diff(self.freq)
        return bool(np.all(np.abs(d - d.mean()) <= rtol * abs(d.mean())))

    def label(self, i: int, j: int) -> str:
        if self.custom_labels and (i, j) in self.custom_labels:
            return self.custom_labels[(i, j)]
        if self.port_info:
            mi, pi = self.port_info[i]
            mj, pj = self.port_info[j]
            return f"S{mi}{mj}{pi}{pj}"
        if self.nports <= 9:
            return f"S{i + 1}{j + 1}"
        return f"S{i + 1},{j + 1}"

    def port_label(self, i: int) -> str:
        if self.port_info:
            m, p = self.port_info[i]
            return f"{m}{p}"
        return str(i + 1)

    def sij(self, i: int, j: int) -> np.ndarray:
        return self.s[:, i, j]

    # ----------------------------------------------------------- conversioni
    def copy(self, **over) -> "Network":
        kw = dict(freq=self.freq.copy(), s=self.s.copy(), z0=self.z0.copy(), name=self.name,
                  path=self.path, comments=list(self.comments), noise=self.noise,
                  port_info=self.port_info, custom_labels=self.custom_labels,
                  source_format=self.source_format)
        kw.update(over)
        return Network(**kw)

    @property
    def z(self) -> np.ndarray:
        return params.s_to_z(self.s, self.z0)

    @property
    def y(self) -> np.ndarray:
        return params.s_to_y(self.s, self.z0)

    @property
    def abcd(self) -> np.ndarray:
        return params.s_to_abcd(self.s, self.z0)

    @property
    def t(self) -> np.ndarray:
        return params.s_to_t(self.s)

    def renormalized(self, z0_new) -> "Network":
        z0_new = np.broadcast_to(np.asarray(z0_new, float), (self.nports,))
        return self.copy(s=params.renormalize(self.s, self.z0, z0_new), z0=z0_new.copy())

    # ------------------------------------------------------- griglia freq.
    def cropped(self, fmin: Optional[float] = None, fmax: Optional[float] = None) -> "Network":
        lo = -np.inf if fmin is None else fmin
        hi = np.inf if fmax is None else fmax
        m = (self.freq >= lo) & (self.freq <= hi)
        if not m.any():
            raise ValueError("intervallo di frequenza vuoto")
        return self.copy(freq=self.freq[m], s=self.s[m])

    def interpolated(self, new_freq, allow_extrapolation: bool = False) -> "Network":
        nf = np.asarray(new_freq, float)
        if not allow_extrapolation and (nf.min() < self.fmin * (1 - 1e-12) or nf.max() > self.fmax * (1 + 1e-12)):
            raise ValueError("frequenze fuori dall'intervallo della rete")
        out = np.empty((nf.size, self.nports, self.nports), complex)
        for i in range(self.nports):
            for j in range(self.nports):
                v = self.s[:, i, j]
                out[:, i, j] = np.interp(nf, self.freq, v.real) + 1j * np.interp(nf, self.freq, v.imag)
        return self.copy(freq=nf, s=out)

    def subnetwork(self, ports: list[int]) -> "Network":
        """Sotto-matrice (le altre porte vengono ignorate, non terminate)."""
        idx = np.array(ports)
        pi = [self.port_info[k] for k in ports] if self.port_info else None
        return self.copy(s=self.s[:, idx][:, :, idx], z0=self.z0[idx], port_info=pi, noise=None,
                         custom_labels=None)

    def __repr__(self) -> str:
        return (f"Network({self.name!r}, {self.nports} porte, {self.nfreq} punti, "
                f"{self.fmin:.6g}..{self.fmax:.6g} Hz)")


def default_name(path: str | Path) -> str:
    return Path(path).stem
