"""Rilevamento automatico del protocollo per canale."""
from __future__ import annotations

from collections import deque
from typing import Optional

from ..chunk import Chunk
from .base import Decoder, Message

PRIORITY = ("ubx", "mavlink", "sbus", "modbus", "modbus_ascii", "nmea", "grbl", "at")
GOOD_KINDS = {
    "modbus": {"request", "response", "exception"}, "modbus_ascii": {"request", "response", "exception"},
    "nmea": {"sentence"}, "at": {"command", "echo", "final"}, "ubx": {"message", "ack"}, "mavlink": {"message"},
    "sbus": {"frame"}, "grbl": {"command", "response", "status", "alarm", "welcome"},
}
WINDOW = 24       # ultimi messaggi valutati per candidato
MIN_GOOD = 3      # messaggi validi necessari per assegnare un protocollo
MIN_RATIO = 0.6   # quota minima di messaggi validi nella finestra
BUFFER = 64       # chunk trattenuti per canale finché il protocollo non è noto


def is_good(name: str, m: Message) -> bool:
    """Un messaggio è un indizio a favore del protocollo solo se è valido e specifico di quel protocollo."""
    if not m.ok:
        return False
    if name == "grbl" and m.kind == "response" and m.pair_ns is None:
        return False  # un "ok" isolato non basta: serve un comando G-code/$ in attesa
    if name == "at" and m.kind in ("info", "urc"):
        return bool(m.fields) and m.fields[0][1].startswith("+")  # etichetta +XXX: le altre (#, $, ^) sono ambigue
    kinds = GOOD_KINDS.get(name)
    return m.kind in kinds if kinds else True


class _Chan:
    def __init__(self, trials: dict[str, Decoder]) -> None:
        self.trials = trials
        self.window = {n: deque(maxlen=WINDOW) for n in trials}
        self.buf: deque[Chunk] = deque(maxlen=BUFFER)
        self.locked: Optional[str] = None


class AutoDetect(Decoder):
    """Assegna a ogni canale il protocollo che produce più messaggi validi (Modbus RTU/ASCII, NMEA, AT, UBX, MAVLink,
    SBUS, GRBL). Un solo protocollo per canale: su flussi misti (UBX + NMEA sulla stessa porta) vince il primo che
    raggiunge la soglia; per gli altri usare viste con protocollo scelto a mano.

    - Finché il protocollo di un canale non è noto, i suoi dati sono trattenuti e non producono righe.
    - Dopo almeno 3 messaggi validi (e almeno il 60% di validi nella finestra recente) il canale viene assegnato e i
      dati trattenuti vengono decodificati, quindi non si perde nulla.
    - L'assegnazione resta fissa fino al reset: errori sporadici (un CRC rotto) non la cambiano.
    - Un'istanza di decoder per protocollo è condivisa fra i canali assegnati, così richieste e risposte che passano
      da adattatori diversi restano accoppiate."""

    name = "auto"
    label = "Automatico (per canale)"
    options = {"masters": "Modbus: flussi (canale,dir) da cui parte il master",
               "candidates": "nomi dei decoder candidati (default: tutti quelli integrati)"}

    def __init__(self, masters: Optional[set] = None, candidates: Optional[tuple[str, ...]] = None) -> None:
        self.masters = set(masters) if masters else None
        self.candidates = tuple(candidates) if candidates else PRIORITY
        self._chans: dict[int, _Chan] = {}
        self._real: dict[str, Decoder] = {}

    # ----------------------------------------------------------------- stato
    def _make(self, name: str) -> Decoder:
        from . import DECODERS  # import tardivo: il registro contiene anche i plugin

        cls = DECODERS[name]
        if name in ("modbus", "modbus_ascii") and self.masters:
            return cls(masters=self.masters)
        return cls()

    def reset(self) -> None:
        self._chans.clear()
        self._real.clear()

    def assignments(self) -> dict[int, Optional[str]]:
        return {ch: c.locked for ch, c in sorted(self._chans.items())}

    # ----------------------------------------------------------------- elaborazione
    def feed(self, chunk: Chunk) -> list[Message]:
        st = self._chans.get(chunk.channel)
        if st is None:
            st = self._chans[chunk.channel] = _Chan({n: self._make(n) for n in self.candidates})
        if st.locked:
            return self._real[st.locked].feed(chunk)
        st.buf.append(chunk)
        for name, trial in st.trials.items():
            for m in trial.feed(chunk):
                st.window[name].append(is_good(name, m))
        best, best_good = None, 0
        for name in self.candidates:
            w = st.window[name]
            good = sum(w)
            if good >= MIN_GOOD and good / len(w) >= MIN_RATIO and good > best_good:
                best, best_good = name, good
        if best is None:
            return []
        st.locked = best
        if best not in self._real:
            self._real[best] = self._make(best)
        out: list[Message] = []
        for c in st.buf:
            out += self._real[best].feed(c)
        st.buf.clear()
        st.trials.clear()
        st.window.clear()
        return out

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        out: list[Message] = []
        for dec in self._real.values():
            out += dec.flush(now_ns)
        return out
