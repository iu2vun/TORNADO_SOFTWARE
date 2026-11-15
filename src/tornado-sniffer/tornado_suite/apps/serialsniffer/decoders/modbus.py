"""Modbus RTU: CRC, framing per chunk, accoppiamento richiesta/risposta, decodifica dei campi."""
from __future__ import annotations

from typing import Optional

from ..chunk import Chunk
from .base import Decoder, Message, StreamKey
from .lines import LineSplitter

FC_NAMES = {
    1: "Read Coils", 2: "Read Discrete Inputs", 3: "Read Holding Registers", 4: "Read Input Registers",
    5: "Write Single Coil", 6: "Write Single Register", 7: "Read Exception Status", 8: "Diagnostics",
    11: "Get Comm Event Counter", 12: "Get Comm Event Log", 15: "Write Multiple Coils",
    16: "Write Multiple Registers", 17: "Report Server ID", 20: "Read File Record", 21: "Write File Record",
    22: "Mask Write Register", 23: "Read/Write Multiple Registers", 24: "Read FIFO Queue",
    43: "Encapsulated Interface (MEI)",
}
EXC_NAMES = {
    1: "Illegal Function", 2: "Illegal Data Address", 3: "Illegal Data Value", 4: "Server Device Failure",
    5: "Acknowledge", 6: "Server Device Busy", 7: "Negative Acknowledge", 8: "Memory Parity Error",
    10: "Gateway Path Unavailable", 11: "Gateway Target Device Failed to Respond",
}
MIN_FRAME, MAX_FRAME = 4, 256


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def append_crc(frame: bytes) -> bytes:
    return frame + crc16(frame).to_bytes(2, "little")


def crc_ok(frame: bytes) -> bool:
    return len(frame) >= MIN_FRAME and crc16(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def split_frames(data: bytes) -> tuple[list[bytes], bytes]:
    """Divide in frame con CRC valido (greedy dal più corto). Restituisce (frame, byte non riconosciuti)."""
    frames: list[bytes] = []
    pos = 0
    bad_start: Optional[int] = None
    n = len(data)
    while pos < n:
        found = 0
        if n - pos >= MIN_FRAME:
            for end in range(pos + MIN_FRAME, min(n, pos + MAX_FRAME) + 1):
                if crc_ok(data[pos:end]):
                    found = end
                    break
        if found:
            if bad_start is not None:
                break  # byte non riconosciuti prima di un frame valido: resta tutto come residuo
            frames.append(data[pos:found])
            pos = found
        else:
            bad_start = pos if bad_start is None else bad_start
            break
    return frames, (data[pos:] if bad_start is not None else b"")


def _u16(b: bytes, i: int) -> int:
    return (b[i] << 8) | b[i + 1]


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _bits(data: bytes, qty: int) -> str:
    return "".join("1" if data[i // 8] >> (i % 8) & 1 else "0" for i in range(min(qty, len(data) * 8)))


class _Req:
    __slots__ = ("ts", "addr", "fc", "pdu", "stream")

    def __init__(self, ts: int, addr: int, fc: int, pdu: bytes, stream: StreamKey) -> None:
        self.ts, self.addr, self.fc, self.pdu, self.stream = ts, addr, fc, pdu, stream


def request_shape_ok(fc: int, pdu: bytes) -> bool:
    n = len(pdu)
    if fc in (1, 2, 3, 4, 5, 6, 8):
        return n == 5
    if fc in (7, 11, 12, 17):
        return n == 1
    if fc == 15 or fc == 16:
        return n >= 6 and n == 6 + pdu[5]
    if fc == 22:
        return n == 7
    if fc == 23:
        return n >= 10 and n == 10 + pdu[9]
    if fc == 24:
        return n == 3
    return False


def response_shape_ok(fc: int, pdu: bytes, req: Optional[_Req]) -> bool:
    n = len(pdu)
    if fc in (1, 2, 3, 4):
        if n < 2 or n != 2 + pdu[1]:
            return False
        if req is not None and req.fc == fc and len(req.pdu) == 5:
            q = _u16(req.pdu, 3)
            expect = (q + 7) // 8 if fc in (1, 2) else 2 * q
            return pdu[1] == expect
        return fc in (1, 2) or pdu[1] % 2 == 0
    if fc in (5, 6):
        return n == 5 and (req is None or req.pdu == pdu)
    if fc in (15, 16):
        return n == 5 and (req is None or req.pdu[1:5] == pdu[1:5])
    if fc == 22:
        return n == 7 and (req is None or req.pdu == pdu)
    if fc == 23:
        return n >= 2 and n == 2 + pdu[1]
    if fc == 7:
        return n == 2
    if fc == 8:
        return n == 5
    if fc == 11:
        return n == 5
    if fc == 17:
        return n >= 2 and n == 2 + pdu[1]
    return False


def describe_request(fc: int, pdu: bytes) -> tuple[str, list[tuple[str, str]]]:
    f: list[tuple[str, str]] = []
    if fc in (1, 2, 3, 4):
        a, q = _u16(pdu, 1), _u16(pdu, 3)
        f += [("Indirizzo iniziale", f"{a} (0x{a:04X})"), ("Quantità", str(q))]
        return f"@{a} ×{q}", f
    if fc == 5:
        a, v = _u16(pdu, 1), _u16(pdu, 3)
        state = "ON" if v == 0xFF00 else "OFF" if v == 0 else f"0x{v:04X}?"
        f += [("Indirizzo", f"{a} (0x{a:04X})"), ("Valore", state)]
        return f"@{a} = {state}", f
    if fc == 6:
        a, v = _u16(pdu, 1), _u16(pdu, 3)
        f += [("Indirizzo", f"{a} (0x{a:04X})"), ("Valore", f"{v} (0x{v:04X}, s16 {_s16(v)})")]
        return f"@{a} = {v}", f
    if fc == 15:
        a, q, bc = _u16(pdu, 1), _u16(pdu, 3), pdu[5]
        f += [("Indirizzo iniziale", f"{a} (0x{a:04X})"), ("Quantità", str(q)), ("Bit", _bits(pdu[6:], q))]
        return f"@{a} ×{q} = {_bits(pdu[6:], q)}", f
    if fc == 16:
        a, q = _u16(pdu, 1), _u16(pdu, 3)
        regs = [_u16(pdu, 6 + 2 * i) for i in range(min(q, (len(pdu) - 6) // 2))]
        f += [("Indirizzo iniziale", f"{a} (0x{a:04X})"), ("Quantità", str(q))]
        f += [(f"reg[{a + i}]", f"{v} (0x{v:04X})") for i, v in enumerate(regs)]
        return f"@{a} ×{q} = " + ", ".join(str(v) for v in regs[:8]) + (" …" if len(regs) > 8 else ""), f
    if fc == 22:
        a, am, om = _u16(pdu, 1), _u16(pdu, 3), _u16(pdu, 5)
        f += [("Indirizzo", str(a)), ("AND mask", f"0x{am:04X}"), ("OR mask", f"0x{om:04X}")]
        return f"@{a} AND 0x{am:04X} OR 0x{om:04X}", f
    if fc == 23:
        ra, rq, wa, wq = _u16(pdu, 1), _u16(pdu, 3), _u16(pdu, 5), _u16(pdu, 7)
        regs = [_u16(pdu, 10 + 2 * i) for i in range(min(wq, (len(pdu) - 10) // 2))]
        f += [("Lettura", f"@{ra} ×{rq}"), ("Scrittura", f"@{wa} ×{wq}")]
        f += [(f"wreg[{wa + i}]", f"{v} (0x{v:04X})") for i, v in enumerate(regs)]
        return f"R @{ra}×{rq}  W @{wa}×{wq}", f
    if fc == 8:
        sub, d = _u16(pdu, 1), _u16(pdu, 3)
        f += [("Sub-function", f"0x{sub:04X}"), ("Dati", f"0x{d:04X}")]
        return f"sub 0x{sub:04X} dati 0x{d:04X}", f
    if fc == 24:
        a = _u16(pdu, 1)
        return f"FIFO @{a}", [("Indirizzo FIFO", str(a))]
    return "", f


def describe_response(fc: int, pdu: bytes, req: Optional[_Req]) -> tuple[str, list[tuple[str, str]]]:
    f: list[tuple[str, str]] = []
    start = _u16(req.pdu, 1) if req is not None and fc in (1, 2, 3, 4) and len(req.pdu) == 5 else None
    if fc in (1, 2):
        data = pdu[2:]
        q = _u16(req.pdu, 3) if start is not None else len(data) * 8
        bits = _bits(data, q)
        f += [("Byte count", str(pdu[1])), ("Bit (LSB per primo)", bits)]
        if start is not None:
            f += [(f"bit[{start + i}]", bits[i]) for i in range(min(len(bits), 64))]
        return f"{len(bits)} bit: {bits[:32]}{'…' if len(bits) > 32 else ''}", f
    if fc in (3, 4):
        regs = [_u16(pdu, 2 + 2 * i) for i in range(pdu[1] // 2)]
        f.append(("Byte count", str(pdu[1])))
        for i, v in enumerate(regs[:128]):
            name = f"reg[{start + i}]" if start is not None else f"reg[{i}]"
            f.append((name, f"{v} (0x{v:04X}, s16 {_s16(v)})"))
        if len(regs) >= 2:
            f.append(("u32 BE (reg0..1)", str(regs[0] << 16 | regs[1])))
            f.append(("u32 LE-word (reg0..1)", str(regs[1] << 16 | regs[0])))
            import struct
            try:
                f.append(("float BE (reg0..1)", f"{struct.unpack('>f', struct.pack('>HH', regs[0], regs[1]))[0]:.6g}"))
                f.append(("float word-swap (reg0..1)",
                          f"{struct.unpack('>f', struct.pack('>HH', regs[1], regs[0]))[0]:.6g}"))
            except struct.error:
                pass
        return ", ".join(str(v) for v in regs[:8]) + (" …" if len(regs) > 8 else ""), f
    if fc in (5, 6, 22):
        s, _ = describe_request(fc, pdu)
        return f"eco {s}", [("Eco", s)]
    if fc in (15, 16):
        a, q = _u16(pdu, 1), _u16(pdu, 3)
        return f"@{a} ×{q} scritti", [("Indirizzo iniziale", str(a)), ("Quantità", str(q))]
    if fc == 23:
        regs = [_u16(pdu, 2 + 2 * i) for i in range(pdu[1] // 2)]
        return ", ".join(str(v) for v in regs[:8]), [(f"reg[{i}]", str(v)) for i, v in enumerate(regs)]
    if fc == 7:
        return f"stato 0x{pdu[1]:02X}", [("Exception status", f"0x{pdu[1]:02X}")]
    if fc == 8:
        sub, d = _u16(pdu, 1), _u16(pdu, 3)
        return f"sub 0x{sub:04X} dati 0x{d:04X}", [("Sub-function", f"0x{sub:04X}"), ("Dati", f"0x{d:04X}")]
    if fc == 11:
        st, cnt = _u16(pdu, 1), _u16(pdu, 3)
        return f"stato 0x{st:04X} eventi {cnt}", [("Stato", f"0x{st:04X}"), ("Contatore", str(cnt))]
    if fc == 17:
        return f"{pdu[1]} byte", [("Dati", pdu[2:].hex(" "))]
    return "", f


class ModbusRtu(Decoder):
    """Decoder Modbus RTU.

    Si affida al raggruppamento per gap idle: un chunk = uno o più frame completi. Il frame è accettato
    se il CRC16 è valido; un chunk senza CRC valido viene riportato come frame errato.

    Ruolo richiesta/risposta:
    - `masters` (insieme di (canale, direzione)) lo fissa per flusso: ciò che parte da quei flussi è richiesta;
    - altrimenti lo deduce dalla struttura del frame e dalla richiesta pendente (stesso indirizzo e funzione,
      lunghezza coerente con quantità richiesta), anche quando richiesta e risposta passano dallo stesso canale
      (adattatore RS-485 in ascolto sul bus)."""

    name = "modbus"
    label = "Modbus RTU"
    options = {"masters": "flussi (canale,dir) da cui parte il master; vuoto = automatico",
               "timeout_s": "validità di una richiesta in attesa di risposta (default 2 s)"}

    def __init__(self, masters: Optional[set[StreamKey]] = None, timeout_s: float = 2.0) -> None:
        self.masters = set(masters) if masters else set()
        self.timeout_ns = int(timeout_s * 1e9)
        self._pending: Optional[_Req] = None

    def reset(self) -> None:
        self._pending = None

    def feed(self, chunk: Chunk) -> list[Message]:
        data = chunk.data
        if len(data) < MIN_FRAME:
            return [self._bad(chunk, data, "frame troppo corto")]
        frames, rest = split_frames(data)
        out = [self._frame(chunk, fr) for fr in frames]
        if rest:
            out.append(self._bad(chunk, rest, "CRC errato" if len(rest) >= MIN_FRAME else "residuo"))
        return out

    # ----------------------------------------------------------------- interno
    def _bad(self, c: Chunk, raw: bytes, why: str) -> Message:
        addr = f"@{raw[0]} " if raw else ""
        return Message(c.ts_ns, c.channel, c.direction, self.label, f"{addr}frame non valido ({why})",
                       raw=raw, ok=False, note=why, kind="invalid", key="invalid",
                       fields=(("Addr", str(raw[0])), ("Hex", raw.hex(" "))) if raw else ())

    def _frame(self, c: Chunk, frame: bytes) -> Message:
        return self._decode(c, frame, frame[0], frame[1:-2], ("CRC", f"0x{int.from_bytes(frame[-2:], 'little'):04X} ok"))

    def _decode(self, c: Chunk, frame: bytes, addr: int, pdu: bytes, check: tuple[str, str]) -> Message:
        """Decodifica indirizzo + PDU, indipendentemente dal trasporto (RTU o ASCII)."""
        fc = pdu[0]
        stream = (c.channel, c.direction)
        base = [("Indirizzo", f"{addr}" + (" (broadcast)" if addr == 0 else "")),
                ("Funzione", f"{fc & 0x7F} {FC_NAMES.get(fc & 0x7F, 'non standard')}"),
                check]
        req = self._pending
        if req is not None and (c.ts_ns - req.ts > self.timeout_ns or c.ts_ns < req.ts):
            req = self._pending = None

        if fc & 0x80:  # eccezione
            ok = len(pdu) == 2
            code = pdu[1] if ok else 0
            match = req is not None and req.addr == addr and req.fc == (fc & 0x7F)
            lat = c.ts_ns - req.ts if match else None
            if match:
                self._pending = None
            text = EXC_NAMES.get(code, "sconosciuta")
            return Message(c.ts_ns, c.channel, c.direction, self.label,
                           f"@{addr} FC{fc & 0x7F} ECCEZIONE {code} {text}",
                           tuple(base + [("Codice eccezione", f"{code} {text}")]), frame, ok=ok,
                           note="" if ok else "eccezione malformata", kind="exception", pair_ns=lat,
                           key=f"@{addr} FC{fc & 0x7F}")

        match = req is not None and req.addr == addr and req.fc == fc
        if self.masters:
            role = "request" if stream in self.masters else "response"
        elif match and response_shape_ok(fc, pdu, req):
            role = "response"
        elif request_shape_ok(fc, pdu):
            role = "request"
        elif response_shape_ok(fc, pdu, None):
            role = "response"
        else:
            role = "unknown"

        name = FC_NAMES.get(fc, f"FC{fc}")
        if role == "request":
            ok = request_shape_ok(fc, pdu)
            sd, f = describe_request(fc, pdu) if ok else ("", [])
            if addr != 0 and ok:
                self._pending = _Req(c.ts_ns, addr, fc, pdu, stream)
            else:
                self._pending = None
            return Message(c.ts_ns, c.channel, c.direction, self.label,
                           f"@{addr} → {name} {sd}".rstrip() + ("" if ok else "  [struttura non valida]"),
                           tuple(base + f), frame, ok=ok, note="" if ok else "struttura della richiesta non valida",
                           kind="request", key=f"@{addr} FC{fc}")
        if role == "response":
            generic = response_shape_ok(fc, pdu, None)
            strict = response_shape_ok(fc, pdu, req) if match else generic
            ok = generic and strict
            sd, f = describe_response(fc, pdu, req if match and strict else None) if generic else ("", [])
            lat = c.ts_ns - req.ts if match and generic else None
            if match and generic:
                self._pending = None
            if not generic:
                note = "struttura della risposta non valida"
            elif not strict:
                note = "incoerente con la richiesta (quantità/eco diversi)"
            else:
                note = "" if match else "risposta senza richiesta"
            summ = f"@{addr} ← {name} {sd}".rstrip()
            if lat is not None:
                summ += f"   ({lat / 1e6:.1f} ms)"
            f2 = base + f
            if lat is not None:
                f2.append(("Latenza", f"{lat / 1e6:.3f} ms"))
            return Message(c.ts_ns, c.channel, c.direction, self.label,
                           summ + (f"  [{note}]" if note else ""), tuple(f2), frame, ok=ok,
                           note=note, kind="response", pair_ns=lat, key=f"@{addr} FC{fc}")
        return Message(c.ts_ns, c.channel, c.direction, self.label,
                       f"@{addr} {name} ({len(pdu) - 1} byte di dati)",
                       tuple(base + [("Dati PDU", pdu[1:].hex(" "))]), frame, ok=True,
                       note="funzione non decodificata", kind="unknown", key=f"@{addr} FC{fc}")


def lrc(data: bytes) -> int:
    return (-sum(data)) & 0xFF


def ascii_frame(addr_pdu: bytes) -> bytes:
    """Compone un frame Modbus ASCII completo (:HEX LRC CRLF) da indirizzo + PDU."""
    return b":" + (addr_pdu + bytes([lrc(addr_pdu)])).hex().upper().encode() + b"\r\n"


class ModbusAscii(ModbusRtu):
    """Modbus ASCII: righe `:` + coppie esadecimali + LRC + CR LF. Stessa decodifica e stesso abbinamento
    richiesta/risposta del Modbus RTU."""

    name = "modbus_ascii"
    label = "Modbus ASCII"

    def __init__(self, masters: Optional[set[StreamKey]] = None, timeout_s: float = 2.0) -> None:
        super().__init__(masters, timeout_s)
        self._lines = LineSplitter()

    def reset(self) -> None:
        super().reset()
        self._lines.reset()

    def feed(self, chunk: Chunk) -> list[Message]:
        return [self._ascii(chunk, *t) for t in self._lines.feed(chunk)]

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        return [self._ascii(None, *t) for t in self._lines.flush(now_ns)]

    def _abad(self, c: Chunk, raw: bytes, why: str) -> Message:
        return Message(c.ts_ns, c.channel, c.direction, self.label, f"riga non valida ({why})", (("Testo", raw.decode("latin-1").strip()),),
                       raw, ok=False, note=why, kind="invalid", key="invalid")

    def _ascii(self, c: Optional[Chunk], key: StreamKey, ts: int, raw: bytes, complete: bool) -> Message:
        chunk = Chunk(ts, key[0], key[1], raw)
        text = raw.strip()
        if not text.startswith(b":"):
            return self._abad(chunk, raw, "manca ':'")
        body = text[1:]
        try:
            data = bytes.fromhex(body.decode("ascii"))
        except (ValueError, UnicodeDecodeError):
            return self._abad(chunk, raw, "caratteri non esadecimali")
        if len(data) < 3:
            return self._abad(chunk, raw, "frame troppo corto")
        if lrc(data[:-1]) != data[-1]:
            return self._abad(chunk, raw, f"LRC errato (atteso {lrc(data[:-1]):02X})")
        return self._decode(chunk, raw, data[0], data[1:-1], ("LRC", f"0x{data[-1]:02X} ok"))
