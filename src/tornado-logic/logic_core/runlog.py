"""Registro (log) delle acquisizioni ripetute. Nessuna dipendenza da Qt.

Ogni sessione crea una cartella ``Log_AAAAMMGG_HHMMSS`` con:
  eventi.log        avvio/fine, una riga per ciclo, trigger mancati, errori dei decoder (testo con data e ora)
  decodifica.csv    una riga per ogni annotazione dei decoder (UART, SPI, I2C, seriale), separatore ';'
  cicli.csv         indice: una riga per ciclo con campioni, trigger, conteggi e nome del file grezzo
  catture/          cattura_000001.npz ... (si riaprono con "Apri"); si tengono le ultime ``max_raw``

L'ora assoluta di una riga è l'ora di inizio dell'acquisizione più il tempo dell'annotazione dentro la
cattura: con trigger è un'approssimazione (la finestra viene cercata dopo l'avvio).
"""
from __future__ import annotations

import csv
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .analysis import DecoderConfig, run_decoders, run_measures
from .capture import N_CHANNELS, Capture
from .export import save_npz

DEC_HEADER = ("ciclo", "ora", "t_s", "decoder", "dato", "dettaglio", "errore")
CYCLE_HEADER = ("ciclo", "ora_inizio", "campioni", "samplerate_hz", "trigger_s", "annotazioni", "errori",
                "glitch", "file")


@dataclass(frozen=True)
class LogOptions:
    folder: str
    decoded: bool = True
    raw: bool = True
    events: bool = True
    max_raw: int = 200          # catture grezze conservate (le più vecchie vengono cancellate); 0 = tutte

    def any(self) -> bool:
        return self.decoded or self.raw or self.events


def _hz(v: float) -> str:
    for unit, k in (("GS/s", 1e9), ("MS/s", 1e6), ("kS/s", 1e3)):
        if v >= k:
            return f"{v / k:g} {unit}"
    return f"{v:g} S/s"


def _stamp(t: datetime) -> str:
    return t.isoformat(sep=" ", timespec="milliseconds")


class RunLog:
    """Scrive una sessione di log. Thread-safe: log_capture() può essere chiamato da thread diversi."""

    def __init__(self, opts: LogOptions, now: datetime | None = None) -> None:
        if not opts.any():
            raise ValueError("nessun tipo di log selezionato")
        if opts.max_raw < 0:
            raise ValueError("max_raw non può essere negativo")
        self.opts = opts
        self._lock = threading.Lock()
        self._closed = False
        self.cycles = 0            # catture registrate
        self.misses = 0            # trigger mancati
        t = now or datetime.now()
        base = Path(opts.folder).expanduser()
        d = base / f"Log_{t:%Y%m%d_%H%M%S}"
        k = 1
        while d.exists():
            k += 1
            d = base / f"Log_{t:%Y%m%d_%H%M%S}_{k}"
        d.mkdir(parents=True)
        self.dir = d
        self._files: list = []
        self._raw: deque[Path] = deque()
        self._ev = self._dec = self._idx = None
        try:
            if opts.events:
                self._ev = self._open(d / "eventi.log", "utf-8")
            if opts.decoded:
                f = self._open(d / "decodifica.csv", "utf-8-sig", newline="")
                self._dec = csv.writer(f, delimiter=";")
                self._dec.writerow(DEC_HEADER)
                self._dec_file = f
            f = self._open(d / "cicli.csv", "utf-8-sig", newline="")
            self._idx = csv.writer(f, delimiter=";")
            self._idx.writerow(CYCLE_HEADER)
            self._idx_file = f
            if opts.raw:
                (d / "catture").mkdir()
        except OSError:
            self.close()
            raise

    def _open(self, path: Path, enc: str, **kw):
        f = open(path, "w", encoding=enc, **kw)
        self._files.append(f)
        return f

    # ---------------------------------------------------------------- eventi
    def event(self, level: str, text: str, when: datetime | None = None) -> None:
        with self._lock:
            self._event(level, text, when)

    def _event(self, level: str, text: str, when: datetime | None = None) -> None:
        if self._closed or self._ev is None:
            return
        self._ev.write(f"{_stamp(when or datetime.now())}  {level:<7} {text}\n")
        self._ev.flush()

    def note(self, cycle: int, text: str) -> None:
        """Avviso su un ciclo (per esempio attese anomale)."""
        with self._lock:
            self._event("AVVISO", f"Ciclo {cycle}: {text}")

    def miss(self, cycle: int, t0: datetime, message: str) -> None:
        """Ciclo senza cattura (trigger non avvenuto nella finestra)."""
        with self._lock:
            if self._closed:
                return
            self.misses += 1
            self._event("AVVISO", f"Ciclo {cycle}: {message}")

    # --------------------------------------------------------------- catture
    def log_capture(self, cycle: int, t0: datetime, cap: Capture, dcfg: DecoderConfig | None = None,
                    channels: tuple[int, ...] | list[int] = tuple(range(N_CHANNELS)),
                    glitch_s: float = 100e-9) -> None:
        """Analizza (fuori dal lock) e registra una cattura."""
        o = self.opts
        t_an = time.perf_counter()
        dec = run_decoders(cap, dcfg or DecoderConfig()) if (o.decoded or o.events) else None
        meas = run_measures(cap, tuple(channels), glitch_s) if o.events else None
        analysis_s = time.perf_counter() - t_an
        fs = cap.samplerate
        n_ann = n_err = 0
        if dec is not None:
            for lane in dec.lanes:
                n_ann += len(lane.annotations)
                n_err += sum(1 for a in lane.annotations if a.error)
        n_gl = len(meas.glitches) if meas is not None else 0
        trig = cap.trigger_time

        with self._lock:
            if self._closed:
                return
            fname = ""
            if o.raw:
                fname = f"catture/cattura_{cycle:06d}.npz"
                path = self.dir / fname
                save_npz(cap, str(path))
                self._raw.append(path)
                while o.max_raw and len(self._raw) > o.max_raw:
                    self._raw.popleft().unlink(missing_ok=True)
            if o.decoded and dec is not None:
                w = self._dec
                for lane in dec.lanes:
                    for a in lane.annotations:
                        t = a.start / fs
                        w.writerow((cycle, _stamp(t0 + timedelta(seconds=t)), f"{t:.9f}", lane.name, a.text,
                                    a.detail, 1 if a.error else 0))
                self._dec_file.flush()
            if dec is not None:
                for e in dec.errors:
                    self._event("AVVISO", f"Ciclo {cycle}: {e}")
            self._idx.writerow((cycle, _stamp(t0), len(cap), f"{fs:g}", "" if trig is None else f"{trig:.9f}",
                                n_ann, n_err, n_gl, fname))
            self._idx_file.flush()
            self.cycles += 1
            parts = [f"{len(cap)} campioni a {_hz(fs)}"]
            if trig is not None:
                parts.append(f"trigger a {trig * 1e3:.3f} ms")
            if dec is not None:
                parts.append(f"{n_ann} annotazioni ({n_err} con errore)")
            if meas is not None:
                parts.append(f"{n_gl} glitch")
            meta = cap.meta or {}
            if meta.get("seconds") is not None:
                parts.append(f"acquisizione {meta['seconds']:.2f} s")
            if str(meta.get("trigger", "")).startswith("sigrok"):
                parts.append("trigger sigrok")
            parts.append(f"analisi {analysis_s:.2f} s")
            self._event("INFO", f"Ciclo {cycle}: " + " · ".join(parts), t0)
            if str(meta.get("trigger", "")).startswith("software (ripiego"):
                self._event("AVVISO", f"Ciclo {cycle}: {(meta.get('notes') or ['trigger sigrok non riuscito'])[0]}", t0)
            if meta.get("attempts", 1) > 1:
                self._event("AVVISO", f"Ciclo {cycle}: sigrok-cli ha richiesto {meta['attempts']} tentativi "
                                      f"({'; '.join(meta.get('notes') or ['motivo sconosciuto'])})", t0)

    # ------------------------------------------------------------------ fine
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for f in self._files:
                try:
                    f.close()
                except OSError:
                    pass
            self._files.clear()
