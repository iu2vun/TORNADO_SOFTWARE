"""Verifica dei collegamenti con il modo "walk" del tester ESP32. Nessuna dipendenza da Qt.

Il tester alza un canale alla volta (CH1 -> CH8, ``step_s`` per canale, giro completo = 8 passi). Da una cattura
lunga almeno un giro e mezzo (20 kS/s x 6 s bastano) si stabilisce per ogni canale se il filo:
  ok           alto per un passo, nella posizione giusta del giro
  muto         mai alto (filo scollegato, in corto a massa, uscita non collegata)
  sempre alto  mai basso (in corto a 3,3 V o ingresso lasciato libero)
  corto        segue esattamente un altro canale (fili o uscite in corto tra loro)
  scambiato    alto in una posizione diversa da quella attesa (filo infilato al posto di un altro)
  incerto      attività che non è un impulso da un passo (il tester non è in modo walk?)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .capture import N_CHANNELS, Capture
from .sim_signals import WALK_STEP

OK, MUTE, STUCK_HIGH, SHORT, SWAPPED, UNSURE = "ok", "muto", "sempre alto", "corto", "scambiato", "incerto"
MIN_SECONDS = 1.5 * 8 * WALK_STEP          # durata minima della cattura: un giro e mezzo


@dataclass
class WalkResult:
    channel: int                  # indice 0..7 (CH1..CH8 nel software)
    status: str
    message: str
    slot: int | None = None       # posizione osservata nel giro (0..7)
    duration: float | None = None  # durata dell'impulso, s
    source: int | None = None     # uscita del tester (1..8) che si vede su questo canale
    partner: int | None = None    # canale in corto o scambiato

    @property
    def ok(self) -> bool:
        return self.status == OK


def _intervals(level: np.ndarray) -> list[tuple[int, int]]:
    """Intervalli alti completi [salita, discesa) in indici di campione; esclusi quelli già alti a inizio cattura
    e quelli ancora alti alla fine."""
    d = np.diff(level.astype(np.int8))
    rise = np.flatnonzero(d > 0) + 1
    fall = np.flatnonzero(d < 0) + 1
    out = []
    for r in rise:
        f = fall[fall > r]
        if f.size:
            out.append((int(r), int(f[0])))
    return out


def verify_walk(cap: Capture, step_s: float = WALK_STEP, tol: float = 0.3,
                channels: tuple[int, ...] | list[int] = tuple(range(N_CHANNELS))) -> list[WalkResult]:
    """Un WalkResult per canale richiesto. tol = scostamento ammesso della durata dell'impulso (frazione del passo)."""
    if cap.duration < 1.5 * 8 * step_s:
        raise ValueError(f"cattura troppo corta ({cap.duration:.1f} s): servono almeno {1.5 * 8 * step_s:.0f} s, "
                         f"cioè un giro e mezzo del modo walk")
    fs = cap.samplerate
    levels = {ch: cap.channel(ch) for ch in channels}
    res: dict[int, WalkResult] = {}
    good: dict[int, tuple[float, float]] = {}      # canale -> (istante della salita, durata) del primo impulso valido

    for ch, lv in levels.items():
        if not lv.any():
            res[ch] = WalkResult(ch, MUTE, "mai alto: filo scollegato, in corto a massa o uscita del tester non collegata")
            continue
        if lv.all():
            res[ch] = WalkResult(ch, STUCK_HIGH, "sempre alto: in corto a 3,3 V o ingresso lasciato libero")
            continue
        ok = [(r, f) for r, f in _intervals(lv) if abs((f - r) / fs - step_s) <= tol * step_s]
        if not ok:
            seen = _intervals(lv)
            dur = f" (durata {(seen[0][1] - seen[0][0]) / fs * 1e3:.1f} ms)" if seen else ""
            res[ch] = WalkResult(ch, UNSURE, f"attività diversa da un impulso di {step_s * 1e3:.0f} ms{dur}: "
                                             f"il tester è in modo walk? (digitare: mode walk)")
            continue
        r, f = ok[0]
        good[ch] = (r / fs, (f - r) / fs)

    # fili che si comportano come un altro canale (alto negli stessi istanti, per tutta la cattura)
    active = [ch for ch in levels if ch in good]
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if np.mean(levels[a] == levels[b]) >= 0.999 and (levels[a] != levels[b]).sum() <= 2:
                for x, y in ((a, b), (b, a)):
                    res[x] = WalkResult(x, SHORT, f"identico a CH{y + 1}: i due fili sono in corto tra loro "
                                                  f"(o due uscite del tester sono unite)", partner=y)
                good.pop(a, None)
                good.pop(b, None)

    # posizione nel giro: fase di riferimento = prima salita valida; offset = valore più frequente di (slot - canale)
    if good:
        ref = min(good.values(), key=lambda v: v[0])[0]
        slots = {ch: int(round((t - ref) / step_s)) % 8 for ch, (t, _) in good.items()}
        diffs = [(s - ch) % 8 for ch, s in slots.items() if ch in good]
        counts = np.bincount(diffs, minlength=8)
        offset = int(np.argmax(counts))
        for ch, (t, dur) in good.items():
            src = (slots[ch] - offset) % 8 + 1            # uscita del tester che pilota questo canale
            if src == ch + 1:
                res[ch] = WalkResult(ch, OK, f"alto per {dur * 1e3:.0f} ms, posizione {ch + 1} di 8",
                                     slot=slots[ch], duration=dur, source=src)
            else:
                res[ch] = WalkResult(ch, SWAPPED, f"riceve l'uscita {src} del tester (atteso {ch + 1}): fili scambiati",
                                     slot=slots[ch], duration=dur, source=src)
        # compagni di scambio: CHx riceve l'uscita y e CHy riceve l'uscita x
        for ch, r in res.items():
            if r.status == SWAPPED and r.source is not None:
                other = res.get(r.source - 1)
                if other is not None and other.status == SWAPPED and other.source == ch + 1:
                    r.partner = r.source - 1
                    r.message = f"scambiato con CH{r.source}: riceve l'uscita {r.source} del tester (atteso {ch + 1})"
    return [res[ch] for ch in levels]


def walk_summary(results: list[WalkResult]) -> tuple[bool, str]:
    """(tutto ok, riga di sintesi)."""
    bad = [r for r in results if not r.ok]
    if not bad:
        return True, f"Collegamenti corretti: {len(results)} canali su {len(results)}."
    if sum(r.status == UNSURE for r in results) >= max(3, len(results) // 2):
        return False, "Il tester non sembra in modo walk: nella console dell'ESP32 digita «mode walk» e riprova."
    names = ", ".join(f"CH{r.channel + 1} ({r.status})" for r in bad)
    return False, f"{len(bad)} canali da controllare: {names}."
