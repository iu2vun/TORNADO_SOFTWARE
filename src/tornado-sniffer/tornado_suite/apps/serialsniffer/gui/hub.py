"""Hub dati: raccoglie i chunk da qualunque thread e li consegna alle viste, a lotti, nel thread GUI."""
from __future__ import annotations

import collections
from typing import Callable, Iterable, Optional, Sequence

from PySide6.QtCore import QObject, QTimer, Signal

from ..chunk import GLOBAL, Chunk

_CLEAR = object()


class DataHub(QObject):
    """push()/push_many()/request_clear() sono thread-safe (deque); tutto il resto va usato dal
    thread GUI. Le viste ricevono `batch(list[Chunk])` ogni ~33 ms, indipendentemente dalla frequenza
    dei chunk. Un clear è un segnale in coda: i chunk pushati dopo di esso non vengono mai persi."""

    batch = Signal(list)
    cleared = Signal()
    channels_changed = Signal()

    HISTORY = 200_000
    DRAIN_MAX = 20_000

    def __init__(self, parent: Optional[QObject] = None, interval_ms: int = 33) -> None:
        super().__init__(parent)
        self.history: collections.deque[Chunk] = collections.deque(maxlen=self.HISTORY)
        self.full_source: Optional[Callable[[], Sequence[Chunk]]] = None  # riproduzione: tutti i chunk della registrazione
        self.channels: dict[int, dict] = {}
        self.totals: dict[tuple[int, int], int] = {}
        self.chunk_count = 0
        self._incoming: collections.deque = collections.deque()
        self._now_fn: Optional[Callable[[], int]] = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.drain)
        self._timer.start()

    # ------------------------------------------------- thread-safe (da qualunque thread)
    def push(self, chunk: Chunk) -> None:
        self._incoming.append(chunk)

    def push_many(self, chunks: Iterable[Chunk]) -> None:
        self._incoming.extend(chunks)

    def request_clear(self) -> None:
        self._incoming.append(_CLEAR)

    # --------------------------------------------------------------- thread GUI
    def set_channels(self, infos: list[dict]) -> None:
        self.channels = {int(i["index"]): dict(i) for i in infos}
        self.channels_changed.emit()

    def name(self, idx: int) -> str:
        if idx == GLOBAL:
            return "*"
        info = self.channels.get(idx)
        return str(info.get("name")) if info and info.get("name") else f"CH{idx}"

    def set_now_fn(self, fn: Optional[Callable[[], int]]) -> None:
        self._now_fn = fn

    def now_ns(self) -> int:
        if self._now_fn is not None:
            try:
                return int(self._now_fn())
            except Exception:
                pass
        return self.history[-1].ts_ns if self.history else 0

    def drain(self) -> None:
        n = min(len(self._incoming), self.DRAIN_MAX)
        if n == 0:
            return
        items: list[Chunk] = []
        for _ in range(n):
            x = self._incoming.popleft()
            if x is _CLEAR:
                if items:
                    self._commit(items)
                    items = []
                self._clear_now()
            else:
                items.append(x)
        if items:
            self._commit(items)

    def _commit(self, items: list[Chunk]) -> None:
        self.history.extend(items)
        for c in items:
            if not c.is_event:
                key = (c.channel, c.direction)
                self.totals[key] = self.totals.get(key, 0) + len(c.data)
        self.chunk_count += len(items)
        self.batch.emit(items)

    def _clear_now(self) -> None:
        self.history.clear()
        self.totals.clear()
        self.chunk_count = 0
        self.cleared.emit()
