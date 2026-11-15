"""Riproduzione di una registrazione con i tempi originali: velocità, pausa, seek, loop."""
from __future__ import annotations

import bisect
import threading
import time
from dataclasses import replace
from typing import Callable, Iterable, Mapping, Optional

from .chunk import KIND_DATA, Chunk
from .ssnfile import Recording, select_chunks

STOPPED = "stopped"
PLAYING = "playing"
PAUSED = "paused"
FINISHED = "finished"

SPIN_NS = 2_000_000  # sotto i 2 ms si attende in busy-wait: la sleep di Windows ha risoluzione ~15 ms


class Player:
    """on_chunk/on_state/sinks vengono chiamati dal thread di riproduzione.

    sinks: {indice canale: callable(bytes)} - tipicamente `serial.write` per rilanciare
    i dati su una porta reale. on_chunk riceve tutti i chunk (anche eventi) per la GUI.
    max_gap_s: comprime le pause più lunghe (la timeline riflette la compressione).
    rebase_ts: i chunk consegnati portano il tempo della timeline del player (compressione
    inclusa) invece di quello originale: serve a una GUI che mostra position_ns sullo stesso asse.
    on_seek(pos_ns) viene chiamata dopo ogni seek, con le consegne sospese: dopo di essa non
    arriva alcun chunk del vecchio punto. on_loop() a ogni ripartenza del loop (deve essere rapida).
    """

    def __init__(
        self,
        recording: Recording,
        *,
        sinks: Optional[Mapping[int, Callable[[bytes], None]]] = None,
        on_chunk: Optional[Callable[[Chunk], None]] = None,
        on_state: Optional[Callable[[str], None]] = None,
        speed: float = 1.0,
        loop: bool = False,
        max_gap_s: Optional[float] = None,
        channels: Optional[Iterable[int]] = None,
        directions: Optional[Iterable[int]] = None,
        include_events: bool = True,
        rebase_ts: bool = False,
        on_seek: Optional[Callable[[int], None]] = None,
        on_loop: Optional[Callable[[], None]] = None,
    ) -> None:
        self._items = list(select_chunks(recording, channels, directions, include_events))
        self._times = self._build_times(self._items, max_gap_s)
        if rebase_ts:
            self._items = [replace(c, ts_ns=t) for c, t in zip(self._items, self._times)]
        self._duration = self._times[-1] if self._times else 0
        self._sinks = dict(sinks or {})
        self._on_chunk = on_chunk
        self._on_state = on_state
        self._loop = loop
        self._speed = self._clamp_speed(speed)
        self.last_error: Optional[BaseException] = None
        self.loops_done = 0
        self._on_seek = on_seek
        self._on_loop = on_loop
        self._gen = 0  # cambia a ogni seek/stop/loop: scarta i chunk già estratti ma non ancora consegnati
        self._dlock = threading.RLock()  # serializza le consegne; ordine dei lock: _dlock -> _cv

        self._cv = threading.Condition(threading.RLock())
        self._state = STOPPED
        self._pos_ns = 0
        self._idx = 0
        self._anchor_wall = 0
        self._anchor_rec = 0
        self._quit = False
        self._thread = threading.Thread(target=self._run, name="player", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ proprietà
    @property
    def state(self) -> str:
        return self._state

    @property
    def items(self) -> list[Chunk]:
        """Chunk selezionati (con il tempo della timeline se rebase_ts)."""
        return self._items

    @property
    def duration_ns(self) -> int:
        return self._duration

    @property
    def position_ns(self) -> int:
        with self._cv:
            return self._position_locked()

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def loop(self) -> bool:
        return self._loop

    @loop.setter
    def loop(self, value: bool) -> None:
        with self._cv:
            self._loop = bool(value)
            self._cv.notify_all()

    # ------------------------------------------------------------- controllo
    def play(self) -> None:
        """Avvia (da inizio se finita), oppure riprende dalla posizione corrente."""
        with self._cv:
            if self._state == PLAYING:
                return
            if self._state == FINISHED:
                self._pos_ns = 0
            self._idx = bisect.bisect_left(self._times, self._pos_ns)
            self._anchor_locked(self._pos_ns)
            self._set_state_locked(PLAYING)

    def pause(self) -> None:
        with self._cv:
            if self._state == PLAYING:
                self._pos_ns = self._position_locked()
                self._set_state_locked(PAUSED)

    def stop(self) -> None:
        with self._cv:
            self._gen += 1
            self._pos_ns = 0
            self._idx = 0
            if self._state != STOPPED:
                self._set_state_locked(STOPPED)

    def seek(self, ts_ns: int) -> None:
        with self._dlock:  # attende l'eventuale consegna in corso
            with self._cv:
                self._gen += 1
                self._pos_ns = max(0, min(int(ts_ns), self._duration))
                self._idx = bisect.bisect_left(self._times, self._pos_ns)
                if self._state == PLAYING:
                    self._anchor_locked(self._pos_ns)
                elif self._state == FINISHED:
                    self._set_state_locked(PAUSED)
                self._cv.notify_all()
                pos = self._pos_ns
            if self._on_seek is not None:
                try:
                    self._on_seek(pos)
                except Exception as exc:
                    self.last_error = exc

    def chunks_before(self, pos_ns: int, limit: Optional[int] = None) -> list[Chunk]:
        """Chunk con tempo < pos_ns (quelli che un seek a pos_ns lascia alle spalle)."""
        i = bisect.bisect_left(self._times, pos_ns)
        start = 0 if limit is None else max(0, i - limit)
        return self._items[start:i]

    def set_speed(self, speed: float) -> None:
        with self._cv:
            if self._state == PLAYING:
                self._pos_ns = self._position_locked()
                self._speed = self._clamp_speed(speed)
                self._anchor_locked(self._pos_ns)
            else:
                self._speed = self._clamp_speed(speed)
            self._cv.notify_all()

    def wait_finished(self, timeout: Optional[float] = None) -> bool:
        """Blocca finché lo stato è FINISHED/STOPPED. Chiamare dopo play()."""
        with self._cv:
            return self._cv.wait_for(lambda: self._state in (FINISHED, STOPPED), timeout)

    def close(self) -> None:
        with self._cv:
            self._quit = True
            self._cv.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    # -------------------------------------------------------------- interno
    @staticmethod
    def _clamp_speed(speed: float) -> float:
        return max(0.01, min(float(speed), 10000.0))

    @staticmethod
    def _build_times(items: list[Chunk], max_gap_s: Optional[float]) -> list[int]:
        cap = None if max_gap_s is None else int(max_gap_s * 1e9)
        times: list[int] = []
        acc = prev = 0
        for c in items:
            d = max(0, c.ts_ns - prev)
            if cap is not None and d > cap:
                d = cap
            acc += d
            prev = c.ts_ns
            times.append(acc)
        return times

    def _position_locked(self) -> int:
        if self._state != PLAYING:
            return self._pos_ns
        elapsed = time.perf_counter_ns() - self._anchor_wall
        return min(self._duration, self._anchor_rec + int(elapsed * self._speed))

    def _anchor_locked(self, rec_ns: int) -> None:
        self._anchor_wall = time.perf_counter_ns()
        self._anchor_rec = rec_ns

    def _set_state_locked(self, state: str) -> None:
        self._state = state
        self._cv.notify_all()
        if self._on_state is not None:
            try:
                self._on_state(state)
            except Exception as exc:
                self.last_error = exc

    def _run(self) -> None:
        while True:
            with self._cv:
                if self._quit:
                    return
                if self._state != PLAYING:
                    self._cv.wait()
                    continue
                if self._idx >= len(self._items):
                    if self._loop and self._items:
                        self.loops_done += 1
                        self._gen += 1
                        self._pos_ns = 0
                        self._idx = 0
                        self._anchor_locked(0)
                        if self._on_loop is not None:
                            try:
                                self._on_loop()
                            except Exception as exc:
                                self.last_error = exc
                        continue
                    self._pos_ns = self._duration
                    self._set_state_locked(FINISHED)
                    continue
                due = self._anchor_wall + int((self._times[self._idx] - self._anchor_rec) / self._speed)
                delay = due - time.perf_counter_ns()
                if delay > SPIN_NS:
                    self._cv.wait((delay - SPIN_NS // 2) / 1e9)
                    continue
                while time.perf_counter_ns() < due:
                    pass
                chunk = self._items[self._idx]
                gen = self._gen
                self._idx += 1
            self._deliver(chunk, gen)

    def _deliver(self, chunk: Chunk, gen: int) -> None:
        with self._dlock:
            if gen != self._gen:
                return  # un seek/stop/loop è avvenuto dopo l'estrazione: chunk obsoleto
            if chunk.kind == KIND_DATA:
                sink = self._sinks.get(chunk.channel)
                if sink is not None:
                    try:
                        sink(chunk.data)
                    except Exception as exc:
                        self.last_error = exc
            if self._on_chunk is not None:
                try:
                    self._on_chunk(chunk)
                except Exception as exc:
                    self.last_error = exc
