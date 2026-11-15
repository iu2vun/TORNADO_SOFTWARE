"""NMEA 0183 (e sentenze incapsulate !AIVDM/!AIVDO): checksum e decodifica dei messaggi comuni."""
from __future__ import annotations

from typing import Optional

from ..chunk import Chunk
from .base import Decoder, Message
from .lines import LineSplitter

TALKERS = {
    "GP": "GPS", "GL": "GLONASS", "GA": "Galileo", "GB": "BeiDou", "BD": "BeiDou", "GQ": "QZSS", "GN": "GNSS multi",
    "II": "Integrated Instr.", "IN": "Integrated Nav.", "HE": "Gyro (north)", "HC": "Compass", "SD": "Sounder",
    "WI": "Meteo", "AI": "AIS", "CD": "DSC", "EC": "ECDIS", "P": "Proprietario",
}
SENTENCES = {
    "GGA": "Fix data", "RMC": "Recommended minimum", "GLL": "Geographic position", "VTG": "Course/speed",
    "GSA": "DOP e satelliti attivi", "GSV": "Satelliti in vista", "ZDA": "Data e ora", "GBS": "Errori satelliti",
    "GST": "Statistiche errore", "HDT": "Heading vero", "HDG": "Heading magnetico", "DBT": "Profondità",
    "MWV": "Vento", "VDM": "AIS messaggio", "VDO": "AIS proprio", "TXT": "Testo", "GNS": "Fix GNSS",
    "ROT": "Rateo di virata", "RMB": "Navigazione verso waypoint", "BWC": "Rotta a waypoint", "DTM": "Datum",
    "MTW": "Temperatura acqua", "VHW": "Velocità sull'acqua", "XDR": "Trasduttore",
}
QUALITY = {"0": "fix non valido", "1": "GPS", "2": "DGPS", "3": "PPS", "4": "RTK fixed", "5": "RTK float",
           "6": "stimato", "7": "manuale", "8": "simulato"}
MODE = {"A": "autonomo", "D": "differenziale", "E": "stimato", "F": "RTK float", "R": "RTK fixed",
        "M": "manuale", "S": "simulato", "N": "dato non valido", "P": "preciso"}


def checksum(body: str) -> int:
    c = 0
    for ch in body.encode("latin-1", "replace"):
        c ^= ch
    return c


def with_checksum(body: str, start: str = "$") -> str:
    """Compone una sentenza completa da `body` (senza $ e *hh)."""
    return f"{start}{body}*{checksum(body):02X}"


def _coord(value: str, hemi: str, deg_digits: int) -> Optional[float]:
    if not value or not hemi:
        return None
    try:
        deg = int(value[:deg_digits])
        minutes = float(value[deg_digits:])
    except ValueError:
        return None
    v = deg + minutes / 60.0
    return -v if hemi in ("S", "W") else v


def _f(s: str) -> Optional[float]:
    try:
        return float(s)
    except ValueError:
        return None


def _time(s: str) -> str:
    return f"{s[0:2]}:{s[2:4]}:{s[4:]}" if len(s) >= 6 and s[:6].isdigit() else s


def _date(s: str) -> str:
    if len(s) != 6 or not s.isdigit():
        return s
    yy = int(s[4:6])
    return f"{2000 + yy if yy < 80 else 1900 + yy}-{s[2:4]}-{s[0:2]}"


def _pos(lat: Optional[float], lon: Optional[float]) -> str:
    return "posizione n/d" if lat is None or lon is None else f"{lat:.6f}, {lon:.6f}"


def decode_fields(kind: str, f: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """(riepilogo, campi) per i tipi comuni; ([], '') per gli altri."""
    g = lambda i: f[i] if i < len(f) else ""  # noqa: E731
    if kind == "GGA":
        lat, lon = _coord(g(1), g(2), 2), _coord(g(3), g(4), 3)
        fields = [("Ora UTC", _time(g(0))), ("Latitudine", "" if lat is None else f"{lat:.7f}"),
                  ("Longitudine", "" if lon is None else f"{lon:.7f}"),
                  ("Qualità", f"{g(5)} {QUALITY.get(g(5), '')}".strip()), ("Satelliti", g(6)), ("HDOP", g(7)),
                  ("Quota (m)", f"{g(8)} {g(9)}".strip()), ("Altezza geoide (m)", f"{g(10)} {g(11)}".strip()),
                  ("Età DGPS (s)", g(12)), ("ID stazione DGPS", g(13))]
        return f"{_time(g(0))}  {_pos(lat, lon)}  q={g(5)} sat={g(6)} hdop={g(7)} alt={g(8)}{g(9)}", fields
    if kind == "RMC":
        lat, lon = _coord(g(2), g(3), 2), _coord(g(4), g(5), 3)
        valid = g(1) == "A"
        fields = [("Ora UTC", _time(g(0))), ("Stato", "valido" if valid else "avviso (non valido)"),
                  ("Latitudine", "" if lat is None else f"{lat:.7f}"),
                  ("Longitudine", "" if lon is None else f"{lon:.7f}"), ("Velocità (nodi)", g(6)),
                  ("Rotta (°)", g(7)), ("Data", _date(g(8))), ("Var. magnetica", f"{g(9)} {g(10)}".strip()),
                  ("Modo", f"{g(11)} {MODE.get(g(11), '')}".strip())]
        kn = _f(g(6))
        spd = "" if kn is None else f"{kn:.1f} kn ({kn * 1.852:.1f} km/h)"
        return f"{_date(g(8))} {_time(g(0))}  {'A' if valid else 'V'}  {_pos(lat, lon)}  {spd}  rotta {g(7)}°", fields
    if kind == "GLL":
        lat, lon = _coord(g(0), g(1), 2), _coord(g(2), g(3), 3)
        return f"{_time(g(4))}  {_pos(lat, lon)}  {g(5)}", [
            ("Latitudine", "" if lat is None else f"{lat:.7f}"), ("Longitudine", "" if lon is None else f"{lon:.7f}"),
            ("Ora UTC", _time(g(4))), ("Stato", g(5)), ("Modo", g(6))]
    if kind == "VTG":
        return f"rotta {g(0)}° vera, {g(4)} kn, {g(6)} km/h", [
            ("Rotta vera (°)", g(0)), ("Rotta magnetica (°)", g(2)), ("Velocità (nodi)", g(4)),
            ("Velocità (km/h)", g(6)), ("Modo", g(8))]
    if kind == "ZDA":
        return f"{g(3)}-{g(2)}-{g(1)} {_time(g(0))} UTC", [
            ("Ora UTC", _time(g(0))), ("Giorno", g(1)), ("Mese", g(2)), ("Anno", g(3)),
            ("Offset locale (h)", g(4)), ("Offset locale (min)", g(5))]
    if kind == "GSA":
        sats = [x for x in f[2:14] if x]
        return f"modo {g(0)}, fix {g(1)}D, {len(sats)} sat, PDOP {g(14)} HDOP {g(15)} VDOP {g(16)}", [
            ("Modo", g(0)), ("Fix", {"1": "n/d", "2": "2D", "3": "3D"}.get(g(1), g(1))),
            ("Satelliti usati", ",".join(sats)), ("PDOP", g(14)), ("HDOP", g(15)), ("VDOP", g(16)),
            ("System ID", g(17))]
    if kind == "GSV":
        sats = []
        for i in range(3, len(f) - 3, 4):
            sats.append(f"PRN{f[i]} el={f[i + 1]}° az={f[i + 2]}° snr={f[i + 3]}")
        return f"msg {g(1)}/{g(0)}, {g(2)} sat in vista", [
            ("Messaggi", f"{g(1)}/{g(0)}"), ("Satelliti in vista", g(2))] + [(f"Sat {i + 1}", s) for i, s in enumerate(sats)]
    if kind == "HDT":
        return f"heading vero {g(0)}°", [("Heading vero (°)", g(0))]
    if kind == "DBT":
        return f"profondità {g(2)} m", [("Piedi", g(0)), ("Metri", g(2)), ("Braccia", g(4))]
    if kind == "MWV":
        return f"vento {g(0)}° {g(2)} {g(3)}", [("Angolo (°)", g(0)), ("Riferimento", g(1)),
                                                 ("Velocità", f"{g(2)} {g(3)}"), ("Stato", g(4))]
    if kind == "TXT":
        return f"{g(3)}", [("Messaggio", g(3)), ("Tipo", g(2)), ("Numero", f"{g(1)}/{g(0)}")]
    if kind in ("VDM", "VDO"):
        return f"AIS frammento {g(1)}/{g(0)} canale {g(3)}, payload {len(g(4))} car.", [
            ("Frammento", f"{g(1)}/{g(0)}"), ("ID sequenza", g(2)), ("Canale", g(3)), ("Payload (6 bit)", g(4)),
            ("Bit di riempimento", g(5))]
    return "", []


class Nmea(Decoder):
    """NMEA 0183 su righe terminate da CR/LF, con verifica del checksum XOR."""

    name = "nmea"
    label = "NMEA 0183"
    options = {"require_checksum": "segnala come errore le sentenze senza *hh (default no)",
               "ignore_noise": "scarta in silenzio le righe che non iniziano con $ o ! (flussi misti NMEA+binario)"}

    def __init__(self, require_checksum: bool = False, ignore_noise: bool = False) -> None:
        self.require_checksum = require_checksum
        self.ignore_noise = ignore_noise
        self._lines = LineSplitter()

    def reset(self) -> None:
        self._lines.reset()

    def feed(self, chunk: Chunk) -> list[Message]:
        return [m for m in (self._line(*t) for t in self._lines.feed(chunk)) if m is not None]

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        return [m for m in (self._line(*t) for t in self._lines.flush(now_ns)) if m is not None]

    def _line(self, key, ts: int, raw: bytes, complete: bool) -> Optional[Message]:
        ch, d = key
        text = raw.decode("latin-1")
        s = text.strip()
        if not s or s[0] not in "$!":
            if self.ignore_noise:
                return None
            return Message(ts, ch, d, self.label, f"non NMEA: {s[:60]}", (("Testo", s),), raw, ok=False,
                           note="manca $ o !", kind="invalid", key="invalid")
        body, star, cs = s[1:].partition("*")
        ok, note = True, ""
        if star:
            try:
                want = int(cs[:2], 16)
            except ValueError:
                ok, note = False, "checksum non esadecimale"
            else:
                got = checksum(body)
                if got != want:
                    ok, note = False, f"checksum errato (atteso {got:02X}, ricevuto {want:02X})"
        elif self.require_checksum:
            ok, note = False, "checksum assente"
        if not complete:
            note = (note + "; " if note else "") + "senza terminatore"
        parts = body.split(",")
        addr = parts[0]
        fields = parts[1:]
        if addr.startswith("P"):
            talker, kind = "P", addr[1:] or addr
            talker_name = f"proprietario ({addr})"
        else:
            talker, kind = addr[:2], addr[2:]
            talker_name = TALKERS.get(talker, talker)
        summary, f = decode_fields(kind, fields) if ok or star else ("", [])
        if not summary:
            summary = ",".join(fields)[:80]
        desc = SENTENCES.get(kind, "")
        head = [("Sentenza", f"{addr} {desc}".strip()), ("Talker", talker_name)]
        if star:
            head.append(("Checksum", f"*{cs[:2]} " + ("ok" if ok else "ERRATO")))
        return Message(ts, ch, d, self.label, f"{addr:<6} {summary}".rstrip() + ("" if ok else "  [!]"),
                       tuple(head + f), raw, ok=ok, note=note, kind="sentence", key=addr)
