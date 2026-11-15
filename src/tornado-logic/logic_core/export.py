"""Salvataggio/caricamento catture (.npz) ed esportazione CSV / VCD (solo cambi di livello)."""
from __future__ import annotations

import re

import numpy as np

from .capture import N_CHANNELS, Capture


def save_npz(cap: Capture, path: str) -> None:
    np.savez_compressed(path, data=cap.data, samplerate=np.float64(cap.samplerate),
                        names=np.array(cap.channel_names), source=np.array(cap.source),
                        trigger_index=np.int64(-1 if cap.trigger_index is None else cap.trigger_index),
                        trigger_channel=np.int64(-1 if cap.trigger_channel is None else cap.trigger_channel),
                        trigger_mode=np.array(cap.trigger_mode or ""))


def load_npz(path: str) -> Capture:
    with np.load(path, allow_pickle=False) as z:
        missing = {"data", "samplerate"} - set(z.files)
        if missing:
            raise ValueError(f"file non valido: mancano {sorted(missing)}")
        names = [str(x) for x in z["names"]] if "names" in z.files else None
        trig = int(z["trigger_index"]) if "trigger_index" in z.files else -1
        kw = {"channel_names": names} if names and len(names) == N_CHANNELS else {}
        tch = int(z["trigger_channel"]) if "trigger_channel" in z.files else -1
        tmode = str(z["trigger_mode"]) if "trigger_mode" in z.files else ""
        return Capture(float(z["samplerate"]), z["data"], trigger_index=None if trig < 0 else trig,
                       source=str(z["source"]) if "source" in z.files else "",
                       trigger_channel=tch if 0 <= tch < N_CHANNELS else None, trigger_mode=tmode or None, **kw)


def _channels(channels) -> list[int]:
    ch = list(range(N_CHANNELS)) if channels is None else [int(c) for c in channels]
    if not ch or any(not 0 <= c < N_CHANNELS for c in ch):
        raise ValueError("elenco canali non valido")
    return ch


def _change_indices(cap: Capture, mask: int) -> np.ndarray:
    v = cap.data & np.uint8(mask)
    return np.flatnonzero(v[1:] != v[:-1]) + 1


def export_csv(cap: Capture, path: str, channels=None) -> int:
    """Una riga per ogni cambio di livello (più la riga iniziale e quella finale). Ritorna il numero di righe."""
    ch = _channels(channels)
    mask = sum(1 << c for c in ch)
    idx = np.unique(np.concatenate(([0], _change_indices(cap, mask), [len(cap) - 1])))
    cols = [np.rint(idx / cap.samplerate * 1e12) / 1e12]  # tempo in s, risoluzione 1 ps
    cols += [((cap.data[idx] >> c) & 1).astype(np.float64) for c in ch]
    names = [cap.channel_names[c].replace(",", "_") for c in ch]
    np.savetxt(path, np.column_stack(cols), delimiter=",", header=",".join(["tempo_s", *names]),
               comments="", fmt=["%.9g"] + ["%d"] * len(ch))
    return int(idx.size)


def export_vcd(cap: Capture, path: str, channels=None) -> int:
    """VCD con timescale 1 ns. Ritorna il numero di cambi di livello scritti."""
    ch = _channels(channels)
    mask = sum(1 << c for c in ch)
    v = cap.data & np.uint8(mask)
    chg = _change_indices(cap, mask)
    t_ns = np.rint(chg * 1e9 / cap.samplerate).astype(np.int64)
    ids = {c: chr(33 + k) for k, c in enumerate(ch)}  # identificatori ASCII stampabili
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("$version Tornado Logic $end\n$timescale 1 ns $end\n$scope module logic $end\n")
        for c in ch:
            name = re.sub(r"\s+", "_", cap.channel_names[c].strip()) or f"CH{c + 1}"
            f.write(f"$var wire 1 {ids[c]} {name} $end\n")
        f.write("$upscope $end\n$enddefinitions $end\n#0\n$dumpvars\n")
        prev = int(v[0])
        for c in ch:
            f.write(f"{(prev >> c) & 1}{ids[c]}\n")
        f.write("$end\n")
        last_t = -1
        for i, t in zip(chg.tolist(), t_ns.tolist()):
            cur = int(v[i])
            t = max(t, last_t + 1)  # il VCD richiede tempi strettamente crescenti
            f.write(f"#{t}\n")
            diff = cur ^ prev
            for c in ch:
                if (diff >> c) & 1:
                    f.write(f"{(cur >> c) & 1}{ids[c]}\n")
            prev, last_t = cur, t
        f.write(f"#{int(round((len(cap) - 1) * 1e9 / cap.samplerate)) + 1}\n")
    return int(chg.size)
