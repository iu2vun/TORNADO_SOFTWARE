"""Analizzatore RS-232 / RS-485 su cattura logica.

Livello fisico: baud e formato rilevati automaticamente, errore di baud e jitter di bit.
Livello bus: raggruppamento dei byte in messaggi per pausa, direzione TX/RX (da due linee RS-232
oppure dal segnale DE), tempo di risposta (turnaround), anticipo/ritardo di DE, bus non pilotato (A = B).

Collegamento: l'analizzatore vede solo livelli TTL. RS-232 va passato da un convertitore (MAX3232 o simile)
che ridà TTL a riposo alto; RS-485 si legge dall'uscita RO di un transceiver, oppure da A e B (due canali).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .annotate import Annotation, Lane, uart_annotations
from .capture import Capture
from .decoders import UartFrame, decode_uart

STANDARD_BAUDS = (300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800, 115200,
                  230400, 250000, 460800, 500000, 921600, 1000000, 1500000, 2000000, 3000000)

# Formati provati dal rilevamento automatico, in ordine di probabilità (usato per gli spareggi).
# 7E1 si decodifica sempre senza errori anche come 8O1 (7O1 come 8E1): il bit di parità diventa il bit 7 e la
# parità dell'8-bit è sempre soddisfatta. Il contrario non vale, quindi i formati a 7 bit vanno provati prima.
FORMATS = ((8, "N", 1), (7, "E", 1), (7, "O", 1), (8, "E", 1), (8, "O", 1), (8, "N", 2), (7, "N", 1),
           (7, "E", 2), (7, "O", 2), (8, "E", 2), (8, "O", 2), (7, "N", 2), (9, "N", 1))

MODES = ("rs232", "rs485", "rs485_ab")


@dataclass
class SerialCfg:
    enabled: bool = False
    mode: str = "rs232"                 # 'rs232' | 'rs485' (uscita RO) | 'rs485_ab' (A e B su due canali)
    ch_tx: int | None = 0               # RS-232: linea TX
    ch_rx: int | None = 1               # RS-232: linea RX
    ch_ro: int = 2                      # RS-485: uscita RO del transceiver
    ch_a: int = 4                       # RS-485 A/B
    ch_b: int = 5
    swap_ab: bool = False               # le sigle A/B dei costruttori non sono coerenti
    ch_de: int | None = None            # RS-485: segnale DE (direzione)
    de_active_high: bool = True
    baud: float | None = None           # None = automatico
    fmt: tuple[int, str, int] | None = None   # (bit dati, parità, stop); None = automatico
    invert: bool = False                # linea invertita (RS-232 senza convertitore)
    gap_chars: float = 3.5              # pausa (in tempi di carattere) che chiude un messaggio
    modbus: bool = False                # interpreta i messaggi come frame Modbus RTU (CRC, funzioni, abbinamento)


# ================================ stima baud ==================================
@dataclass
class BaudEstimate:
    baud: float                 # misurato
    nominal: float | None       # baud standard più vicino (entro 5 %)
    error_pct: float | None     # scostamento dal nominale
    bit_time: float             # s
    n_runs: int                 # impulsi usati nella stima


def _head_edges(cap: Capture, ch: int, min_edges: int) -> np.ndarray:
    """Indici dei primi `min_edges` fronti del canale (analisi limitata all'attività iniziale)."""
    n = len(cap)
    win = min(n, 1_000_000)
    while True:
        bits = (cap.data[:win] >> ch) & 1
        idx = np.flatnonzero(bits[1:] != bits[:-1]) + 1
        if idx.size >= min_edges or win >= n:
            return idx[:min_edges]
        win = min(n, win * 4)


def _refine(runs: np.ndarray, t0: float, max_bits: int = 9, tol: float = 0.25):
    """Affina il tempo di bit T: ogni impulso dentro un frame vale n*T. Ritorna (T, n usati, residui)."""
    T, used = t0, 0
    for _ in range(3):
        n = np.rint(runs / T)
        ok = (n >= 1) & (n <= max_bits) & (np.abs(runs - n * T) <= tol * T)
        used = int(ok.sum())
        if used < 3:
            return T, used, np.empty(0)
        k = n[ok]
        T = float((runs[ok] * k).sum() / (k * k).sum())
    n = np.rint(runs / T)
    ok = (n >= 1) & (n <= max_bits) & (np.abs(runs - n * T) <= tol * T)
    return T, int(ok.sum()), runs[ok] - n[ok] * T


def estimate_baud(cap: Capture, ch: int) -> BaudEstimate | None:
    edges = _head_edges(cap, ch, 4000)
    if edges.size < 5:
        return None
    runs = np.diff(edges).astype(np.float64)
    s = np.sort(runs)
    need = max(3, int(0.02 * s.size))
    t0 = None
    for i in range(0, s.size - need + 1):  # il più piccolo gruppo di impulsi uguali = 1 bit
        j = int(np.searchsorted(s, s[i] * 1.15, side="right"))
        if j - i >= need:
            t0 = float(np.median(s[i:j]))
            break
    if t0 is None or t0 < 2.5:
        return None
    T, used, _ = _refine(runs, t0)
    if used < 3:
        return None
    fs = cap.samplerate
    baud = fs / T
    best = min(STANDARD_BAUDS, key=lambda b: abs(b - baud) / b)
    nominal = float(best) if abs(best - baud) / best <= 0.05 else None
    return BaudEstimate(baud, nominal, (baud - nominal) / nominal * 100 if nominal else None, T / fs, used)


# =============================== stima formato ================================
@dataclass
class FormatEstimate:
    data_bits: int
    parity: str
    stop_bits: int
    error_rate: float                                   # frazione di frame con errore col formato scelto
    frames: int
    alternatives: list[tuple[int, str, int]] = field(default_factory=list)   # altri formati senza errori


def detect_format(cap: Capture, ch: int, baud: float, invert: bool = False) -> FormatEstimate | None:
    edges = _head_edges(cap, ch, 3000)
    if edges.size < 8:
        return None
    spb = cap.samplerate / baud
    sub = Capture(cap.samplerate, cap.data[:min(len(cap), int(edges[-1] + 24 * spb) + 1)])
    scored = []
    for order, (bits, par, stop) in enumerate(FORMATS):
        frames = decode_uart(sub, ch, baud, bits, par, stop, True, invert)
        n = len(frames)
        if n < 4:
            continue
        rate = sum(not f.ok for f in frames) / n
        # con parità e nessun errore su >= 8 frame la parità è un'evidenza forte (2^-8 per caso)
        prio = 0 if (par != "N" and n >= 8) else 1
        scored.append(((round(rate, 4), prio, order), (bits, par, stop), rate, n))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    _, fmt, rate, n = scored[0]
    # 8E1/8O1 coincidono per costruzione con 9N1: non è un'ambiguità utile da segnalare
    twin = (fmt[0] + 1, "N", fmt[2]) if fmt[1] != "N" else None
    alt = [s[1] for s in scored[1:] if s[2] == 0.0 and s[1] != twin]
    return FormatEstimate(fmt[0], fmt[1], fmt[2], rate, n, alt if rate == 0.0 else [])


# ============================== bit timing ====================================
@dataclass
class BitTiming:
    baud_nominal: float
    baud_actual: float
    error_pct: float
    jitter_rms: float       # s, sugli impulsi bordo-bordo
    jitter_pp: float        # s
    n_runs: int


def measure_bit_timing(cap: Capture, ch: int, baud: float) -> BitTiming | None:
    edges = _head_edges(cap, ch, 4000)
    if edges.size < 8:
        return None
    runs = np.diff(edges).astype(np.float64)
    fs = cap.samplerate
    T, used, res = _refine(runs, fs / baud)
    if used < 8:
        return None
    actual = fs / T
    return BitTiming(baud, actual, (actual - baud) / baud * 100, float(res.std()) / fs,
                     float(res.max() - res.min()) / fs, used)


# ============================ RS-485 A/B differenziale ========================
def ab_to_line(cap: Capture, a: int, b: int, swap: bool = False) -> tuple[Capture, np.ndarray]:
    """Ricostruisce la linea dati da A e B digitalizzati. TIA-485: mark (1) quando A < B.

    Ritorna (cattura con la linea nel bit 0, intervalli [inizio, fine) in cui A == B = bus non pilotato).
    Nei tratti non pilotati la linea mantiene l'ultimo livello valido (prima dei dati: mark).
    """
    A, B = cap.channel(a), cap.channel(b)
    valid = A != B
    mark = (A & ~B) if swap else (B & ~A)
    n = len(A)
    idx = np.maximum.accumulate(np.where(valid, np.arange(n), -1))
    line = np.ones(n, dtype=bool)
    has = idx >= 0
    line[has] = mark[idx[has]]
    inv = ~valid
    d = np.diff(inv.astype(np.int8))
    starts = np.flatnonzero(d > 0) + 1
    ends = np.flatnonzero(d < 0) + 1
    if inv[0]:
        starts = np.concatenate(([0], starts))
    if inv[-1]:
        ends = np.concatenate((ends, [n]))
    runs = np.column_stack((starts, ends)).astype(np.int64)
    return Capture(cap.samplerate, line.astype(np.uint8), source=cap.source), runs


# ================================== messaggi ==================================
@dataclass
class ByteEvent:
    frame: UartFrame
    direction: str          # 'TX' | 'RX' | 'BUS' (direzione non nota)


@dataclass
class Message:
    start: int
    end: int
    direction: str
    data: bytes
    errors: int = 0                     # byte con errore di framing/parità
    gap_before: float | None = None     # s dalla fine del messaggio precedente
    turnaround: float | None = None     # s, solo se la direzione cambia rispetto al precedente
    de_lead: float | None = None        # s, DE attivo prima dello start bit (negativo = DE in ritardo)
    de_lag: float | None = None         # s, DE ancora attivo dopo l'ultimo stop bit (negativo = rilasciato troppo presto)

    @property
    def de_fault(self) -> bool:
        return (self.de_lead is not None and self.de_lead < 0) or (self.de_lag is not None and self.de_lag < 0)


def _group(events: list[ByteEvent], gap_limit: float) -> list[Message]:
    open_: dict[str, list[ByteEvent]] = {}
    out: list[Message] = []

    def flush(d: str) -> None:
        evs = open_.pop(d)
        out.append(Message(evs[0].frame.start, evs[-1].frame.end, d,
                           bytes(e.frame.value & 0xFF for e in evs), sum(not e.frame.ok for e in evs)))

    for ev in sorted(events, key=lambda e: e.frame.start):
        cur = open_.get(ev.direction)
        if cur is not None and ev.frame.start - cur[-1].frame.end <= gap_limit:
            cur.append(ev)
        else:
            if cur is not None:
                flush(ev.direction)
            open_[ev.direction] = [ev]
    for d in list(open_):
        flush(d)
    out.sort(key=lambda m: m.start)
    return out


def _de_lead_lag(active: np.ndarray, rises: np.ndarray, falls: np.ndarray, s0: int, e1: int, fs: float):
    lead = lag = None
    if active[s0]:
        k = int(np.searchsorted(rises, s0, side="right")) - 1
        if k >= 0:
            lead = (s0 - int(rises[k])) / fs
    else:
        k = int(np.searchsorted(rises, s0, side="right"))
        if k < rises.size and rises[k] <= e1:
            lead = -(int(rises[k]) - s0) / fs
    e = min(e1, active.size - 1)
    if active[e]:
        k = int(np.searchsorted(falls, e, side="right"))
        if k < falls.size:
            lag = (int(falls[k]) - e1) / fs
    else:
        k = int(np.searchsorted(falls, e, side="right")) - 1
        if k >= 0 and falls[k] >= s0:
            lag = -(e1 - int(falls[k])) / fs
    return lead, lag


@dataclass
class SerialStats:
    n_bytes: int = 0
    n_errors: int = 0
    n_messages: int = 0
    tx_bytes: int = 0
    rx_bytes: int = 0
    turnaround_min: float | None = None
    turnaround_median: float | None = None
    turnaround_max: float | None = None
    de_lead_min: float | None = None
    de_lead_max: float | None = None
    de_lag_min: float | None = None
    de_lag_max: float | None = None
    de_faults: int = 0
    undriven_events: int = 0
    bit_timing: BitTiming | None = None


@dataclass
class SerialResult:
    lanes: list[Lane] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    stats: SerialStats = field(default_factory=SerialStats)
    baud: float = 0.0
    fmt: tuple[int, str, int] = (8, "N", 1)
    detected_baud: BaudEstimate | None = None
    detected_format: FormatEstimate | None = None
    notes: list[str] = field(default_factory=list)
    modbus: object | None = None        # ModbusResult se abilitato


# ============================ filtro di impulsi spuri ===========================
def deglitch_bits(bits: np.ndarray, min_run: float) -> tuple[np.ndarray, int]:
    """Elimina i tratti piu' brevi di min_run campioni (disturbi, rimbalzi, diafonia).

    Una raffica di tratti brevi consecutivi tra due tratti lunghi vale come un solo disturbo: prende il livello
    del tratto prima (e, se i due tratti lunghi hanno livello diverso, a meta' quello del tratto dopo).
    Ritorna (bit puliti, numero di disturbi eliminati). Il primo e l'ultimo tratto della cattura non si toccano.
    """
    b = bits.astype(np.int8)
    idx = np.flatnonzero(b[1:] != b[:-1]) + 1
    if idx.size < 2:
        return b, 0
    bounds = np.concatenate(([0], idx, [b.size]))
    runs = np.diff(bounds)
    short = np.flatnonzero(runs < min_run)
    short = short[(short > 0) & (short < runs.size - 1)]
    if short.size == 0:
        return b, 0
    levels = b[bounds[:-1]]
    out = b.copy()
    clusters = np.split(short, np.flatnonzero(np.diff(short) > 1) + 1)
    for cl in clusters:
        i, j = int(cl[0]), int(cl[-1]) + 1
        a, nxt = levels[i - 1], levels[j]
        lo, hi = int(bounds[i]), int(bounds[j])
        if a == nxt:
            out[lo:hi] = a
        else:
            mid = (lo + hi) // 2
            out[lo:mid], out[mid:hi] = a, nxt
    return out, len(clusters)


def _clean_line(cap: Capture, ch: int, min_run: float) -> tuple[Capture, int]:
    bits, n = deglitch_bits((cap.data >> ch) & 1, min_run)
    if n == 0:
        return cap, 0
    data = (cap.data & np.uint8(~(1 << ch) & 0xFF)) | (bits.astype(np.uint8) << ch)
    return Capture(cap.samplerate, data), n


def message_text(m: Message, max_bytes: int = 6) -> str:
    head = m.data[:max_bytes].hex(" ").upper()
    more = f" …+{len(m.data) - max_bytes}" if len(m.data) > max_bytes else ""
    return (f"{m.direction} " if m.direction != "BUS" else "") + head + more


def _span(values: list[float]) -> tuple[float | None, float | None]:
    return (min(values), max(values)) if values else (None, None)


def run_serial(cap: Capture, cfg: SerialCfg) -> SerialResult:
    if cfg.mode not in MODES:
        raise ValueError(f"modo '{cfg.mode}' non valido")
    fs = cap.samplerate
    res = SerialResult()
    undriven = np.empty((0, 2), dtype=np.int64)

    if cfg.mode == "rs232":
        lines = [(d, cap, ch) for d, ch in (("TX", cfg.ch_tx), ("RX", cfg.ch_rx)) if ch is not None]
        label = "RS-232"
    elif cfg.mode == "rs485":
        lines, label = [("BUS", cap, cfg.ch_ro)], "RS-485"
    else:
        if cfg.ch_a == cfg.ch_b:
            raise ValueError("A e B devono essere canali diversi")
        line_cap, undriven = ab_to_line(cap, cfg.ch_a, cfg.ch_b, cfg.swap_ab)
        lines, label = [("BUS", line_cap, 0)], "RS-485"
    if not lines:
        raise ValueError("selezionare almeno un canale dati")

    # disturbi di 1 campione (diafonia, rimbalzi) falserebbero la stima del baud: via prima di tutto
    removed = 0
    cleaned = []
    for d, c, ch in lines:
        c2, k = _clean_line(c, ch, 2.0)
        removed += k
        cleaned.append((d, c2, ch))
    lines = cleaned

    baud = cfg.baud
    if baud is None:
        est = next((e for e in (estimate_baud(c, ch) for _, c, ch in lines) if e is not None), None)
        if est is None:
            raise ValueError("baud non rilevabile: servono almeno alcuni impulsi sulla linea")
        res.detected_baud = est
        baud = est.nominal or est.baud
    if fs / baud < 3.0:
        raise ValueError(f"campionamento insufficiente: {fs / baud:.2f} campioni/bit (minimo 3)")
    res.baud = baud

    # con il baud noto, ogni tratto molto piu' corto di un bit e' un disturbo
    min_run = max(2.0, 0.4 * fs / baud)
    cleaned = []
    for d, c, ch in lines:
        c2, k = _clean_line(c, ch, min_run)
        removed += k
        cleaned.append((d, c2, ch))
    lines = cleaned
    if removed:
        res.notes.append(f"{removed} impulsi spuri (più brevi di {min_run / fs * 1e6:.2g} µs) sulla linea ignorati.")

    fmt = cfg.fmt
    if fmt is None:
        fe = next((e for e in (detect_format(c, ch, baud, cfg.invert) for _, c, ch in lines)
                   if e is not None), None)
        if fe is None:
            raise ValueError("formato non rilevabile: pochi frame sulla linea")
        res.detected_format = fe
        fmt = (fe.data_bits, fe.parity, fe.stop_bits)
    bits, parity, stop = fmt
    res.fmt = fmt

    de_active = None
    de_starts = de_ends = rises = falls = np.empty(0, dtype=np.int64)
    if cfg.ch_de is not None and cfg.mode != "rs232":
        de_active = cap.channel(cfg.ch_de)
        if not cfg.de_active_high:
            de_active = ~de_active
        dd = np.diff(de_active.astype(np.int8))
        rises, falls = np.flatnonzero(dd > 0) + 1, np.flatnonzero(dd < 0) + 1
        de_starts = np.concatenate(([0], rises)) if de_active[0] else rises
        de_ends = np.concatenate((falls, [len(de_active)])) if de_active[-1] else falls

    events: list[ByteEvent] = []
    for direction, c, ch in lines:
        for f in decode_uart(c, ch, baud, bits, parity, stop, True, cfg.invert):
            d = direction
            if de_active is not None:
                # TX se DE è attivo in un qualunque istante del frame: così un DE in ritardo o rilasciato
                # troppo presto non sposta il byte tra gli RX e l'errore resta misurabile (anticipo/ritardo < 0)
                k = int(np.searchsorted(de_ends, f.start, side="right"))
                d = "TX" if k < de_starts.size and de_starts[k] <= f.end else "RX"
            events.append(ByteEvent(f, d))

    char_samples = (1 + bits + int(parity != "N") + stop) * fs / baud
    msgs = _group(events, cfg.gap_chars * char_samples)

    prev: Message | None = None
    for m in msgs:
        if prev is not None:
            gap = (m.start - prev.end) / fs
            if gap >= 0:
                m.gap_before = gap
                if m.direction != prev.direction and "BUS" not in (m.direction, prev.direction):
                    m.turnaround = gap
        prev = m

    if de_active is not None:
        for m in msgs:
            if m.direction == "TX":
                m.de_lead, m.de_lag = _de_lead_lag(de_active, rises, falls, m.start, m.end, fs)
    elif cfg.mode != "rs232":
        res.notes.append("DE non collegato: request e risposta non si distinguono (direzione 'BUS'); "
                         "se la pausa tra loro è minore della soglia vengono uniti in un solo messaggio.")

    # ---- lane ---------------------------------------------------------------------
    by_dir: dict[str, list[UartFrame]] = {}
    for ev in events:
        by_dir.setdefault(ev.direction, []).append(ev.frame)
    for d in ("TX", "RX", "BUS"):
        if d in by_dir:
            res.lanes.append(Lane(label if d == "BUS" else f"{label} {d}", uart_annotations(by_dir[d])))

    ann: list[Annotation] = []
    for m in msgs:
        parts = [f"{m.direction} · {len(m.data)} byte", m.data[:64].hex(" ").upper() + (" …" if len(m.data) > 64 else "")]
        if m.errors:
            parts.append(f"{m.errors} byte con errore")
        if m.de_lead is not None and m.de_lead < 0:
            parts.append("DE attivato in ritardo")
        if m.de_lag is not None and m.de_lag < 0:
            parts.append("DE rilasciato prima della fine")
        ann.append(Annotation(m.start, m.end, message_text(m), " · ".join(p for p in parts if p),
                              bool(m.errors) or m.de_fault))
    if ann:
        res.lanes.append(Lane("Messaggi", ann))
    if cfg.modbus and msgs:
        from .modbus import decode_modbus
        mb = decode_modbus(msgs, fs, len(cap), int(cfg.gap_chars * char_samples))
        res.modbus = mb
        res.lanes.append(Lane("Modbus", [
            Annotation(f.start, f.end, f"{f.address:02d} FC{f.function:02d}" if f.function is not None else "?",
                       f.text, not f.ok) for f in mb.frames if f.address is not None]))
        res.notes += mb.notes

    min_run = 0.5 * fs / baud
    long_runs = [(int(a), int(b)) for a, b in undriven if b - a >= min_run]
    if long_runs:
        res.lanes.append(Lane("Bus libero", [
            Annotation(a, b, "A=B", f"bus non pilotato (A = B) per {(b - a) / fs * 1e6:.0f} µs") for a, b in long_runs]))

    # ---- statistiche ------------------------------------------------------------------
    st = res.stats
    st.n_bytes, st.n_messages = len(events), len(msgs)
    st.n_errors = sum(not e.frame.ok for e in events)
    st.tx_bytes = sum(e.direction == "TX" for e in events)
    st.rx_bytes = sum(e.direction == "RX" for e in events)
    ta = [m.turnaround for m in msgs if m.turnaround is not None]
    if ta:
        st.turnaround_min, st.turnaround_max, st.turnaround_median = min(ta), max(ta), float(np.median(ta))
    st.de_lead_min, st.de_lead_max = _span([m.de_lead for m in msgs if m.de_lead is not None])
    st.de_lag_min, st.de_lag_max = _span([m.de_lag for m in msgs if m.de_lag is not None])
    st.de_faults = sum(m.de_fault for m in msgs)
    st.undriven_events = len(long_runs)
    for _, c, ch in lines:
        st.bit_timing = measure_bit_timing(c, ch, baud)
        if st.bit_timing is not None:
            break
    res.messages = msgs
    return res
