"""GRBL (1.1): comandi G-code/`$`, comandi realtime, `ok`/`error:N`, ALARM, report di stato `<...>`, messaggi."""
from __future__ import annotations

import re
from collections import deque
from typing import Optional

from ..chunk import Chunk
from .base import Decoder, Message, StreamKey
from .lines import LineSplitter

ERRORS = {
    1: "G-code: lettera comando attesa", 2: "Formato numerico errato", 3: "Comando `$` non valido", 4: "Valore negativo",
    5: "Homing non abilitato", 6: "Durata impulso passo troppo breve", 7: "Lettura EEPROM fallita (reset)",
    8: "Comando `$` ammesso solo da fermo", 9: "G-code bloccato (allarme o jog)", 10: "Limite soft: target fuori corsa",
    11: "Riga troppo lunga", 12: "Frequenza passi massima superata", 13: "Check door", 14: "Riga più lunga del buffer",
    15: "Jog: target oltre la corsa", 16: "Comando di jog non valido", 17: "Laser: richiede PWM", 20: "Comando G-code non supportato",
    21: "Violazione gruppo modale", 22: "Feed rate non definito", 23: "Valore intero atteso", 24: "Conflitto di assi",
    25: "Parola ripetuta", 26: "Nessuna parola d'asse", 27: "Numero di riga non valido", 28: "Valore mancante",
    29: "Sistema coordinate non supportato", 30: "G59.x non supportato", 31: "Assi non usati", 32: "Arco G2/G3 senza assi",
    33: "Target non valido", 34: "Errore raggio arco", 35: "Arco senza offset nel piano", 36: "Parole non usate",
    37: "Offset lunghezza utensile (G43.1) non assegnato", 38: "Numero utensile oltre il massimo",
}
ALARMS = {
    1: "Limite hard", 2: "Limite soft", 3: "Ciclo interrotto in movimento", 4: "Probe fallito (stato iniziale)",
    5: "Probe fallito (nessun contatto)", 6: "Homing fallito (reset)", 7: "Homing fallito (porta)",
    8: "Homing fallito (pull-off)", 9: "Homing fallito (finecorsa non trovato)", 10: "Homing fallito (finecorsa doppio asse)",
}
REALTIME = {
    ord("?"): "status report", ord("!"): "feed hold", ord("~"): "cycle start / resume", 0x18: "soft reset (Ctrl-X)",
    0x84: "safety door", 0x85: "jog cancel", 0x90: "feed override 100%", 0x91: "feed +10%", 0x92: "feed -10%",
    0x93: "feed +1%", 0x94: "feed -1%", 0x95: "rapid 100%", 0x96: "rapid 50%", 0x97: "rapid 25%", 0x99: "spindle 100%",
    0x9A: "spindle +10%", 0x9B: "spindle -10%", 0x9C: "spindle +1%", 0x9D: "spindle -1%", 0x9E: "spindle stop",
    0xA0: "flood coolant", 0xA1: "mist coolant",
}
_WORD = r"[A-Za-z][-+]?(?:\d+\.?\d*|\.\d+)"
GCODE_RE = re.compile(rf"^(?:N\d+\s*)?{_WORD}(?:\s*{_WORD})*(?:\s*[;(].*)?$", re.I)
SYS_RE = re.compile(r"^\$(?:[A-Za-z$#]|\d+=[-\d.]+|J=.*)")
SETTING_RE = re.compile(r"^\$(\d+)=([-\d.]+)(?:\s+\((.*)\))?$")
STATUS_RE = re.compile(r"^<([A-Za-z]+)(?::(\d+))?((?:\|[^|>]*)*)>$")
WELCOME_RE = re.compile(r"^Grbl\s+(\S+)")
MAX_PENDING = 64


def parse_status(text: str) -> Optional[tuple[str, list[tuple[str, str]]]]:
    m = STATUS_RE.match(text)
    if not m:
        return None
    state, sub, rest = m.groups()
    fields: list[tuple[str, str]] = [("Stato", state + (f":{sub}" if sub else ""))]
    summary = [state]
    for part in rest.split("|")[1:]:
        k, _, v = part.partition(":")
        label = {"MPos": "Posizione macchina", "WPos": "Posizione lavoro", "Bf": "Buffer (blocchi, byte liberi)",
                 "FS": "Feed, velocità mandrino", "F": "Feed", "WCO": "Offset coordinate lavoro",
                 "Ov": "Override (feed, rapido, mandrino)", "Pn": "Pin attivi", "A": "Accessori"}.get(k, k)
        fields.append((label, v))
        if k in ("MPos", "WPos"):
            summary.append(f"{k} {v}")
        elif k == "FS":
            summary.append(f"FS {v}")
    return " ".join(summary), fields


class Grbl(Decoder):
    """GRBL 1.1. I comandi in attesa sono accodati (GRBL ne tiene più di uno nel buffer): ogni `ok`/`error:N`
    chiude il più vecchio e riporta la latenza. I caratteri realtime (`?`, `!`, `~`, Ctrl-X, override) sono
    riconosciuti anche se non terminati da a capo."""

    name = "grbl"
    label = "GRBL"
    options = {}

    def __init__(self) -> None:
        self._lines = LineSplitter()
        self._queue: deque[tuple[int, str, str]] = deque(maxlen=MAX_PENDING)  # (ts, testo, chiave)

    def reset(self) -> None:
        self._lines.reset()
        self._queue.clear()

    def feed(self, chunk: Chunk) -> list[Message]:
        data = chunk.data
        stripped = data.strip(b"\r\n ")
        # `?`, `!`, `~` sono realtime solo se il chunk è fatto soltanto di caratteri realtime (altrimenti possono far
        # parte di testo/commenti); i byte non stampabili (Ctrl-X, override >= 0x80) lo sono sempre.
        if stripped and all(b in REALTIME for b in stripped):
            return [self._realtime(chunk, b) for b in stripped]
        out: list[Message] = []
        keep = bytearray()
        for b in data:
            if b in REALTIME and (b == 0x18 or b >= 0x80):
                out.append(self._realtime(chunk, b))
            else:
                keep.append(b)
        if keep:
            out += [m for t in self._lines.feed(Chunk(chunk.ts_ns, chunk.channel, chunk.direction, bytes(keep)))
                    for m in self._line(*t)]
        return out

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        return [m for t in self._lines.flush(now_ns) for m in self._line(*t)]

    # --------------------------------------------------------------- interno
    def _realtime(self, c: Chunk, b: int) -> Message:
        name = REALTIME[b]
        shown = chr(b) if 32 < b < 127 else f"0x{b:02X}"
        return Message(c.ts_ns, c.channel, c.direction, self.label, f"⚡ {shown}  {name}", (("Comando realtime", name),),
                       bytes([b]), kind="realtime", key=f"realtime {name}")

    def _line(self, stream: StreamKey, ts: int, raw: bytes, complete: bool) -> list[Message]:
        ch, d = stream
        text = raw.decode("latin-1").strip()
        note = "" if complete else "senza terminatore"

        def M(summary: str, fields: tuple, **k) -> list[Message]:
            k["note"] = note or k.get("note", "")
            return [Message(ts, ch, d, self.label, summary, fields, raw, **k)]

        if text == "ok" or text.startswith("error:"):
            cmd = self._queue.popleft() if self._queue else None
            lat = ts - cmd[0] if cmd else None
            key = cmd[2] if cmd else "(risposta senza comando)"
            suffix = f"   ({cmd[1]}, {lat / 1e6:.1f} ms)" if cmd else "   [senza comando]"
            f = [("Risposta", text)]
            if cmd:
                f += [("Comando", cmd[1]), ("Latenza", f"{lat / 1e6:.3f} ms")]
            if text == "ok":
                return M("← ok" + suffix, tuple(f), ok=True, kind="response", pair_ns=lat, key=key)
            try:
                code = int(text.split(":", 1)[1])
            except ValueError:
                code = -1
            desc = ERRORS.get(code, "errore sconosciuto")
            f.append(("Errore", f"{code} {desc}"))
            return M(f"← error:{code} {desc}" + suffix, tuple(f), ok=False, note="errore GRBL", kind="error", pair_ns=lat, key=key)
        if text.startswith("ALARM:"):
            try:
                code = int(text.split(":", 1)[1])
            except ValueError:
                code = -1
            desc = ALARMS.get(code, "allarme sconosciuto")
            return M(f"ALARM:{code} {desc}", (("Allarme", f"{code} {desc}"),), ok=False, note="allarme", kind="alarm", key="alarm")
        st = parse_status(text)
        if st:
            return M(f"<{st[0]}>", tuple(st[1]), kind="status", key="status")
        w = WELCOME_RE.match(text)
        if w:
            self._queue.clear()
            return M(f"avvio Grbl {w.group(1)}", (("Versione", w.group(1)), ("Testo", text)), kind="welcome", key="welcome")
        if text.startswith("[") and text.endswith("]"):
            tag, _, val = text[1:-1].partition(":")
            return M(f"[{tag}] {val}", (("Tipo", tag), ("Valore", val)), kind="message", key=f"[{tag}]")
        s = SETTING_RE.match(text)
        if s and (s.group(3) or (self._queue and self._queue[0][2] == "$$")):  # valore restituito da GRBL, non comando
            num, val, desc = s.groups()
            return M(f"${num} = {val}" + (f"  ({desc})" if desc else ""),
                     (("Impostazione", f"${num}"), ("Valore", val), ("Descrizione", desc or "")), kind="setting", key="setting")
        if SYS_RE.match(text) or GCODE_RE.match(text) or text.startswith(";") or (text.startswith("(") and text.endswith(")")):
            key = self._cmd_key(text)
            self._queue.append((ts, text, key))
            return M(f"⇥ {text}", (("Comando", text), ("Tipo", "sistema ($)" if text.startswith("$") else "G-code")),
                     kind="command", key=key)
        if text:
            return M(text, (("Riga", text),), kind="text", key="testo")
        return []

    @staticmethod
    def _cmd_key(text: str) -> str:
        if text.startswith("$"):
            return "$" + (re.match(r"\$(\$|[A-Za-z]+|\d+|J)", text) or re.match(r"\$()", text)).group(1)
        m = re.match(r"^(?:N\d+\s*)?([A-Za-z][-+]?\d+\.?\d*)", text, re.I)
        return m.group(1).upper() if m else "gcode"
