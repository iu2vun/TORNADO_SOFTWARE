"""Statistiche sui messaggi decodificati: per (canale, protocollo, chiave) conteggi, errori, intervalli, jitter,
latenze richiesta→risposta, richieste senza risposta."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

from .decoders.base import Message

# messaggi che chiudono una richiesta: non entrano nel calcolo degli intervalli fra eventi periodici
RESPONSE_KINDS = {"response", "final", "exception", "error", "echo", "info"}
REQUEST_KINDS = {"request", "command"}
SAMPLE_CAP = 20_000  # campioni di latenza conservati per il percentile
COLUMNS = (
    "channel", "protocol", "key", "count", "events", "errors", "bytes", "rate_hz", "interval_mean_ms", "interval_min_ms",
    "interval_max_ms", "interval_std_ms", "requests", "answered", "unanswered", "lat_n", "lat_mean_ms", "lat_min_ms",
    "lat_max_ms", "lat_p95_ms", "first_s", "last_s",
)


class Running:
    """Media/varianza incrementali (Welford) con minimo e massimo."""

    __slots__ = ("n", "mean", "m2", "min", "max")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min = math.inf
        self.max = -math.inf

    def add(self, x: float) -> None:
        self.n += 1
        d = x - self.mean
        self.mean += d / self.n
        self.m2 += d * (x - self.mean)
        self.min = min(self.min, x)
        self.max = max(self.max, x)

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0


@dataclass
class KeyStats:
    channel: int
    protocol: str
    key: str
    count: int = 0
    events: int = 0          # messaggi "primari" (non risposte): base di intervalli e frequenza
    errors: int = 0
    bytes: int = 0
    requests: int = 0
    answered: int = 0
    first_ns: Optional[int] = None
    last_ns: int = 0
    last_event_ns: Optional[int] = None
    interval: Running = field(default_factory=Running)
    latency: Running = field(default_factory=Running)
    samples: list = field(default_factory=list)


class StatsCollector:
    def __init__(self, channel_names: Optional[dict[int, str]] = None) -> None:
        self.names = channel_names or {}
        self._k: dict[tuple[int, str, str], KeyStats] = {}

    def reset(self) -> None:
        self._k.clear()

    def add(self, m: Message) -> None:
        ks = self._k.get((m.channel, m.protocol, m.key))
        if ks is None:
            ks = self._k[(m.channel, m.protocol, m.key)] = KeyStats(m.channel, m.protocol, m.key or "(senza chiave)")
        ks.count += 1
        ks.bytes += len(m.raw)
        ks.errors += (not m.ok) or m.kind == "exception"
        if ks.first_ns is None:
            ks.first_ns = m.ts_ns
        ks.last_ns = max(ks.last_ns, m.ts_ns)
        if m.kind in REQUEST_KINDS:
            ks.requests += 1
        if m.pair_ns is not None:
            ks.answered += 1
            lat = m.pair_ns / 1e6
            ks.latency.add(lat)
            if len(ks.samples) < SAMPLE_CAP:
                ks.samples.append(lat)
        if m.kind not in RESPONSE_KINDS:
            ks.events += 1
            if ks.last_event_ns is not None and m.ts_ns >= ks.last_event_ns:
                ks.interval.add((m.ts_ns - ks.last_event_ns) / 1e6)
            ks.last_event_ns = m.ts_ns

    def add_many(self, msgs: Iterable[Message]) -> None:
        for m in msgs:
            self.add(m)

    def rows(self) -> list[dict]:
        out = []
        for ks in self._k.values():
            span = (ks.last_event_ns or 0) - (ks.first_ns or 0)
            rate = (ks.events - 1) / (span / 1e9) if ks.events > 1 and span > 0 else None
            lat = ks.latency
            p95 = None
            if ks.samples:
                s = sorted(ks.samples)
                p95 = s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]
            iv = ks.interval
            out.append({
                "channel": self.names.get(ks.channel, str(ks.channel)), "protocol": ks.protocol, "key": ks.key,
                "count": ks.count, "events": ks.events, "errors": ks.errors, "bytes": ks.bytes, "rate_hz": rate,
                "interval_mean_ms": iv.mean if iv.n else None, "interval_min_ms": iv.min if iv.n else None,
                "interval_max_ms": iv.max if iv.n else None, "interval_std_ms": iv.std if iv.n > 1 else None,
                "requests": ks.requests, "answered": ks.answered,
                "unanswered": max(0, ks.requests - ks.answered) if ks.requests else 0,
                "lat_n": lat.n, "lat_mean_ms": lat.mean if lat.n else None, "lat_min_ms": lat.min if lat.n else None,
                "lat_max_ms": lat.max if lat.n else None, "lat_p95_ms": p95,
                "first_s": (ks.first_ns or 0) / 1e9, "last_s": ks.last_ns / 1e9,
            })
        out.sort(key=lambda r: (r["channel"], r["protocol"], -r["count"], r["key"]))
        return out


def _fmt(v, digits: int = 2) -> str:
    return "" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def format_table(rows: list[dict]) -> str:
    cols = [("Canale", "channel"), ("Protocollo", "protocol"), ("Chiave", "key"), ("N", "count"), ("Err", "errors"),
            ("Hz", "rate_hz"), ("Δt medio ms", "interval_mean_ms"), ("Δt σ ms", "interval_std_ms"),
            ("Lat media ms", "lat_mean_ms"), ("Lat p95 ms", "lat_p95_ms"), ("Lat max ms", "lat_max_ms"), ("Senza risp.", "unanswered")]
    table = [[h for h, _ in cols]] + [[_fmt(r[k]) for _, k in cols] for r in rows]
    widths = [max(len(row[i]) for row in table) for i in range(len(cols))]
    lines = ["  ".join(c.ljust(w) if i < 3 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths))) for row in table]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _csv_safe(text: str) -> str:
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def write_csv(rows: list[dict], path: Union[str, Path]) -> Path:
    path = Path(path)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([_csv_safe(v) if isinstance(v, str) else ("" if v is None else v) for v in (r[c] for c in COLUMNS)])
    return path


def write_json(rows: list[dict], path: Union[str, Path]) -> Path:
    path = Path(path)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
