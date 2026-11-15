"""Misure temporali su un canale: frequenza, periodo, duty, larghezze impulso, glitch."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .capture import Capture


@dataclass
class ChannelStats:
    channel: int
    n_rising: int
    n_falling: int
    frequency: float | None = None      # Hz, da mediana dei periodi
    period: float | None = None         # s, mediana
    period_std: float | None = None     # s
    period_pp: float | None = None      # s, max - min (jitter picco-picco)
    duty: float | None = None           # 0..1, mediana sui cicli completi
    high_min: float | None = None       # s, impulsi alti completi
    high_max: float | None = None
    low_min: float | None = None        # s, impulsi bassi completi
    low_max: float | None = None
    resolution: float = 0.0             # s, 1 / samplerate (incertezza di quantizzazione)


@dataclass
class Glitch:
    index: int
    width: float   # s
    level: int     # livello dell'impulso breve (1 = impulso alto)


def pulse_widths(cap: Capture, ch: int) -> tuple[np.ndarray, np.ndarray]:
    """Larghezze (s) degli impulsi alti e bassi *completi* (delimitati da due fronti)."""
    edges = cap.edges(ch)
    if edges.size < 2:
        return np.empty(0), np.empty(0)
    lengths = np.diff(edges) / cap.samplerate
    level = cap.channel(ch)[edges[:-1]]
    return lengths[level], lengths[~level]


def measure_channel(cap: Capture, ch: int) -> ChannelStats:
    fs = cap.samplerate
    rise = cap.edges(ch, falling=False)
    fall = cap.edges(ch, rising=False)
    st = ChannelStats(ch, int(rise.size), int(fall.size), resolution=1.0 / fs)

    hi, lo = pulse_widths(cap, ch)
    if hi.size:
        st.high_min, st.high_max = float(hi.min()), float(hi.max())
    if lo.size:
        st.low_min, st.low_max = float(lo.min()), float(lo.max())

    if rise.size >= 2:
        periods = np.diff(rise) / fs
        st.period = float(np.median(periods))
        st.frequency = 1.0 / st.period
        st.period_std = float(periods.std())
        st.period_pp = float(periods.max() - periods.min())
        # duty: per ogni ciclo completo il primo fronte di discesa tra due salite
        j = np.searchsorted(fall, rise[:-1])
        ok = j < fall.size
        ok[ok] &= fall[j[ok]] < rise[1:][ok]
        if ok.any():
            high = (fall[j[ok]] - rise[:-1][ok]) / fs
            st.duty = float(np.median(high / periods[ok]))
    return st


def find_glitches(cap: Capture, ch: int, max_width_s: float) -> list[Glitch]:
    """Impulsi completi (alti o bassi) più brevi di max_width_s."""
    edges = cap.edges(ch)
    if edges.size < 2:
        return []
    widths = np.diff(edges) / cap.samplerate
    level = cap.channel(ch)[edges[:-1]]
    sel = np.flatnonzero(widths < max_width_s)
    return [Glitch(int(edges[k]), float(widths[k]), int(level[k])) for k in sel]
