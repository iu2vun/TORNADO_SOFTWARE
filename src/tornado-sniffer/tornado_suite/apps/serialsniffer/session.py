"""Sessione di acquisizione: N canali, bridge, ring buffer, listener, registrazione."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, Optional, Union

from .channel import Channel
from .chunk import GLOBAL, KIND_EVENT, MAX_CHANNELS, RX, Chunk
from .config import PortConfig
from .recorder import Recorder
from .ssnfile import make_header

Listener = Callable[[Chunk], None]
ChannelRef = Union[int, str, Channel]


class Session:
    """I listener vengono chiamati dai thread di lettura: una GUI Qt deve inoltrare i chunk
    con un Signal (QueuedConnection) e non toccare i widget direttamente."""

    def __init__(self, ring_bytes: int = 4 << 20) -> None:
        self.channels: list[Channel] = []
        self.running = False
        self.t0_wall_ns = time.time_ns()
        self.listener_errors = 0
        self.last_listener_error: Optional[BaseException] = None
        self._t0_ns = time.perf_counter_ns()
        self._ring_cap = ring_bytes
        self._ring: dict[int, deque[Chunk]] = defaultdict(deque)
        self._ring_size: dict[int, int] = defaultdict(int)
        self._listeners: list[Listener] = []
        self._bridges: dict[int, set[int]] = defaultdict(set)
        self._lock = threading.RLock()
        self._recorder: Optional[Recorder] = None

    # --------------------------------------------------------------- tempo
    def clock(self) -> int:
        """ns dall'origine dei tempi della sessione."""
        return time.perf_counter_ns() - self._t0_ns

    # -------------------------------------------------------------- canali
    def add_channel(
        self,
        name: str,
        url: str,
        config: Optional[PortConfig] = None,
        *,
        reconnect: bool = True,
        monitor_lines: bool = False,
        idle_gap_s: Optional[float] = None,
        max_frame: int = 8192,
        max_hold_s: float = 0.5,
    ) -> Channel:
        """idle_gap_s: None = auto, 0 = nessun raggruppamento, >0 = secondi (vedi Channel)."""
        if len(self.channels) >= MAX_CHANNELS:
            raise ValueError("troppi canali")
        if any(c.name == name for c in self.channels):
            raise ValueError(f"nome canale duplicato: {name!r}")
        ch = Channel(
            len(self.channels), name, url, config or PortConfig(), self._on_chunk, self.clock,
            reconnect=reconnect, monitor_lines=monitor_lines, idle_gap_s=idle_gap_s,
            max_frame=max_frame, max_hold_s=max_hold_s, raw=self._on_raw,
        )
        self.channels.append(ch)
        if self.running:
            ch.start()
        return ch

    def channel(self, ref: ChannelRef) -> Channel:
        if isinstance(ref, Channel):
            return ref
        if isinstance(ref, int):
            if 0 <= ref < len(self.channels):
                return self.channels[ref]
        else:
            for c in self.channels:
                if c.name == ref:
                    return c
            if ref.isdigit() and int(ref) < len(self.channels):
                return self.channels[int(ref)]
        raise ValueError(f"canale sconosciuto: {ref!r}")

    def bridge(self, a: ChannelRef, b: ChannelRef, bidirectional: bool = True) -> None:
        """Inoltra i byte letti da `a` verso `b` (e viceversa): il PC sta in mezzo alla linea.
        I byte inoltrati non vengono registrati come TX: compaiono solo come RX del lato che li riceve."""
        ca, cb = self.channel(a), self.channel(b)
        if ca is cb:
            raise ValueError("bridge fra lo stesso canale")
        self._bridges[ca.index].add(cb.index)
        if bidirectional:
            self._bridges[cb.index].add(ca.index)

    def clear_bridges(self) -> None:
        self._bridges.clear()

    def add_listener(self, cb: Listener) -> None:
        with self._lock:
            self._listeners.append(cb)

    def remove_listener(self, cb: Listener) -> None:
        with self._lock:
            if cb in self._listeners:
                self._listeners.remove(cb)

    # ------------------------------------------------------------- run/stop
    def start(self) -> None:
        if self.running:
            return
        started: list[Channel] = []
        try:
            for ch in self.channels:
                ch.start()
                started.append(ch)
        except Exception:
            for ch in started:
                ch.stop()
            raise
        self.running = True

    def stop(self) -> None:
        for ch in self.channels:
            ch.stop()
        self.running = False
        self.stop_recording()

    def __enter__(self) -> "Session":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ------------------------------------------------------------ azioni TX
    def send(self, ref: ChannelRef, data: bytes) -> None:
        self.channel(ref).write(data)

    def mark(self, text: str) -> None:
        """Marker testuale nella timeline (e nella registrazione, se attiva)."""
        self._on_chunk(Chunk(self.clock(), GLOBAL, RX, text.encode("utf-8"), KIND_EVENT))

    # ---------------------------------------------------------- registrazione
    @property
    def recording(self) -> bool:
        return self._recorder is not None

    def start_recording(self, path: Union[str, Path], notes: str = "") -> Path:
        with self._lock:
            if self._recorder is not None:
                raise RuntimeError("registrazione già attiva")
            offset = self.clock()
            header = make_header(
                [
                    {"index": c.index, "name": c.name, "url": c.url, "config": str(c.config)}
                    for c in self.channels
                ],
                t0_wall_ns=self.t0_wall_ns + offset,
                notes=notes,
            )
            self._recorder = Recorder(path, header, offset_ns=offset)
        self.mark("REC START")
        return Path(path)

    def stop_recording(self) -> Optional[dict]:
        with self._lock:
            rec = self._recorder
        if rec is None:
            return None
        self.mark("REC STOP")
        with self._lock:
            self._recorder = None
        rec.stop()
        return {"path": rec.path, "records": rec.records, "bytes": rec.bytes, "error": rec.error}

    @property
    def recording_error(self) -> Optional[str]:
        rec = self._recorder
        return rec.error if rec else None

    # ----------------------------------------------------------- dati/stats
    def history(self, channel: Optional[ChannelRef] = None) -> list[Chunk]:
        """Copia del ring buffer (ultimi `ring_bytes` per canale), ordinata nel tempo."""
        with self._lock:
            if channel is None:
                items = [c for q in self._ring.values() for c in q]
            else:
                items = list(self._ring[self.channel(channel).index])
        items.sort(key=lambda c: c.ts_ns)
        return items

    def stats(self) -> list[dict]:
        return [
            {
                "index": c.index, "name": c.name, "url": c.url, "config": str(c.config),
                "open": c.is_open, "rx_bytes": c.rx_bytes, "tx_bytes": c.tx_bytes,
                "rx_chunks": c.rx_chunks,
            }
            for c in self.channels
        ]

    # ------------------------------------------------------------- interno
    def _on_raw(self, index: int, data: bytes) -> None:
        """Ogni lettura grezza, senza raggruppamento: il bridge resta trasparente."""
        for dst in self._bridges.get(index, ()):
            self.channels[dst].write(data, record=False)

    def _on_chunk(self, chunk: Chunk) -> None:
        with self._lock:
            q = self._ring[chunk.channel]
            q.append(chunk)
            self._ring_size[chunk.channel] += len(chunk.data)
            while self._ring_size[chunk.channel] > self._ring_cap and len(q) > 1:
                self._ring_size[chunk.channel] -= len(q.popleft().data)
            rec = self._recorder
            listeners = list(self._listeners)
        if rec is not None:
            rec.write(chunk)
        for cb in listeners:
            try:
                cb(chunk)
            except Exception as exc:  # un listener difettoso non deve fermare l'acquisizione
                self.listener_errors += 1
                self.last_listener_error = exc
