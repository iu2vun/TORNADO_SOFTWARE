"""u-blox UBX: framing con resincronizzazione, checksum di Fletcher, decodifica dei messaggi più comuni."""
from __future__ import annotations

import struct
from typing import Optional

from ..chunk import Chunk
from .base import Decoder, Message, StreamKey

SYNC = b"\xb5\x62"
MAX_PAYLOAD = 8192

NAMES = {
    (0x01, 0x01): "NAV-POSECEF", (0x01, 0x02): "NAV-POSLLH", (0x01, 0x03): "NAV-STATUS", (0x01, 0x04): "NAV-DOP",
    (0x01, 0x07): "NAV-PVT", (0x01, 0x11): "NAV-VELECEF", (0x01, 0x12): "NAV-VELNED", (0x01, 0x20): "NAV-TIMEGPS",
    (0x01, 0x21): "NAV-TIMEUTC", (0x01, 0x22): "NAV-CLOCK", (0x01, 0x35): "NAV-SAT", (0x01, 0x30): "NAV-SVINFO",
    (0x01, 0x43): "NAV-SIG", (0x01, 0x3C): "NAV-RELPOSNED", (0x01, 0x3B): "NAV-SVIN", (0x01, 0x26): "NAV-TIMELS",
    (0x01, 0x09): "NAV-ODO", (0x01, 0x34): "NAV-ORB", (0x01, 0x17): "NAV-PVAT",
    (0x02, 0x10): "RXM-RAW", (0x02, 0x15): "RXM-RAWX", (0x02, 0x13): "RXM-SFRBX", (0x02, 0x59): "RXM-RLM",
    (0x02, 0x32): "RXM-RTCM", (0x02, 0x14): "RXM-MEASX", (0x02, 0x61): "RXM-IMES", (0x02, 0x41): "RXM-PMREQ",
    (0x05, 0x00): "ACK-NAK", (0x05, 0x01): "ACK-ACK",
    (0x06, 0x00): "CFG-PRT", (0x06, 0x01): "CFG-MSG", (0x06, 0x04): "CFG-RST", (0x06, 0x08): "CFG-RATE",
    (0x06, 0x09): "CFG-CFG", (0x06, 0x13): "CFG-ANT", (0x06, 0x16): "CFG-SBAS", (0x06, 0x17): "CFG-NMEA",
    (0x06, 0x1B): "CFG-USB", (0x06, 0x23): "CFG-NAVX5", (0x06, 0x24): "CFG-NAV5", (0x06, 0x3E): "CFG-GNSS",
    (0x06, 0x8A): "CFG-VALSET", (0x06, 0x8B): "CFG-VALGET", (0x06, 0x8C): "CFG-VALDEL", (0x06, 0x31): "CFG-TP5",
    (0x0A, 0x04): "MON-VER", (0x0A, 0x09): "MON-HW", (0x0A, 0x0B): "MON-HW2", (0x0A, 0x28): "MON-GNSS",
    (0x0A, 0x31): "MON-SPAN", (0x0A, 0x38): "MON-RF", (0x0A, 0x08): "MON-TXBUF", (0x0A, 0x06): "MON-MSGPP",
    (0x0A, 0x07): "MON-RXBUF", (0x0A, 0x36): "MON-COMMS", (0x0A, 0x27): "MON-PATCH", (0x0A, 0x37): "MON-HW3",
    (0x0B, 0x01): "AID-INI", (0x0B, 0x30): "AID-ALM", (0x0B, 0x31): "AID-EPH", (0x0B, 0x02): "AID-HUI",
    (0x0D, 0x01): "TIM-TM2", (0x0D, 0x03): "TIM-TP", (0x0D, 0x06): "TIM-VRFY", (0x0D, 0x04): "TIM-SVIN",
    (0x10, 0x02): "ESF-MEAS", (0x10, 0x03): "ESF-RAW", (0x10, 0x10): "ESF-STATUS", (0x10, 0x15): "ESF-INS",
    (0x13, 0x80): "MGA-GPS", (0x13, 0x60): "MGA-ACK", (0x13, 0x40): "MGA-INI", (0x13, 0x20): "MGA-GAL",
    (0x21, 0x04): "LOG-INFO", (0x21, 0x07): "LOG-CREATE", (0x27, 0x01): "SEC-SIGN", (0x27, 0x03): "SEC-UNIQID",
    (0x09, 0x14): "UPD-SOS", (0x04, 0x00): "INF-ERROR", (0x04, 0x01): "INF-WARNING", (0x04, 0x02): "INF-NOTICE",
    (0x04, 0x03): "INF-TEST", (0x04, 0x04): "INF-DEBUG",
}
GNSS_ID = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 4: "IMES", 5: "QZSS", 6: "GLONASS", 7: "NavIC"}
FIX_TYPE = {0: "no fix", 1: "dead reckoning", 2: "2D", 3: "3D", 4: "GNSS+DR", 5: "solo tempo"}
CARR_SOLN = {0: "", 1: "RTK float", 2: "RTK fixed"}


def checksum(data: bytes) -> tuple[int, int]:
    a = b = 0
    for x in data:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return a, b


def build(cls: int, mid: int, payload: bytes = b"") -> bytes:
    body = bytes([cls, mid]) + struct.pack("<H", len(payload)) + payload
    return SYNC + body + bytes(checksum(body))


def _g(fmt: str, p: bytes, off: int = 0):
    return struct.unpack_from("<" + fmt, p, off)


def decode_payload(cls: int, mid: int, p: bytes) -> tuple[str, list[tuple[str, str]]]:
    """(riepilogo, campi) per i messaggi comuni; ('', []) per gli altri."""
    n = len(p)
    f: list[tuple[str, str]] = []
    if (cls, mid) == (0x01, 0x07) and n >= 92:  # NAV-PVT
        (itow, yr, mo, dy, hh, mm, ss, valid, tacc, nano, fix, flags, flags2, nsv, lon, lat, hgt, hmsl, hacc, vacc,
         vn, ve, vd, gs, hm, sacc, hacc2, pdop) = _g("IHBBBBBBIiBBBBiiiiIIiiiiiIIH", p)[:28]
        carr = CARR_SOLN.get(flags >> 6 & 3, "")
        f += [("iTOW", f"{itow} ms"), ("Data/ora UTC", f"{yr:04d}-{mo:02d}-{dy:02d} {hh:02d}:{mm:02d}:{ss + nano / 1e9:06.3f}"),
              ("Validità", f"0x{valid:02X} (data {valid & 1}, ora {valid >> 1 & 1}, risolto {valid >> 2 & 1})"),
              ("Fix", f"{fix} {FIX_TYPE.get(fix, '?')}" + (f" {carr}" if carr else "")),
              ("Fix OK", str(flags & 1)), ("Satelliti usati", str(nsv)), ("Latitudine", f"{lat * 1e-7:.7f}°"),
              ("Longitudine", f"{lon * 1e-7:.7f}°"), ("Altezza ellissoidale", f"{hgt / 1000:.3f} m"),
              ("Altezza s.l.m.", f"{hmsl / 1000:.3f} m"), ("Accuratezza orizz.", f"{hacc / 1000:.3f} m"),
              ("Accuratezza vert.", f"{vacc / 1000:.3f} m"), ("Vel. N/E/D", f"{vn / 1000:.3f} / {ve / 1000:.3f} / {vd / 1000:.3f} m/s"),
              ("Vel. al suolo", f"{gs / 1000:.3f} m/s"), ("Rotta", f"{hm * 1e-5:.2f}°"), ("PDOP", f"{pdop * 0.01:.2f}")]
        return f"{FIX_TYPE.get(fix, fix)} sat={nsv} {lat * 1e-7:.6f},{lon * 1e-7:.6f} h={hmsl / 1000:.1f}m hAcc={hacc / 1000:.2f}m", f
    if (cls, mid) == (0x01, 0x02) and n >= 28:  # NAV-POSLLH
        itow, lon, lat, hgt, hmsl, hacc, vacc = _g("IiiiiII", p)
        f += [("iTOW", f"{itow} ms"), ("Latitudine", f"{lat * 1e-7:.7f}°"), ("Longitudine", f"{lon * 1e-7:.7f}°"),
              ("Altezza s.l.m.", f"{hmsl / 1000:.3f} m"), ("hAcc", f"{hacc / 1000:.3f} m"), ("vAcc", f"{vacc / 1000:.3f} m")]
        return f"{lat * 1e-7:.6f},{lon * 1e-7:.6f} h={hmsl / 1000:.1f}m", f
    if (cls, mid) == (0x01, 0x04) and n >= 18:  # NAV-DOP
        itow, *d = _g("IHHHHHHH", p)
        names = ("G", "P", "T", "V", "H", "N", "E")
        f += [(f"{k}DOP", f"{v * 0.01:.2f}") for k, v in zip(names, d)]
        return f"PDOP {d[1] * 0.01:.2f} HDOP {d[4] * 0.01:.2f} VDOP {d[3] * 0.01:.2f}", f
    if (cls, mid) == (0x01, 0x03) and n >= 16:  # NAV-STATUS
        itow, fix, flags, fstat, fl2, ttff, msss = _g("IBBBBII", p)
        f += [("gpsFix", f"{fix} {FIX_TYPE.get(fix, '?')}"), ("flags", f"0x{flags:02X}"), ("TTFF", f"{ttff} ms"),
              ("Tempo da avvio", f"{msss} ms")]
        return f"fix {FIX_TYPE.get(fix, fix)} TTFF {ttff} ms", f
    if (cls, mid) == (0x01, 0x21) and n >= 20:  # NAV-TIMEUTC
        itow, tacc, nano, yr, mo, dy, hh, mm, ss, valid = _g("IIiHBBBBBB", p)
        f += [("Data/ora", f"{yr:04d}-{mo:02d}-{dy:02d} {hh:02d}:{mm:02d}:{ss:02d}"), ("tAcc", f"{tacc} ns"),
              ("Validità", f"0x{valid:02X}")]
        return f"{yr:04d}-{mo:02d}-{dy:02d} {hh:02d}:{mm:02d}:{ss:02d} tAcc {tacc} ns", f
    if (cls, mid) == (0x01, 0x35) and n >= 8:  # NAV-SAT
        itow, ver, nsv = _g("IBB", p)
        nsv = min(nsv, (n - 8) // 12)
        used = 0
        cnos = []
        for i in range(nsv):
            g, sv, cno, elev, azim, res, flags = _g("BBBbhhI", p, 8 + 12 * i)
            used += flags >> 3 & 1
            cnos.append(cno)
            f.append((f"{GNSS_ID.get(g, g)} {sv}", f"C/N0 {cno} dBHz  el {elev}°  az {azim}°  {'usato' if flags >> 3 & 1 else ''}"))
        avg = sum(cnos) / len(cnos) if cnos else 0
        return f"{nsv} satelliti ({used} usati), C/N0 medio {avg:.1f} dBHz", f
    if (cls, mid) == (0x05, 0x01) and n >= 2:
        k = NAMES.get((p[0], p[1]), f"{p[0]:02X}-{p[1]:02X}")
        return f"ACK di {k}", [("Messaggio confermato", k)]
    if (cls, mid) == (0x05, 0x00) and n >= 2:
        k = NAMES.get((p[0], p[1]), f"{p[0]:02X}-{p[1]:02X}")
        return f"NAK di {k}", [("Messaggio rifiutato", k)]
    if (cls, mid) == (0x0A, 0x04) and n >= 40:  # MON-VER
        sw = p[:30].split(b"\0")[0].decode("latin-1")
        hw = p[30:40].split(b"\0")[0].decode("latin-1")
        ext = [p[i:i + 30].split(b"\0")[0].decode("latin-1") for i in range(40, n - 29, 30)]
        f += [("Software", sw), ("Hardware", hw)] + [(f"Estensione {i}", e) for i, e in enumerate(ext)]
        return f"sw {sw} hw {hw}", f
    if (cls, mid) == (0x02, 0x15) and n >= 16:  # RXM-RAWX
        tow, week, leap, nmeas, stat, ver = _g("dHbBBB", p)
        f += [("rcvTow", f"{tow:.9f} s"), ("Settimana", str(week)), ("Leap s", str(leap)), ("Misure", str(nmeas)),
              ("recStat", f"0x{stat:02X}")]
        for i in range(min(nmeas, (n - 16) // 32)):
            pr, cp, do, g, sv, sig, fq, lock, cno = _g("ddfBBBBHB", p, 16 + 32 * i)
            f.append((f"{GNSS_ID.get(g, g)} {sv} sig{sig}", f"PR {pr:.3f} m  CP {cp:.3f}  Doppler {do:.2f} Hz  C/N0 {cno}  lock {lock} ms"))
        return f"TOW {tow:.3f} sett. {week}, {nmeas} misure", f
    if (cls, mid) == (0x02, 0x13) and n >= 8:  # RXM-SFRBX
        g, sv, sig, fq, nw, chn, ver = _g("BBBBBBB", p)
        f += [("GNSS", GNSS_ID.get(g, str(g))), ("SV", str(sv)), ("Segnale", str(sig)), ("Parole", str(nw)), ("Canale", str(chn))]
        return f"{GNSS_ID.get(g, g)} {sv}: {nw} parole", f
    if (cls, mid) == (0x06, 0x08) and n >= 6:  # CFG-RATE
        meas, nav, tref = _g("HHH", p)
        return f"misura ogni {meas} ms, nav ogni {nav} cicli", [("measRate", f"{meas} ms"), ("navRate", str(nav)), ("timeRef", str(tref))]
    if (cls, mid) == (0x06, 0x01) and n >= 3:  # CFG-MSG
        k = NAMES.get((p[0], p[1]), f"{p[0]:02X}-{p[1]:02X}")
        rates = ", ".join(str(x) for x in p[2:])
        return f"{k} rate {rates}", [("Messaggio", k), ("Rate per porta", rates)]
    if cls == 0x04 and n:  # INF-*
        text = p.decode("latin-1", "replace")
        return text, [("Testo", text)]
    return "", f


class Ubx(Decoder):
    """UBX con ricostruzione dei frame fra chunk (un frame può essere spezzato) e resincronizzazione:
    i byte che non appartengono a un frame UBX (es. righe NMEA sulla stessa porta) sono ignorati in silenzio.
    Un frame con checksum errato è riportato come non valido."""

    name = "ubx"
    label = "u-blox UBX"

    def __init__(self) -> None:
        self._buf: dict[StreamKey, tuple[int, bytearray]] = {}

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: Chunk) -> list[Message]:
        key = (chunk.channel, chunk.direction)
        ts, buf = self._buf.get(key, (chunk.ts_ns, bytearray()))
        if not buf:
            ts = chunk.ts_ns
        buf += chunk.data
        out: list[Message] = []
        while True:
            i = buf.find(SYNC)
            if i < 0:
                del buf[:max(0, len(buf) - 1)]  # può restare 0xB5 a metà
                break
            if i:
                del buf[:i]
            if len(buf) < 8:
                break
            ln = buf[4] | buf[5] << 8
            if ln > MAX_PAYLOAD:
                del buf[:2]
                continue
            total = 8 + ln
            if len(buf) < total:
                break
            frame = bytes(buf[:total])
            ck = checksum(frame[2:-2])
            if ck == (frame[-2], frame[-1]):
                out.append(self._msg(chunk, ts, frame))
                del buf[:total]
            else:
                out.append(Message(ts, chunk.channel, chunk.direction, self.label,
                                   f"frame {frame[2]:02X}-{frame[3]:02X} checksum errato", (("Hex", frame.hex(" ")),),
                                   frame, ok=False, note="checksum errato", kind="invalid", key="invalid"))
                del buf[:2]  # risincronizza dopo la sync appena scartata
            ts = chunk.ts_ns
        if buf:
            self._buf[key] = (ts, buf)
        else:
            self._buf.pop(key, None)
        return out

    def _msg(self, c: Chunk, ts: int, frame: bytes) -> Message:
        cls, mid = frame[2], frame[3]
        payload = frame[6:-2]
        name = NAMES.get((cls, mid), f"{cls:02X}-{mid:02X}")
        summary, fields = decode_payload(cls, mid, payload)
        head = [("Messaggio", f"{name} (classe 0x{cls:02X}, id 0x{mid:02X})"), ("Lunghezza payload", str(len(payload)))]
        if not fields and payload:
            head.append(("Payload", payload[:64].hex(" ") + ("…" if len(payload) > 64 else "")))
        kind = "ack" if cls == 5 else "message"
        return Message(ts, c.channel, c.direction, self.label, f"{name}  {summary}".rstrip(), tuple(head + fields), frame,
                       ok=True, kind=kind, key=f"UBX {name}")
