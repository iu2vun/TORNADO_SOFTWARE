"""Trasformata nel dominio del tempo (lowpass impulso/gradino, bandpass) e time gating."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..core.network import Network

WINDOWS = ("rettangolare", "hann", "hamming", "blackman", "kaiser", "flattop")

GATE_SHAPES = {"minimo": 0.05, "normale": 0.25, "largo": 0.5, "massimo": 1.0}


def make_window(name: str, n: int, beta: float = 6.0) -> np.ndarray:
    name = name.lower()
    if n <= 1:
        return np.ones(max(n, 1))
    if name.startswith("rett"):
        return np.ones(n)
    if name == "hann":
        return np.hanning(n)
    if name == "hamming":
        return np.hamming(n)
    if name == "blackman":
        return np.blackman(n)
    if name == "kaiser":
        return np.kaiser(n, beta)
    if name == "flattop":
        x = np.linspace(0, 2 * np.pi, n)
        return (0.21557895 - 0.41663158 * np.cos(x) + 0.277263158 * np.cos(2 * x)
                - 0.083578947 * np.cos(3 * x) + 0.006947368 * np.cos(4 * x))
    raise ValueError(f"finestra sconosciuta: {name}")


def to_uniform(freq: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Se la griglia non è uniforme, ricampiona linearmente (n punti equispaziati)."""
    if freq.size < 3:
        return freq, s
    d = np.diff(freq)
    if np.all(np.abs(d - d.mean()) <= 1e-6 * d.mean()):
        return freq, s
    fu = np.linspace(freq[0], freq[-1], freq.size)
    return fu, np.interp(fu, freq, s.real) + 1j * np.interp(fu, freq, s.imag)


@dataclass
class TimeResult:
    t: np.ndarray            # secondi, crescente (da -T/2 a T/2)
    y: np.ndarray            # reale (lowpass) o complesso (bandpass)
    mode: str
    response: str
    dt: float
    span: float
    resolution: float        # risoluzione approssimata (s) ~ 1/BW * fattore finestra


def _dc_value(freq: np.ndarray, s: np.ndarray, dc: str | complex) -> complex:
    if isinstance(dc, (complex, float, int)):
        return complex(dc)
    if dc == "zero":
        return 0j
    if dc == "open":
        return 1 + 0j
    if dc == "short":
        return -1 + 0j
    # auto: estrapolazione lineare di Re(S) verso f=0 dai primi punti, limitata
    k = min(6, s.size)
    if k < 2:
        return complex(s[0].real)
    c = np.polyfit(freq[:k], s[:k].real, 1)
    v = float(np.polyval(c, 0.0))
    return complex(min(max(v, -1.5), 1.5))


def transform(freq: np.ndarray, s: np.ndarray, mode: str = "lowpass", response: str = "impulse",
              window: str = "kaiser", beta: float = 6.0, pad: int = 4,
              dc: str | complex = "auto") -> TimeResult:
    """Trasformata di una traccia complessa S(f) nel dominio del tempo.

    mode 'lowpass': griglia armonica da DC (spettro hermitiano) -> risposta reale; response
    'impulse' (normalizzata al picco: un riflesso Γ0 ritardato appare con altezza ~Γ0) o 'step'
    (valore finale = S(DC)).
    mode 'bandpass': solo la banda misurata -> inviluppo complesso dell'impulso (|y| = ampiezza).
    """
    freq = np.asarray(freq, float)
    s = np.asarray(s, complex)
    if freq.size < 4:
        raise ValueError("servono almeno 4 punti")
    freq, s = to_uniform(freq, s)
    df = (freq[-1] - freq[0]) / (freq.size - 1)
    pad = max(1, int(pad))
    if mode == "bandpass":
        n = freq.size
        w = make_window(window, n, beta)
        nfft = int(2 ** np.ceil(np.log2(n * pad)))
        x = np.fft.ifft(s * w, nfft)
        x *= nfft / np.sum(w)
        t = np.fft.fftfreq(nfft, d=df)
        order = np.argsort(t)
        dt = 1.0 / (nfft * df)
        bw = freq[-1] - freq[0]
        return TimeResult(t[order], x[order], "bandpass", "impulse", dt, 1.0 / df, 1.0 / bw * _win_width(window, beta))
    if mode != "lowpass":
        raise ValueError("mode deve essere 'lowpass' o 'bandpass'")
    kmax = int(np.floor(freq[-1] / df + 1e-9))
    if kmax < 2:
        raise ValueError("banda troppo stretta per la modalità lowpass (frequenza iniziale vicino a DC richiesta)")
    fk = np.arange(0, kmax + 1) * df
    sk = np.empty(kmax + 1, complex)
    inb = fk >= freq[0] - 1e-9 * df
    sk[inb] = np.interp(fk[inb], freq, s.real) + 1j * np.interp(fk[inb], freq, s.imag)
    dcv = _dc_value(freq, s, dc)
    sk[0] = dcv
    below = np.flatnonzero(~inb)
    if below.size > 1:
        first = below[-1] + 1
        a = np.arange(1, first)
        sk[a] = dcv + (sk[first] - dcv) * (fk[a] / fk[first])
    # finestra monolatera: meta destra di una finestra simmetrica di lunghezza 2K+1
    wfull = make_window(window, 2 * kmax + 1, beta)
    w = wfull[kmax:]
    spec = sk * w
    kpad = kmax * pad
    sp = np.zeros(kpad + 1, complex)
    sp[:kmax + 1] = spec
    nfft = 2 * kpad
    x = np.fft.irfft(sp, n=nfft)
    dt = 1.0 / (nfft * df)
    t = np.arange(nfft) * dt
    t = np.where(t >= nfft * dt / 2, t - nfft * dt, t)
    order = np.argsort(t)
    if response == "step":
        y = np.cumsum(x[order])    # integrale a partire da -T/2: valore finale = S(DC)
    elif response == "impulse":
        scale = nfft / (w[0] + 2 * np.sum(w[1:]))
        y = (x * scale)[order]
    else:
        raise ValueError("response deve essere 'impulse' o 'step'")
    return TimeResult(t[order], y, "lowpass", response, dt, 1.0 / df, 1.0 / (2 * freq[-1]) * _win_width(window, beta))


def _win_width(name: str, beta: float) -> float:
    n = name.lower()
    if n.startswith("rett"):
        return 0.89
    if n == "hann":
        return 1.44
    if n == "hamming":
        return 1.30
    if n == "blackman":
        return 1.68
    if n == "kaiser":
        return 0.89 + 0.1 * beta
    return 3.8


# ------------------------------------------------------------------------ gating
def gate_function(t: np.ndarray, t_start: float, t_stop: float, shape: str | float = "normale",
                  notch: bool = False) -> np.ndarray:
    """Gate con transizioni a coseno rialzato centrate su t_start/t_stop (50% in quei punti)."""
    if t_stop <= t_start:
        raise ValueError("t_stop deve essere > t_start")
    span = t_stop - t_start
    frac = GATE_SHAPES[shape] if isinstance(shape, str) else float(shape)
    wt = max(frac * span, 1e-18)

    def edge(x):
        u = np.clip((x + wt / 2) / wt, 0.0, 1.0)
        return 0.5 - 0.5 * np.cos(np.pi * u)

    g = edge(t - t_start) * edge(t_stop - t)
    return 1.0 - g if notch else g


def gate_spectrum(freq: np.ndarray, s: np.ndarray, t_start: float, t_stop: float,
                  shape: str | float = "normale", notch: bool = False, pad: int = 4) -> np.ndarray:
    """Applica un gate temporale a S(f) (modalità bandpass, trasformata esattamente invertibile)."""
    freq = np.asarray(freq, float)
    s = np.asarray(s, complex)
    fu, su = to_uniform(freq, s)
    n = fu.size
    df = (fu[-1] - fu[0]) / (n - 1)
    nfft = int(2 ** np.ceil(np.log2(n * max(1, pad))))
    sp = np.zeros(nfft, complex)
    sp[:n] = su
    x = np.fft.ifft(sp)
    t = np.fft.fftfreq(nfft, d=df)
    g = gate_function(t, t_start, t_stop, shape, notch)
    y = np.fft.fft(x * g)[:n]
    if fu is not freq and not np.array_equal(fu, freq):
        y = np.interp(freq, fu, y.real) + 1j * np.interp(freq, fu, y.imag)
    return y


def gate_network(net: Network, cells: list[tuple[int, int]], t_start: float, t_stop: float,
                 shape: str | float = "normale", notch: bool = False, pad: int = 4) -> Network:
    s = net.s.copy()
    for (i, j) in cells:
        s[:, i, j] = gate_spectrum(net.freq, net.s[:, i, j], t_start, t_stop, shape, notch, pad)
    return net.copy(s=s, name=net.name + "_gated")


def time_to_distance(t: np.ndarray | float, velocity_factor: float = 1.0, round_trip: bool = True):
    c = 299792458.0 * velocity_factor
    return np.asarray(t) * c / (2.0 if round_trip else 1.0)


def distance_to_time(d: np.ndarray | float, velocity_factor: float = 1.0, round_trip: bool = True):
    c = 299792458.0 * velocity_factor
    return np.asarray(d) * (2.0 if round_trip else 1.0) / c
