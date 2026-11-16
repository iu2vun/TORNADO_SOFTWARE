"""Lettore PcbDoc (OLE2). Formato ricavato per ispezione dei file reali; nessun codice di terzi."""
from __future__ import annotations
import math
import struct
import olefile
from .model import *

U = 1.0 / 10000.0   # unità interne Altium -> mil


def layer_name(n: int) -> str:
    if n == 1: return "Top"
    if n == 32: return "Bottom"
    if 2 <= n <= 31: return f"Mid{n - 1}"
    names = {33: "TopOverlay", 34: "BottomOverlay", 35: "TopPaste", 36: "BottomPaste",
             37: "TopSolder", 38: "BottomSolder", 55: "DrillGuide", 56: "KeepOut",
             73: "DrillDrawing", 74: "Multi"}
    if 39 <= n <= 54: return f"Plane{n - 38}"
    if 57 <= n <= 72: return f"Mech{n - 56}"
    return names.get(n, f"L{n}")


def _props(raw: bytes) -> dict:
    d = {}
    s = raw.rstrip(b"\x00").decode("latin-1")
    for part in s.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip().upper()] = v.strip()
    return d


def _mil(v: str, default=0.0) -> float:
    try:
        v = v.strip().lower()
        if v.endswith("mil"):
            return float(v[:-3])
        if v.endswith("mm"):
            return float(v[:-2]) / 0.0254
        return float(v)
    except Exception:
        return default


class _R:
    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def left(self): return len(self.b) - self.p

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def u16(self):
        v = struct.unpack_from("<H", self.b, self.p)[0]; self.p += 2; return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.p)[0]; self.p += 4; return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v

    def f64(self):
        v = struct.unpack_from("<d", self.b, self.p)[0]; self.p += 8; return v

    def skip(self, n): self.p += n

    def sub(self):
        n = self.u32()
        s = self.b[self.p:self.p + n]
        self.p += n
        return _R(s)

    def coord(self): return self.i32() * U


def _stream(ole, name):
    path = name + "/Data"
    if not ole.exists(path):
        return None
    return ole.openstream(path).read()


def _records(data: bytes, rtype: int):
    r = _R(data)
    while r.left() > 0:
        t = r.u8()
        if t != rtype:
            raise ValueError(f"record type {t} != {rtype}")
        yield r


def _each(data, rtype, fn, warns, label):
    if not data:
        return
    r = _R(data)
    n = 0
    while r.left() > 0:
        try:
            t = r.u8()
            if t != rtype:
                raise ValueError(f"tipo {t} inatteso")
            fn(r)
            n += 1
        except Exception as e:
            warns.append(f"{label}: interrotto dopo {n} record ({e})")
            break


def load_pcbdoc(path: str) -> PcbDoc:
    ole = olefile.OleFileIO(path)
    doc = PcbDoc()
    w = doc.warnings

    def tracks(r):
        s = r.sub()
        layer = s.u8(); s.skip(2); net = s.u16(); s.skip(2 + 2 + 4)
        x1, y1, x2, y2, wd = s.coord(), s.coord(), s.coord(), s.coord(), s.coord()
        doc.tracks.append(Track(layer, x1, y1, x2, y2, wd, net if net != 0xFFFF else -1))
    _each(_stream(ole, "Tracks6"), 4, tracks, w, "Tracks6")

    def arcs(r):
        s = r.sub()
        layer = s.u8(); s.skip(2); net = s.u16(); s.skip(2 + 2 + 4)
        cx, cy, rad = s.coord(), s.coord(), s.coord()
        a0, a1 = s.f64(), s.f64()
        wd = s.coord()
        doc.arcs.append(Arc(layer, cx, cy, rad, a0, a1, wd, net if net != 0xFFFF else -1))
    _each(_stream(ole, "Arcs6"), 1, arcs, w, "Arcs6")

    def vias(r):
        s = r.sub()
        s.skip(1 + 1 + 1); net = s.u16(); s.skip(2 + 2 + 4)
        x, y, d, h = s.coord(), s.coord(), s.coord(), s.coord()
        l0, l1 = s.u8(), s.u8()
        doc.vias.append(Via(x, y, d, h, l0, l1, net if net != 0xFFFF else -1))
    _each(_stream(ole, "Vias6"), 3, vias, w, "Vias6")

    def pads(r):
        s1 = r.sub(); nlen = s1.u8()
        name = s1.b[1:1 + nlen].decode("latin-1")
        r.sub(); r.sub(); r.sub()
        s = r.sub()
        layer = s.u8(); s.skip(1)
        fl2 = s.u8(); net = s.u16(); s.skip(2); comp = s.u16(); s.skip(4)
        x, y = s.coord(), s.coord()
        tx, ty = s.coord(), s.coord()
        s.skip(16)  # mid/bottom sizes
        hole = s.coord()
        shape = s.u8(); s.skip(2)
        rot = s.f64()
        plated = s.u8() != 0
        r.sub()  # subrecord 6 (forme estese), non necessaria per la vista
        doc.pads.append(Pad(name, x, y, layer, tx, ty, shape, hole, rot, plated,
                            net if net != 0xFFFF else -1, comp if comp != 0xFFFF else -1))
    _each(_stream(ole, "Pads6"), 2, pads, w, "Pads6")

    def fills(r):
        s = r.sub()
        layer = s.u8(); s.skip(2); net = s.u16(); s.skip(2 + 2 + 4)
        x1, y1, x2, y2 = s.coord(), s.coord(), s.coord(), s.coord()
        rot = s.f64()
        doc.fills.append(Fill(layer, x1, y1, x2, y2, rot, net if net != 0xFFFF else -1))
    _each(_stream(ole, "Fills6"), 6, fills, w, "Fills6")

    def regions(r):
        s = r.sub()
        layer = s.u8(); s.skip(2); net = s.u16(); s.skip(2 + 2 + 5)
        holes = s.u16(); s.skip(2)
        n = s.u32()
        props = _props(s.b[s.p:s.p + n]); s.skip(n)
        cnt = s.u32()
        pts = [(s.f64() * U, s.f64() * U) for _ in range(cnt)]
        kind = int(props.get("KIND", "0") or 0)
        if props.get("ISBOARDCUTOUT", "").upper() == "TRUE":
            kind = 100
        doc.regions.append(Region(layer, pts, holes, kind, net if net != 0xFFFF else -1))
    _each(_stream(ole, "Regions6"), 11, regions, w, "Regions6")

    # componenti
    data = _stream(ole, "Components6")
    if data:
        r = _R(data)
        try:
            while r.left() > 4:
                n = r.u32() & 0xFFFFFF
                p = _props(r.b[r.p:r.p + n]); r.skip(n)
                doc.components.append(Component(
                    p.get("SOURCEDESIGNATOR", ""), _mil(p.get("X", "0")), _mil(p.get("Y", "0")),
                    float(p.get("ROTATION", "0") or 0), p.get("LAYER", ""), p.get("PATTERN", "")))
        except Exception as e:
            w.append(f"Components6: {e}")

    # reti
    data = _stream(ole, "Nets6")
    if data:
        r = _R(data)
        try:
            while r.left() > 4:
                n = r.u32() & 0xFFFFFF
                p = _props(r.b[r.p:r.p + n]); r.skip(n)
                doc.nets.append(p.get("NAME", ""))
        except Exception as e:
            w.append(f"Nets6: {e}")

    # contorno scheda
    data = _stream(ole, "Board6")
    if data:
        try:
            n = struct.unpack_from("<I", data, 0)[0] & 0xFFFFFF
            p = _props(data[4:4 + n])
            i = 0
            while f"VX{i}" in p:
                doc.outline.append((_mil(p[f"VX{i}"]), _mil(p[f"VY{i}"])))
                i += 1
        except Exception as e:
            w.append(f"Board6: {e}")
    ole.close()
    return doc
