"""Formato interno dei campioni.

Un byte per campione, bit i = canale i (CH1 = bit 0 ... CH8 = bit 7).
La mappatura etichette-PCB -> bit va verificata sull'hardware reale.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

N_CHANNELS = 8


def default_names() -> list[str]:
    return [f"CH{i + 1}" for i in range(N_CHANNELS)]


@dataclass
class Capture:
    samplerate: float
    data: np.ndarray
    channel_names: list[str] = field(default_factory=default_names)
    trigger_index: int | None = None
    source: str = ""
    trigger_channel: int | None = None   # canale e modo del trigger che ha prodotto trigger_index
    trigger_mode: str | None = None      # 'rising' | 'falling' | 'either' | 'high' | 'low'
    meta: dict = field(default_factory=dict)   # diagnostica dell'acquisizione (durata, tentativi...); non viene salvata

    def __post_init__(self) -> None:
        self.data = np.ascontiguousarray(self.data, dtype=np.uint8)
        if self.data.ndim != 1:
            raise ValueError("data deve essere 1-D")
        if self.samplerate <= 0:
            raise ValueError("samplerate deve essere > 0")
        if len(self.channel_names) != N_CHANNELS:
            raise ValueError(f"servono {N_CHANNELS} nomi di canale")

    def __len__(self) -> int:
        return int(self.data.size)

    @property
    def duration(self) -> float:
        return len(self) / self.samplerate

    @property
    def trigger_time(self) -> float | None:
        if self.trigger_index is None:
            return None
        return self.trigger_index / self.samplerate

    def time_of(self, index):
        """Istante (s) dall'inizio della cattura; accetta indice o array di indici."""
        return np.asarray(index) / self.samplerate

    @staticmethod
    def _check_channel(ch: int) -> None:
        if not 0 <= ch < N_CHANNELS:
            raise ValueError(f"canale {ch} fuori range 0..{N_CHANNELS - 1}")

    def channel(self, ch: int) -> np.ndarray:
        """Livelli del canale come array bool."""
        self._check_channel(ch)
        return ((self.data >> ch) & 1).astype(bool)

    def edges(self, ch: int, rising: bool = True, falling: bool = True) -> np.ndarray:
        """Indici dei campioni in cui il canale cambia livello (primo campione col nuovo livello)."""
        d = np.diff(self.channel(ch).astype(np.int8))
        mask = np.zeros(d.shape, dtype=bool)
        if rising:
            mask |= d > 0
        if falling:
            mask |= d < 0
        return np.flatnonzero(mask) + 1

    def slice(self, start: int, stop: int) -> "Capture":
        """Sotto-cattura [start, stop) con trigger_index riallineato."""
        start = max(0, int(start))
        stop = min(len(self), int(stop))
        if stop <= start:
            raise ValueError("intervallo vuoto")
        trig = self.trigger_index
        if trig is not None:
            trig = trig - start if start <= trig < stop else None
        return Capture(self.samplerate, self.data[start:stop].copy(),
                       list(self.channel_names), trig, self.source,
                       self.trigger_channel if trig is not None else None,
                       self.trigger_mode if trig is not None else None)
