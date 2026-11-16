"""Lettore SchDoc: file OLE2 (stream FileHeader) oppure ASCII 'Protel for Windows'.
Coordinate in mil (1 unità Altium = 10 mil), Y verso l'alto."""
from __future__ import annotations
import struct
from .model import SchDoc, SchPrim

SHEETS = {0: (1150, 760), 1: (1550, 1110), 2: (2230, 1570), 3: (3150, 2230), 4: (4460, 3150),
          5: (950, 750), 6: (1500, 950), 7: (2000, 1500), 8: (3200, 2000), 9: (4200, 3200),
          10: (1100, 850), 11: (1400, 850), 12: (1700, 1100)}


def _parse_props(raw: bytes) -> dict:
    d = {}
    s = raw.rstrip(b"\x00")
    utf8 = b"%UTF8%" in s
    txt = s.decode("utf-8", "replace") if utf8 else s.decode("latin-1")
    for part in txt.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().upper()
            if k.startswith("%UTF8%"):
                k = k[6:]
            d[k] = v
    return d


def _records_ole(path: str):
    import olefile
    ole = olefile.OleFileIO(path)
    data = ole.openstream("FileHeader").read()
    ole.close()
    i, out = 0, []
    while i + 4 <= len(data):
        n = struct.unpack_from("<I", data, i)[0]
        flag, n = n >> 24, n & 0xFFFFFF
        i += 4
        raw = data[i:i + n]; i += n
        if flag == 0:
            out.append(_parse_props(raw))
        # flag 1 = binario (pin compressi in alcuni formati): ignorato
    return out


def _records_ascii(path: str):
    out = []
    with open(path, "rb") as f:
        for line in f.read().split(b"\n"):
            line = line.strip()
            if line.startswith(b"|") and b"RECORD=" in line:
                out.append(_parse_props(line))
            elif line.startswith(b"|HEADER") or not line:
                continue
    return out


def _f(d, k, default=0.0):
    try:
        return float(d.get(k, default) or default)
    except ValueError:
        return float(default)


def _loc(d, kx="LOCATION.X", ky="LOCATION.Y"):
    x = (_f(d, kx) + _f(d, kx + "_FRAC") / 100000.0) * 10.0
    y = (_f(d, ky) + _f(d, ky + "_FRAC") / 100000.0) * 10.0
    return x, y


def load_schdoc(path: str) -> SchDoc:
    with open(path, "rb") as f:
        head = f.read(8)
    recs = _records_ole(path) if head[:4] == b"\xd0\xcf\x11\xe0" else _records_ascii(path)
    doc = SchDoc()
    if recs and recs[0].get("HEADER"):
        recs = recs[1:]
    comps = {}
    for i, d in enumerate(recs):
        if d.get("RECORD") == "1":
            comps[i] = d
    doc.components = {i: dict(d) for i, d in comps.items()}

    def visible(d):
        o = int(_f(d, "OWNERINDEX", -1))
        if o in comps:
            part = int(_f(d, "OWNERPARTID", -1))
            cur = int(_f(comps[o], "CURRENTPARTID", 1))
            if part not in (-1, cur) and part != 0:
                return False
            if int(_f(d, "OWNERPARTDISPLAYMODE", 0)) != int(_f(comps[o], "DISPLAYMODE", 0)):
                return False
        return True

    for i, d in enumerate(recs):
        r = d.get("RECORD")
        owner = int(_f(d, "OWNERINDEX", -1))
        col = int(_f(d, "COLOR", 0))
        P = doc.prims
        try:
            if r == "31":
                if d.get("USECUSTOMSHEET", "F") == "T":
                    doc.sheet = (_f(d, "CUSTOMX", 1150) * 10, _f(d, "CUSTOMY", 760) * 10)
                else:
                    sx, sy = SHEETS.get(int(_f(d, "SHEETSTYLE", 0)), (1150, 760))
                    doc.sheet = (sx * 10, sy * 10)
                continue
            if not visible(d):
                continue
            if r in ("13", "6", "27", "7", "5"):          # linea, polilinea, filo, poligono
                if r == "13":
                    pts = [_loc(d), _loc(d, "CORNER.X", "CORNER.Y")]
                else:
                    n = int(_f(d, "LOCATIONCOUNT", 0))
                    pts = [_loc(d, f"X{k}", f"Y{k}") for k in range(1, n + 1)]
                    pts = [(_f(d, f"X{k}") * 10, _f(d, f"Y{k}") * 10) for k in range(1, n + 1)]
                kind = {"13": "line", "6": "line", "27": "wire", "7": "poly", "5": "line"}[r]
                P.append(SchPrim(kind, pts, color=col, width=max(1.0, _f(d, "LINEWIDTH", 1)),
                                 extra={"filled": d.get("ISSOLID") == "T", "fill": int(_f(d, "AREACOLOR", 0))},
                                 owner=owner))
            elif r in ("14", "10", "15"):                   # rettangoli (14), rett. arrotondato, sheet symbol
                P.append(SchPrim("rect", [_loc(d), _loc(d, "CORNER.X", "CORNER.Y")], color=col,
                                 extra={"filled": d.get("ISSOLID", "T" if r == "15" else "F") == "T",
                                        "fill": int(_f(d, "AREACOLOR", 0))}, owner=owner))
            elif r == "2":                                  # pin
                x, y = _loc(d)
                ln = _f(d, "PINLENGTH") * 10
                cong = int(_f(d, "PINCONGLOMERATE", 0))
                rot = cong & 3
                dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][rot]
                P.append(SchPrim("pin", [(x, y), (x + dx * ln, y + dy * ln)], color=col,
                                 text=d.get("NAME", ""),
                                 extra={"num": d.get("DESIGNATOR", ""), "rot": rot,
                                        "show_name": bool(cong & 0x08), "show_num": bool(cong & 0x10),
                                        "hidden": d.get("HIDDEN") == "T"}, owner=owner))
            elif r in ("4", "25", "34", "41", "33", "32", "28", "17", "18"):
                x, y = _loc(d)
                if r in ("41", "34") and x == 0 and y == 0:
                    continue
                txt = d.get("TEXT", d.get("NAME", ""))
                if r == "41" and (d.get("ISHIDDEN") == "T" or not txt):
                    continue
                if r == "34":
                    txt = d.get("TEXT", "")
                    if d.get("ISHIDDEN") == "T":
                        continue
                kind = {"4": "label", "25": "netlabel", "34": "designator", "41": "param",
                        "33": "label", "32": "label", "28": "label", "17": "power", "18": "port"}[r]
                ex = {"rot": int(_f(d, "ORIENTATION", 0)), "font": int(_f(d, "FONTID", 1))}
                if r == "18":
                    txt = d.get("NAME", "")
                    ex.update(w=_f(d, "WIDTH") * 10, h=_f(d, "HEIGHT", 10) * 10 or 100,
                              style=int(_f(d, "STYLE", 0)), io=int(_f(d, "IOTYPE", 0)))
                if r == "17":
                    ex.update(style=int(_f(d, "STYLE", 0)))
                if r == "41" and d.get("NAME") and not d.get("TEXT"):
                    continue
                P.append(SchPrim(kind, [(x, y)], text=txt, color=col, extra=ex, owner=owner))
            elif r == "29":
                P.append(SchPrim("junction", [_loc(d)], color=col, owner=owner))
            elif r in ("12", "11"):
                x, y = _loc(d)
                P.append(SchPrim("arc", [(x, y)], color=col,
                                 extra={"r": _f(d, "RADIUS") * 10, "r2": _f(d, "SECONDARYRADIUS", _f(d, "RADIUS")) * 10,
                                        "a0": _f(d, "STARTANGLE"), "a1": _f(d, "ENDANGLE", 360)}, owner=owner))
            elif r == "8":
                x, y = _loc(d)
                P.append(SchPrim("ellipse", [(x, y)], color=col,
                                 extra={"r": _f(d, "RADIUS") * 10, "r2": _f(d, "SECONDARYRADIUS", _f(d, "RADIUS")) * 10,
                                        "filled": d.get("ISSOLID") == "T", "fill": int(_f(d, "AREACOLOR", 0))},
                                 owner=owner))
        except Exception as e:
            doc.warnings.append(f"record {i} (RECORD={r}): {e}")
    return doc
