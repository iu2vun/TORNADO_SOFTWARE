"""Modello condiviso: reti caricate, selezione tracce, marker."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, Signal

from ..core.network import Network
from .theme import DARK

TraceKey = tuple  # (uid, i, j)


@dataclass
class TraceRef:
    net: Network
    i: int
    j: int
    color: str
    label: str
    key: TraceKey
    width: float = 1.6

    @property
    def values(self) -> np.ndarray:
        return self.net.s[:, self.i, self.j]


@dataclass
class NetEntry:
    net: Network
    visible: bool = True
    cells: set = field(default_factory=set)
    colors: dict = field(default_factory=dict)


@dataclass
class Marker:
    id: int
    name: str
    freq: float
    key: TraceKey
    ref: Optional[int] = None     # id del marker di riferimento (modo delta)


class MarkerModel(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.markers: list[Marker] = []
        self.active: Optional[int] = None
        self._next = 1

    def get(self, mid: Optional[int]) -> Optional[Marker]:
        for m in self.markers:
            if m.id == mid:
                return m
        return None

    def active_marker(self) -> Optional[Marker]:
        return self.get(self.active)

    def add(self, freq: float, key: TraceKey, ref: Optional[int] = None) -> Marker:
        m = Marker(self._next, f"M{self._next}", float(freq), key, ref)
        self._next += 1
        self.markers.append(m)
        self.active = m.id
        self.changed.emit()
        return m

    def remove(self, mid: int) -> None:
        self.markers = [m for m in self.markers if m.id != mid]
        for m in self.markers:
            if m.ref == mid:
                m.ref = None
        if self.active == mid:
            self.active = self.markers[-1].id if self.markers else None
        self.changed.emit()

    def remove_for_net(self, uid: int) -> None:
        gone = [m.id for m in self.markers if m.key[0] == uid]
        for g in gone:
            self.markers = [m for m in self.markers if m.id != g]
        ids = {m.id for m in self.markers}
        for m in self.markers:
            if m.ref not in ids:
                m.ref = None
        if self.active not in ids:
            self.active = self.markers[-1].id if self.markers else None
        if gone:
            self.changed.emit()

    def clear(self) -> None:
        self.markers.clear()
        self.active = None
        self._next = 1
        self.changed.emit()

    def set_active(self, mid: Optional[int]) -> None:
        if self.active != mid:
            self.active = mid
            self.changed.emit()

    def move(self, mid: int, freq: float, key: Optional[TraceKey] = None) -> None:
        m = self.get(mid)
        if m is None:
            return
        m.freq = float(freq)
        if key is not None:
            m.key = key
        self.changed.emit()

    def set_ref(self, mid: int, ref: Optional[int]) -> None:
        m = self.get(mid)
        if m is not None and ref != mid:
            m.ref = ref
            self.changed.emit()


class Workspace(QObject):
    """Contenitore delle reti e della selezione delle tracce."""

    changed = Signal()            # reti/selezione/visibilità cambiate
    network_added = Signal(int)   # uid
    network_removed = Signal(int)
    current_changed = Signal(int)  # uid della rete corrente (selezionata nell'albero)

    def __init__(self):
        super().__init__()
        self.entries: list[NetEntry] = []
        self.markers = MarkerModel()
        self.current_uid: Optional[int] = None
        self._color_i = 0
        self.palette = list(DARK.traces)
        self.trace_width = 1.6

    # ------------------------------------------------------------ accesso
    def entry(self, uid: int) -> Optional[NetEntry]:
        for e in self.entries:
            if e.net.uid == uid:
                return e
        return None

    def network(self, uid: int) -> Optional[Network]:
        e = self.entry(uid)
        return e.net if e else None

    def current_entry(self) -> Optional[NetEntry]:
        return self.entry(self.current_uid) if self.current_uid is not None else None

    def networks(self) -> list[Network]:
        return [e.net for e in self.entries]

    # ------------------------------------------------------------ modifica
    def _next_color(self) -> str:
        c = self.palette[self._color_i % len(self.palette)]
        self._color_i += 1
        return c

    def _ensure_colors(self, e: NetEntry) -> None:
        for c in e.cells:
            if c not in e.colors:
                e.colors[c] = self._next_color()

    @staticmethod
    def default_cells(net: Network) -> set:
        n = net.nports
        if n == 1:
            return {(0, 0)}
        if n == 2:
            return {(0, 0), (1, 0), (0, 1), (1, 1)}
        return {(i, i) for i in range(n)}

    def add_network(self, net: Network, cells: Optional[set] = None, select: bool = True) -> NetEntry:
        e = NetEntry(net, True, set(cells) if cells is not None else self.default_cells(net))
        self._ensure_colors(e)
        self.entries.append(e)
        self.network_added.emit(net.uid)
        if select:
            self.set_current(net.uid)
        self.changed.emit()
        return e

    def remove_network(self, uid: int) -> None:
        self.entries = [e for e in self.entries if e.net.uid != uid]
        self.markers.remove_for_net(uid)
        if self.current_uid == uid:
            self.current_uid = self.entries[-1].net.uid if self.entries else None
            if self.current_uid is not None:
                self.current_changed.emit(self.current_uid)
        self.network_removed.emit(uid)
        self.changed.emit()

    def clear(self) -> None:
        for e in list(self.entries):
            self.remove_network(e.net.uid)
        self.markers.clear()

    def set_current(self, uid: int) -> None:
        if uid != self.current_uid:
            self.current_uid = uid
            self.current_changed.emit(uid)

    def set_visible(self, uid: int, vis: bool) -> None:
        e = self.entry(uid)
        if e and e.visible != vis:
            e.visible = vis
            self.changed.emit()

    def set_cells(self, uid: int, cells: set) -> None:
        e = self.entry(uid)
        if e is None:
            return
        e.cells = set(cells)
        self._ensure_colors(e)
        self.changed.emit()

    def toggle_cell(self, uid: int, i: int, j: int) -> None:
        e = self.entry(uid)
        if e is None:
            return
        if (i, j) in e.cells:
            e.cells.discard((i, j))
        else:
            e.cells.add((i, j))
            self._ensure_colors(e)
        self.changed.emit()

    def set_color(self, uid: int, i: int, j: int, color: str) -> None:
        e = self.entry(uid)
        if e:
            e.colors[(i, j)] = color
            self.changed.emit()

    def rename(self, uid: int, name: str) -> None:
        n = self.network(uid)
        if n:
            n.name = name
            self.changed.emit()

    def replace_network(self, uid: int, new: Network) -> None:
        """Sostituisce i dati mantenendo selezione e colori (uid cambia)."""
        e = self.entry(uid)
        if e is None:
            return
        old_uid = uid
        e.net = new
        e.cells = {(i, j) for (i, j) in e.cells if i < new.nports and j < new.nports}
        for m in self.markers.markers:
            if m.key[0] == old_uid:
                m.key = (new.uid, m.key[1], m.key[2])
        if self.current_uid == old_uid:
            self.current_uid = new.uid
        self.changed.emit()

    # -------------------------------------------------------------- tracce
    def traces(self) -> list[TraceRef]:
        out: list[TraceRef] = []
        many = sum(1 for e in self.entries if e.visible) > 1
        for e in self.entries:
            if not e.visible:
                continue
            for (i, j) in sorted(e.cells):
                lab = e.net.label(i, j)
                out.append(TraceRef(e.net, i, j, e.colors.get((i, j), "#ffffff"),
                                    f"{e.net.name}: {lab}" if many else lab,
                                    (e.net.uid, i, j), self.trace_width))
        return out

    def trace_by_key(self, key: TraceKey) -> Optional[TraceRef]:
        for t in self.traces():
            if t.key == key:
                return t
        return None

    def first_trace_key(self) -> Optional[TraceKey]:
        t = self.traces()
        return t[0].key if t else None
