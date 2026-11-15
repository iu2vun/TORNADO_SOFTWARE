"""Registratore asincrono: la scrittura su disco non rallenta mai i thread di lettura."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Optional, Union

from .chunk import Chunk
from .ssnfile import SsnWriter


class Recorder:
    """Coda illimitata + thread scrittore. Se il disco fallisce l'errore finisce in `error`
    e i chunk successivi vengono scartati (la coda non cresce)."""

    def __init__(
        self,
        path: Union[str, Path],
        header: dict,
        offset_ns: int = 0,
        flush_s: float = 0.5,
    ) -> None:
        self.path = Path(path)
        self.records = 0
        self.bytes = 0
        self.error: Optional[str] = None
        self._offset = offset_ns
        self._flush_s = flush_s
        self._writer = SsnWriter(self.path, header)  # solleva subito se il percorso non è scrivibile
        self._q: "queue.Queue[Optional[Chunk]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)
        self._thread.start()

    def write(self, chunk: Chunk) -> None:
        if self.error is None:
            self._q.put(chunk)

    def stop(self) -> None:
        self._q.put(None)
        self._thread.join()
        try:
            self._writer.close()
        except OSError as exc:
            self.error = self.error or str(exc)

    def _run(self) -> None:
        last_flush = time.monotonic()
        while True:
            try:
                item = self._q.get(timeout=self._flush_s)
            except queue.Empty:
                self._safe_flush()
                last_flush = time.monotonic()
                continue
            if item is None:
                break
            if self.error is not None:
                continue
            try:
                self._writer.write(item, max(0, item.ts_ns - self._offset))
            except OSError as exc:
                self.error = str(exc)
                continue
            self.records += 1
            self.bytes += len(item.data)
            if time.monotonic() - last_flush >= self._flush_s:
                self._safe_flush()
                last_flush = time.monotonic()
        self._safe_flush()

    def _safe_flush(self) -> None:
        try:
            self._writer.flush()
        except OSError as exc:
            self.error = self.error or str(exc)
