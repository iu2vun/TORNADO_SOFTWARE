"""Trigger software (l'hardware fx2lafw non ha trigger in-device)."""
from __future__ import annotations

import numpy as np

from .device import Trigger


def find_trigger(data: np.ndarray, trig: Trigger, start: int = 0, stop: int | None = None) -> int | None:
    """Primo indice i in [start, stop) che soddisfa il trigger, oppure None.

    Per i fronti, i è il primo campione col nuovo livello (serve il campione i-1,
    quindi non si può innescare sull'indice 0).
    """
    n = len(data)
    stop = n if stop is None else min(stop, n)
    start = max(int(start), 0)
    if stop <= start:
        return None
    bits = ((data >> trig.channel) & 1).astype(np.int8)

    if trig.mode in ("high", "low"):
        want = 1 if trig.mode == "high" else 0
        idx = np.flatnonzero(bits[start:stop] == want)
        return int(idx[0]) + start if idx.size else None

    seg_start = max(start - 1, 0)
    d = np.diff(bits[seg_start:stop])
    if trig.mode == "rising":
        cond = d > 0
    elif trig.mode == "falling":
        cond = d < 0
    else:
        cond = d != 0
    idx = np.flatnonzero(cond) + 1 + seg_start
    idx = idx[idx >= max(start, 1)]
    return int(idx[0]) if idx.size else None


def find_triggers(data: np.ndarray, trig: Trigger, start: int, stop: int) -> np.ndarray:
    """Tutti gli indici in [start, stop) che soddisfano il trigger (stessa convenzione di find_trigger)."""
    n = len(data)
    stop = min(int(stop), n)
    start = max(int(start), 0)
    if stop <= start:
        return np.empty(0, dtype=np.int64)
    bits = ((data >> trig.channel) & 1).astype(np.int8)
    if trig.mode in ("high", "low"):
        return np.flatnonzero(bits[start:stop] == (1 if trig.mode == "high" else 0)) + start
    s0 = max(start - 1, 0)
    d = np.diff(bits[s0:stop])
    cond = d > 0 if trig.mode == "rising" else d < 0 if trig.mode == "falling" else d != 0
    idx = np.flatnonzero(cond) + 1 + s0
    return idx[idx >= max(start, 1)]
