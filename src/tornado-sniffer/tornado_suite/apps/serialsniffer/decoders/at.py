"""Comandi AT e risposte: classificazione delle righe e accoppiamento comando → codice finale con latenza."""
from __future__ import annotations

import re
from typing import Optional

from ..chunk import Chunk, RX, TX
from .base import Decoder, Message, StreamKey
from .lines import LineSplitter

FINAL_OK = {"OK"}
FINAL_ERR_RE = re.compile(r"^(ERROR|NO CARRIER|NO ANSWER|NO DIALTONE|BUSY|ABORTED|\+CME ERROR:.*|\+CMS ERROR:.*|COMMAND NOT SUPPORT|"
                          r"SEND FAIL|FAIL|CONNECT FAIL)$", re.I)
FINAL_OTHER_RE = re.compile(r"^(CONNECT(\s+\d+)?|READY|SEND OK|SHUT OK|CLOSE OK)$", re.I)
CMD_RE = re.compile(r"^(AT|at)(\S.*)?$")
INFO_RE = re.compile(r"^(\+[A-Za-z0-9#$%&*^_!]+|\^[A-Za-z0-9]+|#[A-Za-z0-9]+|\$[A-Za-z0-9]+)\s*:?\s*(.*)$")
URC_NAMES = {"RING", "NO CARRIER", "+CMTI", "+CREG", "+CEREG", "+CGREG", "+CLIP", "+CUSD", "+CIEV", "+CMT",
             "+CDSI", "+CBM", "+QIURC", "+QIND", "+CPIN", "+CSQ"}
MAX_PENDING_NS = 30_000_000_000
ECHO_WINDOW_NS = 1_000_000_000  # stessa riga sullo stesso flusso entro 1 s dal comando = eco


def cmd_key(text: str) -> str:
    """AT+CMGS="123" -> AT+CMGS ; AT+CSQ? -> AT+CSQ"""
    return text.split("=", 1)[0].rstrip("?").upper()


class _Cmd:
    __slots__ = ("ts", "text", "lines", "stream", "key")

    def __init__(self, ts: int, text: str, stream: StreamKey) -> None:
        self.ts, self.text, self.lines, self.stream, self.key = ts, text, 0, stream, cmd_key(text)


class AtLines(Decoder):
    """Comandi AT, risposte informative, codici finali e URC.

    Il comando è una riga che inizia con AT. Le righe successive dello stesso canale fino al codice finale
    (OK, ERROR, +CME ERROR…) sono risposte; il codice finale riporta il comando e la latenza. Le righe
    informative fuori da un comando in corso sono URC. Una riga identica al comando, entro 1 s e prima di
    qualunque risposta, è l'eco (ATE1), anche se passa dallo stesso flusso del comando (bus condiviso). Se non c'è eco né direzione, funziona comunque:
    l'accoppiamento usa l'ultimo comando pendente sul canale."""

    name = "at"
    label = "AT / righe di testo"
    options = {"echo": "il dispositivo ripete il comando (ATE1): la riga identica al comando viene marcata eco",
               "per_channel": "accoppia comando e risposta solo sullo stesso canale (default no: con due adattatori "
                              "il comando e la risposta passano da canali diversi)"}

    def __init__(self, echo: bool = True, per_channel: bool = False) -> None:
        self.echo = echo
        self.per_channel = per_channel
        self._lines = LineSplitter()
        self._pending: dict[int, _Cmd] = {}

    def reset(self) -> None:
        self._lines.reset()
        self._pending.clear()

    def feed(self, chunk: Chunk) -> list[Message]:
        return [m for t in self._lines.feed(chunk) for m in self._line(*t)]

    def flush(self, now_ns: Optional[int] = None) -> list[Message]:
        return [m for t in self._lines.flush(now_ns) for m in self._line(*t)]

    # ----------------------------------------------------------------- interno
    def _line(self, key: StreamKey, ts: int, raw: bytes, complete: bool) -> list[Message]:
        ch, d = key
        text = raw.decode("utf-8", "replace").strip()
        note = "" if complete else "senza terminatore"
        pk = ch if self.per_channel else 0
        pend = self._pending.get(pk)
        if pend is not None and ts - pend.ts > MAX_PENDING_NS:
            self._pending.pop(pk, None)
            pend = None

        is_echo = (pend is not None and self.echo and text == pend.text and pend.lines == 0
                   and (key != pend.stream or ts - pend.ts < ECHO_WINDOW_NS))
        if CMD_RE.match(text) and not is_echo:
            self._pending[pk] = _Cmd(ts, text, key)
            return [Message(ts, ch, d, self.label, f"⇥ {text}", (("Comando", text), *self._parse_cmd(text)), raw,
                            note=note, kind="command", key=cmd_key(text))]
        if is_echo:
            return [Message(ts, ch, d, self.label, f"echo {text}", (("Eco", text),), raw, note=note, kind="echo",
                            key=cmd_key(text))]
        if text in FINAL_OK or FINAL_ERR_RE.match(text) or FINAL_OTHER_RE.match(text):
            err = bool(FINAL_ERR_RE.match(text))
            lat = ts - pend.ts if pend is not None else None
            cmd = pend.text if pend is not None else ""
            fkey = pend.key if pend is not None else "(risposta senza comando)"
            if pend is not None:
                self._pending.pop(pk, None)
            summ = f"← {text}" + (f"   ({cmd}, {lat / 1e6:.1f} ms)" if lat is not None else "   [senza comando]")
            f = [("Codice finale", text)]
            if cmd:
                f += [("Comando", cmd), ("Latenza", f"{lat / 1e6:.3f} ms")]
            f += self._cme(text)
            return [Message(ts, ch, d, self.label, summ, tuple(f), raw, ok=not err, note=note or ("errore" if err else ""),
                            kind="final", pair_ns=lat, key=fkey)]
        m = INFO_RE.match(text)
        if m:
            tag, args = m.group(1), m.group(2)
            urc = pend is None or tag.upper() in URC_NAMES and pend.lines == 0 and not self._related(pend.text, tag)
            if pend is not None and not urc:
                pend.lines += 1
            f = [("Etichetta", tag), ("Argomenti", args)]
            parts = [p.strip().strip('"') for p in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', args)] if args else []
            f += [(f"arg{i}", p) for i, p in enumerate(parts[:16])]
            return [Message(ts, ch, d, self.label, ("URC " if urc else "← ") + text, tuple(f), raw, note=note,
                            kind="urc" if urc else "info", key=("URC " + tag) if urc else (pend.key if pend else tag))]
        if pend is not None:
            pend.lines += 1
            return [Message(ts, ch, d, self.label, f"← {text}", (("Riga", text),), raw, note=note, kind="info",
                            key=pend.key)]
        return [Message(ts, ch, d, self.label, text, (("Riga", text),), raw, note=note, kind="text", key="testo")]

    @staticmethod
    def _related(cmd: str, tag: str) -> bool:
        return tag.upper().lstrip("+") in cmd.upper()

    @staticmethod
    def _parse_cmd(text: str) -> list[tuple[str, str]]:
        body = text[2:]
        if body.endswith("=?"):
            return [("Tipo", "test"), ("Nome", body[:-2])]
        if body.endswith("?"):
            return [("Tipo", "lettura"), ("Nome", body[:-1])]
        if "=" in body:
            name, _, args = body.partition("=")
            return [("Tipo", "impostazione"), ("Nome", name), ("Argomenti", args)]
        return [("Tipo", "esecuzione"), ("Nome", body or "(AT)")]

    @staticmethod
    def _cme(text: str) -> list[tuple[str, str]]:
        m = re.match(r"^\+(CME|CMS) ERROR:\s*(.*)$", text, re.I)
        return [("Codice errore", m.group(2))] if m else []
