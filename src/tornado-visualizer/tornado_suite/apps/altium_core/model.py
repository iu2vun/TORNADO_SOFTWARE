"""Modello comune (unità: mil, asse Y verso l'alto come in Altium)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Track:
    layer: int; x1: float; y1: float; x2: float; y2: float; width: float; net: int = -1


@dataclass
class Arc:
    layer: int; cx: float; cy: float; r: float; a0: float; a1: float; width: float; net: int = -1


@dataclass
class Via:
    x: float; y: float; dia: float; hole: float; l0: int; l1: int; net: int = -1


@dataclass
class Pad:
    name: str; x: float; y: float; layer: int
    sx: float; sy: float; shape: int; hole: float; rot: float
    plated: bool = True; net: int = -1; comp: int = -1


@dataclass
class Fill:
    layer: int; x1: float; y1: float; x2: float; y2: float; rot: float; net: int = -1


@dataclass
class Region:
    layer: int; pts: list; holes: int = 0; kind: int = 0; net: int = -1


@dataclass
class Component:
    designator: str; x: float; y: float; rot: float; layer: str; pattern: str = ""


@dataclass
class PcbDoc:
    tracks: list = field(default_factory=list)
    arcs: list = field(default_factory=list)
    vias: list = field(default_factory=list)
    pads: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    regions: list = field(default_factory=list)
    components: list = field(default_factory=list)
    nets: list = field(default_factory=list)
    outline: list = field(default_factory=list)   # [(x,y)] o archi approssimati
    warnings: list = field(default_factory=list)

    def bbox(self):
        xs, ys = [], []
        pts = self.outline or []
        for x, y in pts:
            xs.append(x); ys.append(y)
        if not xs:
            for t in self.tracks:
                xs += [t.x1, t.x2]; ys += [t.y1, t.y2]
            for p in self.pads:
                xs.append(p.x); ys.append(p.y)
        if not xs:
            return (0, 0, 1000, 1000)
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class SchPrim:
    kind: str            # line, rect, poly, label, pin, wire, junction, power, port, arc, ellipse, text
    pts: list = field(default_factory=list)
    text: str = ""
    color: int = 0
    width: float = 1
    extra: dict = field(default_factory=dict)
    owner: int = -1


@dataclass
class SchDoc:
    prims: list = field(default_factory=list)
    sheet: tuple = (1150, 760)
    warnings: list = field(default_factory=list)
    components: dict = field(default_factory=dict)  # index -> dict(props)
