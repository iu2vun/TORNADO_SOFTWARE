"""Decodifica Modbus RTU sui messaggi dell'analizzatore seriale.

Ogni messaggio (gruppo di byte separato da una pausa >= 3,5 caratteri) e' un frame RTU:
``indirizzo | funzione | dati | CRC16 (byte basso per primo)``. Qui si controllano CRC e lunghezza, si
interpretano le funzioni piu' comuni, si abbinano richiesta e risposta (tempo di risposta) e si segnalano
eccezioni, richieste senza risposta e frame malformati.

Direzione: se il segnale DE (o le due linee RS-232) distingue TX/RX, TX = master (richiesta) e RX = slave
(risposta); senza direzione ('BUS') il tipo si deduce dalla lunghezza e dall'abbinamento con la richiesta aperta.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FUNCTIONS = {
    1: "Lettura coil", 2: "Lettura ingressi discreti", 3: "Lettura holding register",
    4: "Lettura input register", 5: "Scrittura coil", 6: "Scrittura registro",
    7: "Lettura stato eccezioni", 8: "Diagnostica", 11: "Contatore eventi", 12: "Log eventi",
    15: "Scrittura coil multipli", 16: "Scrittura registri multipli", 17: "Identificazione slave",
    22: "Mask write registro", 23: "Lettura/scrittura registri", 43: "Interfaccia MEI",
}
EXCEPTIONS = {
    1: "funzione non valida", 2: "indirizzo dati non valido", 3: "valore dati non valido",
    4: "guasto dello slave", 5: "acknowledge", 6: "slave occupato", 8: "errore di parità memoria",
    10: "gateway: percorso non disponibile", 11: "gateway: bersaglio non risponde",
}
MIN_FRAME = 4        # indirizzo + funzione + CRC


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def func_name(f: int) -> str:
    return FUNCTIONS.get(f, f"funzione {f}")


@dataclass
class ModbusFrame:
    start: int                      # campione di inizio/fine (stessi del Message)
    end: int
    direction: str
    address: int | None
    function: int | None            # codice funzione senza il bit di eccezione
    kind: str                       # request | response | exception | invalid
    crc_ok: bool
    length_ok: bool = True
    broadcast: bool = False
    truncated: bool = False         # tagliato dal bordo della cattura: non e' un errore del bus
    text: str = ""                  # una riga, per tabella e tracce
    detail: str = ""
    exception_code: int | None = None
    response_time: float | None = None      # s, dalla fine della richiesta all'inizio della risposta
    answered: bool | None = None            # solo per le richieste

    @property
    def ok(self) -> bool:
        return self.truncated or (self.crc_ok and self.length_ok and self.kind != "invalid")


@dataclass
class ModbusStats:
    n_frames: int = 0
    n_requests: int = 0
    n_responses: int = 0
    n_exceptions: int = 0
    n_crc_errors: int = 0
    n_malformed: int = 0
    n_unanswered: int = 0
    n_pairs: int = 0
    resp_min: float | None = None
    resp_median: float | None = None
    resp_max: float | None = None
    slaves: list[int] = field(default_factory=list)


@dataclass
class ModbusResult:
    frames: list[ModbusFrame] = field(default_factory=list)
    stats: ModbusStats = field(default_factory=ModbusStats)
    notes: list[str] = field(default_factory=list)


# ---- lunghezze attese -----------------------------------------------------------------------
def _request_len(d: bytes) -> int | None:
    """Lunghezza totale (con CRC) di una richiesta con questa funzione, None se non determinabile."""
    f = d[1]
    if f in (1, 2, 3, 4, 5, 6, 8):
        return 8
    if f in (7, 11, 12, 17):
        return 4
    if f == 22:
        return 10
    if f in (15, 16):
        return 9 + d[6] if len(d) > 6 else None
    if f == 23:
        return 13 + d[10] if len(d) > 10 else None
    return None


def _response_len(d: bytes) -> int | None:
    f = d[1]
    if f in (1, 2, 3, 4, 17):
        return 5 + d[2] if len(d) > 2 else None
    if f in (5, 6, 15, 16, 8, 11):
        return 8
    if f == 7:
        return 5
    if f == 22:
        return 10
    if f == 23:
        return 5 + d[2] if len(d) > 2 else None
    if f == 12:
        return 5 + d[2] if len(d) > 2 else None
    return None


def _matches(d: bytes, expect: int | None) -> bool:
    return expect is None or expect == len(d)


# ---- descrizioni ----------------------------------------------------------------------------
def _u16(d: bytes, i: int) -> int:
    return (d[i] << 8) | d[i + 1]


def _regs(d: bytes, i: int, n: int, limit: int = 8) -> str:
    vals = [str(_u16(d, i + 2 * k)) for k in range(min(n, limit))]
    return ", ".join(vals) + (f" …(+{n - limit})" if n > limit else "")


def _describe_request(d: bytes) -> str:
    f = d[1]
    try:
        if f in (1, 2, 3, 4):
            unit = "reg" if f in (3, 4) else "bit"
            return f"da {_u16(d, 2)} ×{_u16(d, 4)} {unit}"
        if f == 5:
            return f"coil {_u16(d, 2)} = {'ON' if _u16(d, 4) == 0xFF00 else 'OFF' if _u16(d, 4) == 0 else hex(_u16(d, 4))}"
        if f == 6:
            return f"reg {_u16(d, 2)} = {_u16(d, 4)}"
        if f == 16:
            return f"da {_u16(d, 2)} ×{_u16(d, 4)} reg = {_regs(d, 7, d[6] // 2)}"
        if f == 15:
            return f"da {_u16(d, 2)} ×{_u16(d, 4)} coil, {d[6]} byte"
        if f == 22:
            return f"reg {_u16(d, 2)} AND {_u16(d, 4):04X} OR {_u16(d, 6):04X}"
        if f == 23:
            return (f"leggi da {_u16(d, 2)} ×{_u16(d, 4)}, scrivi da {_u16(d, 6)} ×{_u16(d, 8)}")
        if f == 8:
            return f"sottofunzione {_u16(d, 2):04X}, dati {_u16(d, 4):04X}"
    except IndexError:
        pass
    return d[2:-2].hex(" ").upper()


def _describe_response(d: bytes) -> str:
    f = d[1]
    try:
        if f in (3, 4, 23):
            return f"{d[2] // 2} reg: {_regs(d, 3, d[2] // 2)}"
        if f in (1, 2):
            return f"{d[2]} byte: {d[3:3 + d[2]].hex(' ').upper()}"
        if f == 5:
            return f"coil {_u16(d, 2)} = {'ON' if _u16(d, 4) == 0xFF00 else 'OFF'}"
        if f == 6:
            return f"reg {_u16(d, 2)} = {_u16(d, 4)}"
        if f in (15, 16):
            return f"da {_u16(d, 2)} ×{_u16(d, 4)}"
        if f == 7:
            return f"stato {d[2]:02X}"
    except IndexError:
        pass
    return d[2:-2].hex(" ").upper()


# ---- decodifica -----------------------------------------------------------------------------
def _classify(d: bytes, hint: str, pending: ModbusFrame | None) -> tuple[str, bool]:
    """(kind, length_ok). hint: 'request' | 'response' | ''."""
    if d[1] & 0x80:
        return "exception", len(d) == 5
    exp_req, exp_rsp = _request_len(d), _response_len(d)
    as_req, as_rsp = _matches(d, exp_req) and exp_req is not None, _matches(d, exp_rsp) and exp_rsp is not None
    if hint == "request":
        order = ("request", "response")
    elif hint == "response":
        order = ("response", "request")
    else:
        same = pending is not None and pending.address == d[0] and pending.function == d[1]
        order = ("response", "request") if same else ("request", "response")
    for k in order:
        if (k == "request" and as_req) or (k == "response" and as_rsp):
            return k, True
    if exp_req is None and exp_rsp is None:           # funzione sconosciuta: nessun controllo di lunghezza
        return hint or ("response" if pending is not None else "request"), True
    return hint or "request", False


def decode_modbus(messages, fs: float, n_samples: int | None = None, edge_margin: int = 0) -> ModbusResult:
    """messages: Message di serialbus (start, end, direction, data, errors). Ordine temporale.

    n_samples/edge_margin: un frame non valido che tocca l'inizio o la fine della cattura (entro edge_margin
    campioni) e' considerato troncato dalla cattura, non un errore."""
    res = ModbusResult()
    frames = res.frames
    pending: ModbusFrame | None = None
    response_times: list[float] = []
    slaves: set[int] = set()

    for m in sorted(messages, key=lambda x: x.start):
        d = bytes(m.data)
        hint = {"TX": "request", "RX": "response"}.get(m.direction, "")
        if len(d) < MIN_FRAME:
            fr = ModbusFrame(m.start, m.end, m.direction, d[0] if d else None, None, "invalid", False, False,
                             text=f"frame troppo corto ({len(d)} byte)", detail=d.hex(" ").upper())
            frames.append(fr)
            continue
        crc_ok = crc16(d[:-2]) == (d[-2] | (d[-1] << 8))
        kind, len_ok = _classify(d, hint, pending)
        func = d[1] & 0x7F
        fr = ModbusFrame(m.start, m.end, m.direction, d[0], func, kind, crc_ok, len_ok,
                         broadcast=(d[0] == 0 and kind == "request"))
        name = func_name(func)
        head = f"{d[0]:02d} · FC{func:02d} {name}"
        if kind == "exception":
            fr.exception_code = d[2] if len(d) > 2 else None
            why = EXCEPTIONS.get(fr.exception_code, "codice sconosciuto")
            fr.text = f"{head} · ECCEZIONE {fr.exception_code}: {why}"
        elif kind == "request":
            fr.text = f"{head} · {_describe_request(d)}"
            if fr.broadcast:
                fr.text += " · broadcast"
        else:
            fr.text = f"{head} · risposta {_describe_response(d)}"
        flags = []
        if not crc_ok:
            exp = crc16(d[:-2])
            flags.append(f"CRC errato (ricevuto {d[-2] | (d[-1] << 8):04X}, atteso {exp:04X})")
        if not len_ok:
            flags.append("lunghezza non coerente con la funzione")
        if getattr(m, "errors", 0):
            flags.append(f"{m.errors} byte con errore di framing/parità")
        if flags:
            fr.text += " · " + ", ".join(flags)
        fr.detail = d.hex(" ").upper()

        # abbinamento richiesta/risposta
        if kind == "request":
            if pending is not None and pending.answered is False and not pending.broadcast:
                res.stats.n_unanswered += 1
            fr.answered = False
            pending = fr
        elif kind in ("response", "exception") and pending is not None and pending.address == fr.address \
                and pending.function == fr.function and pending.answered is False:
            pending.answered = True
            fr.response_time = (fr.start - pending.end) / fs
            response_times.append(fr.response_time)
            res.stats.n_pairs += 1
            pending = None
        frames.append(fr)
        if crc_ok and kind != "invalid":
            if d[0] != 0:
                slaves.add(d[0])

    if frames and n_samples is not None:
        for f in (frames[0], frames[-1]):
            if not f.ok and (f.start <= edge_margin or f.end + edge_margin >= n_samples):
                f.truncated = True
                f.text = f.text.split(" · CRC")[0].split(" · lunghezza")[0] + " · troncato dal bordo della cattura"

    st = res.stats
    st.n_frames = len(frames)
    st.n_requests = sum(f.kind == "request" for f in frames)
    st.n_responses = sum(f.kind == "response" for f in frames)
    st.n_exceptions = sum(f.kind == "exception" for f in frames)
    st.n_crc_errors = sum(not f.crc_ok and f.kind != "invalid" and not f.truncated for f in frames)
    st.n_malformed = sum(((not f.length_ok) or f.kind == "invalid") and not f.truncated for f in frames)
    if response_times:
        st.resp_min, st.resp_max, st.resp_median = (min(response_times), max(response_times),
                                                    float(np.median(response_times)))
    st.slaves = sorted(slaves)
    bad = sum(not f.ok for f in frames)
    if st.n_frames >= 2 and bad * 2 > st.n_frames:
        res.notes.append("La maggior parte dei frame non è Modbus RTU valido: controlla la linea scelta (di solito "
                         "RS-485, RO + DE), baud e formato (8E1 o 8N2), oppure disattiva la decodifica Modbus.")
    if st.n_unanswered:
        res.notes.append(f"{st.n_unanswered} richieste senza risposta (slave muto o risposta "
                         "fuori dalla cattura).")
    return res
