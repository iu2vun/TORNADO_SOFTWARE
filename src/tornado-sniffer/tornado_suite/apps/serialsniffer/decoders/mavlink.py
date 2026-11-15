"""MAVLink 1 e 2: framing con resincronizzazione, CRC X.25 + CRC_EXTRA, decodifica dell'intero dialetto `common`."""
from __future__ import annotations

import struct
from typing import Optional

from ..chunk import Chunk
from ._mavlink_data import ENUMS, MESSAGES
from .base import Decoder, Message, StreamKey

STX1, STX2 = 0xFE, 0xFD
SIGNATURE_LEN = 13
ENUM_FIELDS = {
    ("HEARTBEAT", "type"): "MAV_TYPE", ("HEARTBEAT", "autopilot"): "MAV_AUTOPILOT",
    ("HEARTBEAT", "system_status"): "MAV_STATE", ("STATUSTEXT", "severity"): "MAV_SEVERITY",
    ("GPS_RAW_INT", "fix_type"): "GPS_FIX_TYPE", ("GPS2_RAW", "fix_type"): "GPS_FIX_TYPE",
    ("COMMAND_LONG", "command"): "MAV_CMD", ("COMMAND_INT", "command"): "MAV_CMD", ("COMMAND_ACK", "command"): "MAV_CMD",
    ("COMMAND_ACK", "result"): "MAV_RESULT", ("MISSION_ACK", "type"): "MAV_MISSION_RESULT",
    ("MISSION_ITEM_INT", "frame"): "MAV_FRAME", ("MISSION_ITEM", "frame"): "MAV_FRAME",
}


def crc_x25_step(crc: int, b: int) -> int:
    tmp = b ^ (crc & 0xFF)
    tmp = (tmp ^ (tmp << 4)) & 0xFF
    return ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF


def crc_x25(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc = crc_x25_step(crc, b)
    return crc


def _fmt(fields) -> str:
    return "<" + "".join((f"{n}s" if ch == "c" else f"{n}{ch}") if n else ch for _, ch, n in fields)


def payload_size(msgid: int) -> Optional[int]:
    m = MESSAGES.get(msgid)
    return None if m is None else struct.calcsize(_fmt(m[2]))


def build(msgid: int, payload: bytes, *, seq: int = 0, sysid: int = 1, compid: int = 1, version: int = 2) -> bytes:
    """Compone un frame MAVLink valido (v1 o v2) per un messaggio del dialetto common. Il payload v2 viene
    accorciato degli zeri finali, come da specifica."""
    if msgid not in MESSAGES:
        raise ValueError(f"messaggio {msgid} non presente nel dialetto common")
    extra = MESSAGES[msgid][1]
    if version == 1:
        body = bytes([len(payload), seq & 0xFF, sysid, compid, msgid]) + payload
        head = bytes([STX1])
    else:
        payload = payload.rstrip(b"\0") or payload[:1]
        body = bytes([len(payload), 0, 0, seq & 0xFF, sysid, compid]) + struct.pack("<I", msgid)[:3] + payload
        head = bytes([STX2])
    crc = crc_x25_step(crc_x25(body), extra)
    return head + body + struct.pack("<H", crc)


def _enum(name: str, value: int) -> str:
    return f"{value} {ENUMS.get(name, {}).get(value, '?')}"


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def unpack_fields(msgid: int, payload: bytes) -> list[tuple[str, object]]:
    name, _, fields = MESSAGES[msgid]
    need = struct.calcsize(_fmt(fields))
    if len(payload) < need:
        payload = payload + b"\0" * (need - len(payload))  # troncamento degli zeri finali (v2)
    values = struct.unpack_from(_fmt(fields), payload)
    out: list[tuple[str, object]] = []
    i = 0
    for fname, ch, n in fields:
        if ch == "c":
            out.append((fname, _text(values[i])))
            i += 1
        elif n:  # array numerico: struct restituisce gli n elementi appiattiti
            out.append((fname, tuple(values[i:i + n])))
            i += n
        else:
            out.append((fname, values[i]))
            i += 1
    return out


def _fmt_value(msg: str, fname: str, v: object) -> str:
    enum = ENUM_FIELDS.get((msg, fname))
    if enum and isinstance(v, int):
        return _enum(enum, v)
    if isinstance(v, tuple):
        return "[" + ", ".join(f"{x:g}" if isinstance(x, float) else str(x) for x in v) + "]"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def summarize(msg: str, d: dict) -> str:
    g = d.get
    if msg == "HEARTBEAT":
        return f"{ENUMS['MAV_TYPE'].get(g('type'), g('type'))} / {ENUMS['MAV_AUTOPILOT'].get(g('autopilot'), g('autopilot'))}, " \
               f"{ENUMS['MAV_STATE'].get(g('system_status'), g('system_status'))}, mode {g('base_mode')}, custom {g('custom_mode')}"
    if msg == "ATTITUDE":
        import math
        return f"roll {math.degrees(g('roll')):.1f}° pitch {math.degrees(g('pitch')):.1f}° yaw {math.degrees(g('yaw')):.1f}°"
    if msg == "GLOBAL_POSITION_INT":
        return f"{g('lat') * 1e-7:.6f},{g('lon') * 1e-7:.6f} alt {g('alt') / 1000:.1f} m rel {g('relative_alt') / 1000:.1f} m hdg {g('hdg') / 100:.0f}°"
    if msg == "GPS_RAW_INT":
        return f"{ENUMS['GPS_FIX_TYPE'].get(g('fix_type'), g('fix_type'))} sat {g('satellites_visible')} {g('lat') * 1e-7:.6f},{g('lon') * 1e-7:.6f}"
    if msg == "SYS_STATUS":
        return f"{g('voltage_battery') / 1000:.2f} V {g('current_battery') / 100:.2f} A batt {g('battery_remaining')}% load {g('load') / 10:.0f}%"
    if msg == "VFR_HUD":
        return f"aria {g('airspeed'):.1f} suolo {g('groundspeed'):.1f} m/s alt {g('alt'):.1f} m clb {g('climb'):.1f} thr {g('throttle')}%"
    if msg == "STATUSTEXT":
        return f"[{ENUMS['MAV_SEVERITY'].get(g('severity'), g('severity'))}] {g('text')}"
    if msg in ("COMMAND_LONG", "COMMAND_INT"):
        return f"{ENUMS['MAV_CMD'].get(g('command'), g('command'))} → sys {g('target_system')}/{g('target_component')}"
    if msg == "COMMAND_ACK":
        return f"{ENUMS['MAV_CMD'].get(g('command'), g('command'))}: {ENUMS['MAV_RESULT'].get(g('result'), g('result'))}"
    if msg == "PARAM_VALUE":
        return f"{g('param_id')} = {g('param_value'):.6g} ({g('param_index')}/{g('param_count')})"
    if msg == "PARAM_SET":
        return f"{g('param_id')} ← {g('param_value'):.6g}"
    items = [f"{k}={_fmt_value(msg, k, v)}" for k, v in list(d.items())[:5]]
    return " ".join(items)


class Mavlink(Decoder):
    """MAVLink v1/v2. Verifica il CRC con CRC_EXTRA del dialetto `common` (210 messaggi); per id sconosciuti (altri
    dialetti, es. ardupilotmega) cerca il CRC_EXTRA che quadra e segnala il frame come "dialetto non common".
    Rileva pacchetti persi dal numero di sequenza per (sysid, compid). I byte fuori frame sono ignorati."""

    name = "mavlink"
    label = "MAVLink"

    def __init__(self) -> None:
        self._buf: dict[StreamKey, tuple[int, bytearray]] = {}
        self._seq: dict[tuple[StreamKey, int, int], int] = {}

    def reset(self) -> None:
        self._buf.clear()
        self._seq.clear()

    def feed(self, chunk: Chunk) -> list[Message]:
        key = (chunk.channel, chunk.direction)
        ts, buf = self._buf.get(key, (chunk.ts_ns, bytearray()))
        if not buf:
            ts = chunk.ts_ns
        buf += chunk.data
        out: list[Message] = []
        pos = 0
        while True:
            i1, i2 = buf.find(bytes([STX1]), pos), buf.find(bytes([STX2]), pos)
            cands = [i for i in (i1, i2) if i >= 0]
            if not cands:
                pos = len(buf)
                break
            pos = min(cands)
            v2 = buf[pos] == STX2
            hdr = 10 if v2 else 6
            if len(buf) - pos < hdr:
                break
            ln = buf[pos + 1]
            sig = SIGNATURE_LEN if v2 and buf[pos + 2] & 1 else 0
            total = hdr + ln + 2 + sig
            if len(buf) - pos < total:
                if ln < 255 and len(buf) - pos < 300:
                    break  # frame incompleto: attendi il prossimo chunk
                pos += 1
                continue
            frame = bytes(buf[pos:pos + total])
            msg = self._frame(chunk, ts, key, frame, v2, hdr, ln, sig)
            if msg is None:
                pos += 1  # falso STX: riprova dal byte successivo
                continue
            out.append(msg)
            pos += total
            ts = chunk.ts_ns
        del buf[:pos]
        if buf:
            self._buf[key] = (ts, buf)
        else:
            self._buf.pop(key, None)
        if len(buf) > 600:  # residuo che non diventa mai un frame
            self._buf.pop(key, None)
        return out

    def _frame(self, c: Chunk, ts: int, stream: StreamKey, frame: bytes, v2: bool, hdr: int, ln: int, sig: int
               ) -> Optional[Message]:
        if v2:
            seq, sysid, compid = frame[4], frame[5], frame[6]
            msgid = frame[7] | frame[8] << 8 | frame[9] << 16
        else:
            seq, sysid, compid, msgid = frame[2], frame[3], frame[4], frame[5]
        payload = frame[hdr:hdr + ln]
        crc_rx = frame[hdr + ln] | frame[hdr + ln + 1] << 8
        base = crc_x25(frame[1:hdr + ln])
        known = MESSAGES.get(msgid)
        extra_note = ""
        if known is not None:
            ok = crc_x25_step(base, known[1]) == crc_rx
            name = known[0]
        else:
            extra = next((e for e in range(256) if crc_x25_step(base, e) == crc_rx), None)
            ok = extra is not None
            name = f"MSG_{msgid}"
            extra_note = f"dialetto non common (CRC_EXTRA {extra})" if ok else ""
        if not ok:
            if known is None:
                return None  # indistinguibile da un byte STX casuale nel flusso
            return Message(ts, c.channel, c.direction, self.label, f"{name} CRC errato (sys {sysid}/{compid} seq {seq})",
                           (("Hex", frame.hex(" ")),), frame, ok=False, note="CRC errato", kind="invalid", key="invalid")
        lost = 0
        sk = (stream, sysid, compid)
        if sk in self._seq:
            lost = (seq - self._seq[sk] - 1) & 0xFF
        self._seq[sk] = seq
        head = [("Versione", f"MAVLink {2 if v2 else 1}"), ("Sequenza", str(seq)), ("Sistema/componente", f"{sysid}/{compid}"),
                ("Msg ID", f"{msgid} {name}"), ("Lunghezza payload", str(ln))]
        if sig:
            head.append(("Firma", "presente (non verificata)"))
        note = extra_note
        if lost:
            head.append(("Pacchetti persi", str(lost)))
            note = (note + "; " if note else "") + f"{lost} pacchetti persi"
        if known is not None:
            try:
                data = unpack_fields(msgid, payload)
            except struct.error:
                return Message(ts, c.channel, c.direction, self.label, f"{name} payload di lunghezza anomala ({ln})",
                               tuple(head), frame, ok=False, note="payload non valido", kind="invalid", key="invalid")
            fields = [(k, _fmt_value(name, k, v)) for k, v in data]
            summary = summarize(name, dict(data))
        else:
            fields = [("Payload", payload.hex(" "))]
            summary = f"{ln} byte"
        key = f"MAV {name} {sysid}/{compid}"
        return Message(ts, c.channel, c.direction, self.label, f"{name}  {summary}  (sys {sysid}/{compid})", tuple(head + fields),
                       frame, ok=True, note=note, kind="message", key=key)
