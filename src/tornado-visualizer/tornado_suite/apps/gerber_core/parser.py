"""Lettore Gerber RS-274X ed Excellon (unità interna: mm, Y verso l'alto). Scritto dalla specifica Ucamco."""
from __future__ import annotations
import math
import os
import re
from dataclasses import dataclass, field


@dataclass
class Aperture:
    kind: str                       # C R O P M
    p: tuple = ()
    polys: list = field(default_factory=list)   # solo macro: [(esposizione, [(x,y)...])]


@dataclass
class GerberLayer:
    name: str
    ops: list = field(default_factory=list)     # ("s",x1,y1,x2,y2,w,dark) ("f",x,y,ap,dark) ("r",pts,dark)
    apertures: dict = field(default_factory=dict)
    holes: list = field(default_factory=list)   # (x,y,dia) Excellon
    slots: list = field(default_factory=list)   # (x1,y1,x2,y2,dia)
    role: str = ""
    warnings: list = field(default_factory=list)

    def bbox(self):
        xs, ys = [], []
        for o in self.ops:
            if o[0] == "s":
                w = o[5] / 2
                xs += [o[1] - w, o[1] + w, o[3] - w, o[3] + w]; ys += [o[2] - w, o[2] + w, o[4] - w, o[4] + w]
            elif o[0] == "f":
                ap = self.apertures.get(o[3]); r = _ap_radius(ap)
                xs += [o[1] - r, o[1] + r]; ys += [o[2] - r, o[2] + r]
            else:
                for x, y in o[1]:
                    xs.append(x); ys.append(y)
        for x, y, d in self.holes:
            xs += [x - d / 2, x + d / 2]; ys += [y - d / 2, y + d / 2]
        for x1, y1, x2, y2, d in self.slots:
            xs += [x1, x2]; ys += [y1, y2]
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _ap_radius(ap) -> float:
    if ap is None:
        return 0.0
    if ap.kind in ("C", "P"):
        return (ap.p[0] if ap.p else 0) / 2
    if ap.kind in ("R", "O"):
        return math.hypot(ap.p[0], ap.p[1]) / 2 if len(ap.p) >= 2 else 0
    m = 0.0
    for _, pts in ap.polys:
        for x, y in pts:
            m = max(m, math.hypot(x, y))
    return m


def guess_role(name: str, text: str = "") -> str:
    n = name.lower()
    m = re.search(r"TF\.FileFunction,([^*\n]+)", text[:4000])
    if m:
        f = m.group(1).lower()
        if "copper" in f:
            return "bottom" if ("bot" in f or ",b" in f) else "top" if "top" in f else "inner"
        if "soldermask" in f: return "mask_b" if "bot" in f else "mask_t"
        if "legend" in f: return "silk_b" if "bot" in f else "silk_t"
        if "paste" in f: return "paste_b" if "bot" in f else "paste_t"
        if "profile" in f: return "outline"
        if "plated" in f or "drill" in f: return "drill"
    table = [(("f_cu", ".gtl", "top.gbr", "-top"), "top"), (("b_cu", ".gbl", "bottom.gbr", "-bot"), "bottom"),
             (("f_mask", ".gts"), "mask_t"), (("b_mask", ".gbs"), "mask_b"),
             (("f_silk", ".gto"), "silk_t"), (("b_silk", ".gbo"), "silk_b"),
             (("f_paste", ".gtp"), "paste_t"), (("b_paste", ".gbp"), "paste_b"),
             (("edge_cuts", ".gm1", ".gko", "outline", "profile"), "outline"),
             ((".drl", ".xln", ".exc", "-pth", "-npth", ".drd"), "drill")]
    for keys, role in table:
        if any(k in n for k in keys):
            return role
    return "other"


# ---------------------------------------------------------------- macro
def _eval(expr: str, vars_: dict) -> float:
    e = re.sub(r"\$(\d+)", lambda m: repr(float(vars_.get(int(m.group(1)), 0.0))), expr)
    e = e.replace("x", "*").replace("X", "*")
    if not re.fullmatch(r"[0-9eE+\-*/().\s]*", e):
        return 0.0
    try:
        return float(eval(e, {"__builtins__": {}}, {}))
    except Exception:
        return 0.0


def _rot(pts, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def _circle_pts(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _macro_polys(body: list, params: list) -> list:
    vars_ = {i + 1: v for i, v in enumerate(params)}
    out = []
    for stmt in body:
        stmt = stmt.strip()
        if not stmt or stmt.startswith("0 ") or stmt == "0":
            continue
        if stmt.startswith("$"):
            k, ex = stmt.split("=", 1)
            vars_[int(k[1:])] = _eval(ex, vars_)
            continue
        f = [_eval(t, vars_) for t in stmt.split(",")]
        code = int(f[0])
        try:
            if code == 1:
                ex, d, cx, cy = f[1], f[2], f[3], f[4]
                rot = f[5] if len(f) > 5 else 0
                out.append((ex, _rot(_circle_pts(cx, cy, d / 2), rot)))
            elif code in (2, 20):
                ex, w, x1, y1, x2, y2, rot = f[1], f[2], f[3], f[4], f[5], f[6], f[7]
                dx, dy = x2 - x1, y2 - y1
                L = math.hypot(dx, dy) or 1e-9
                nx, ny = -dy / L * w / 2, dx / L * w / 2
                out.append((ex, _rot([(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)], rot)))
            elif code == 21:
                ex, w, h, cx, cy, rot = f[1:7]
                out.append((ex, _rot([(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)], rot)))
            elif code == 22:
                ex, w, h, lx, ly, rot = f[1:7]
                out.append((ex, _rot([(lx, ly), (lx + w, ly), (lx + w, ly + h), (lx, ly + h)], rot)))
            elif code == 4:
                ex, n = f[1], int(f[2])
                pts = [(f[3 + 2 * i], f[4 + 2 * i]) for i in range(n + 1)]
                rot = f[3 + 2 * (n + 1)] if len(f) > 3 + 2 * (n + 1) else 0
                out.append((ex, _rot(pts, rot)))
            elif code == 5:
                ex, n, cx, cy, d = f[1], int(f[2]), f[3], f[4], f[5]
                rot = f[6] if len(f) > 6 else 0
                out.append((ex, _rot([(cx + d / 2 * math.cos(2 * math.pi * i / n), cy + d / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)], rot)))
            elif code == 7:
                od, id_, gap, rot = f[3], f[4], f[5], f[6]
                out.append((1.0, _rot(_circle_pts(f[1], f[2], od / 2), rot)))
                out.append((0.0, _rot(_circle_pts(f[1], f[2], id_ / 2), rot)))
        except IndexError:
            continue
    return out


# ---------------------------------------------------------------- gerber
def _split_blocks(text: str):
    """Restituisce [('x', [cmd,...]) | ('d', 'D01X..')] preservando l'ordine."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "%":
            j = text.find("%", i + 1)
            if j < 0:
                j = n
            out.append(("x", [s for s in text[i + 1:j].replace("\r", "").replace("\n", "").split("*") if s]))
            i = j + 1
        elif c in "\r\n \t":
            i += 1
        else:
            j = text.find("*", i)
            if j < 0:
                j = n
            out.append(("d", text[i:j].replace("\r", "").replace("\n", "")))
            i = j + 1
    return out


def load_gerber(path: str) -> GerberLayer:
    with open(path, "rb") as f:
        text = f.read().decode("latin-1")
    lay = GerberLayer(os.path.basename(path))
    lay.role = guess_role(lay.name, text)
    fmt = (2, 4); zero = "L"; unit = 1.0
    macros: dict[str, list] = {}
    aps = lay.apertures
    cur = None; x = y = 0.0
    interp = "G01"; multi = True; dark = True
    region = False; contour: list = []; regions_pts: list = []
    sr_stack: list = []   # (start_index, nx, ny, di, dj)

    def num(s, kind):
        if "." in s:
            return float(s) * unit
        neg = s.startswith("-"); s = s.lstrip("+-")
        ints, decs = fmt
        total = ints + decs
        if zero == "L":
            s = s.zfill(total)
        else:
            s = s.ljust(total, "0")
        v = int(s) / (10 ** decs)
        return (-v if neg else v) * unit

    def parse_xy(cmd, px, py):
        nx, ny, i, j = px, py, 0.0, 0.0
        for k, v in re.findall(r"([XYIJ])([+-]?[\d.]+)", cmd):
            val = num(v, k)
            if k == "X": nx = val
            elif k == "Y": ny = val
            elif k == "I": i = val
            else: j = val
        return nx, ny, i, j

    def emit_seg(x0, y0, x1, y1, i, j):
        nonlocal contour
        if interp == "G01":
            pts = [(x1, y1)]
        else:
            cw = interp == "G02"
            cx, cy = x0 + i, y0 + j
            if not multi:   # quadrante singolo: prova i segni
                best = None
                for si in (1, -1):
                    for sj in (1, -1):
                        c = (x0 + si * abs(i), y0 + sj * abs(j))
                        e = abs(math.hypot(x0 - c[0], y0 - c[1]) - math.hypot(x1 - c[0], y1 - c[1]))
                        if best is None or e < best[0]:
                            best = (e, c)
                cx, cy = best[1]
            r = math.hypot(x0 - cx, y0 - cy)
            a0 = math.atan2(y0 - cy, x0 - cx); a1 = math.atan2(y1 - cy, x1 - cx)
            if cw:
                if a1 >= a0: a1 -= 2 * math.pi
            else:
                if a1 <= a0: a1 += 2 * math.pi
            if abs(x1 - x0) < 1e-9 and abs(y1 - y0) < 1e-9:
                a1 = a0 + (-2 * math.pi if cw else 2 * math.pi)
            n = max(4, int(abs(a1 - a0) / (math.pi / 36)))
            pts = [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(1, n + 1)]
        if region:
            contour.extend(pts)
        else:
            w = _ap_radius(aps.get(cur)) * 2 if cur in aps else 0.0
            if cur in aps and aps[cur].kind in ("R", "O"):
                w = min(aps[cur].p[:2]) if len(aps[cur].p) >= 2 else w
            px, py = x0, y0
            for qx, qy in pts:
                lay.ops.append(("s", px, py, qx, qy, w, dark)); px, py = qx, qy

    for kind, body in _split_blocks(text):
        if kind == "x":
            i = 0
            while i < len(body):
                c = body[i]
                if c.startswith("FS"):
                    m = re.match(r"FS([LTD]?)([AI]?)X(\d)(\d)Y(\d)(\d)", c)
                    if m:
                        zero = "T" if m.group(1) == "T" else "L"; fmt = (int(m.group(3)), int(m.group(4)))
                elif c.startswith("MO"):
                    unit = 25.4 if c[2:4].upper() == "IN" else 1.0
                elif c.startswith("AM"):
                    name = c[2:]; stm = []
                    i += 1
                    while i < len(body):
                        stm.append(body[i]); i += 1
                    macros[name] = stm
                    break
                elif c.startswith("AD"):
                    m = re.match(r"ADD(\d+)([^,]+)(?:,(.*))?", c)
                    if m:
                        code, shape, par = int(m.group(1)), m.group(2), m.group(3) or ""
                        raw = [float(v) if v else 0.0 for v in re.split(r"[Xx]", par)] if par else []
                        ndim = {"C": 2, "R": 4, "O": 4, "P": 1}.get(shape, 0)      # solo le dimensioni lineari seguono l'unità
                        vals = [v * unit if k < ndim else v for k, v in enumerate(raw)]
                        if shape in ("C", "R", "O", "P"):
                            aps[code] = Aperture(shape, tuple(vals))
                        elif shape in macros:
                            mv = [float(v) for v in re.split(r"[Xx]", par)] if par else []
                            polys = _macro_polys(macros[shape], mv)
                            if unit != 1.0:
                                polys = [(e, [(a * unit, b * unit) for a, b in p]) for e, p in polys]
                            aps[code] = Aperture("M", (), polys)
                        else:
                            lay.warnings.append(f"macro sconosciuta {shape}")
                elif c.startswith("LP"):
                    dark = c[2] == "D"
                elif c.startswith("SR"):
                    m = re.match(r"SRX(\d+)Y(\d+)I([\d.+-]+)J([\d.+-]+)", c)
                    if sr_stack:
                        s0, nx, ny, di, dj = sr_stack.pop()
                        blk = lay.ops[s0:]
                        for a in range(nx):
                            for b in range(ny):
                                if a == 0 and b == 0:
                                    continue
                                for o in blk:
                                    lay.ops.append(_shift(o, a * di, b * dj))
                    if m and (int(m.group(1)) > 1 or int(m.group(2)) > 1):
                        sr_stack.append((len(lay.ops), int(m.group(1)), int(m.group(2)), float(m.group(3)) * unit, float(m.group(4)) * unit))
                i += 1
            continue
        # comando dati (può contenere più G/D)
        cmd = body
        if cmd.startswith("G04") or cmd.startswith("M"):
            continue
        for g in re.findall(r"G(\d+)", cmd):
            gi = int(g)
            if gi in (1, 2, 3): interp = f"G0{gi}"
            elif gi == 74: multi = False
            elif gi == 75: multi = True
            elif gi == 36:
                region = True; contour = [(x, y)]
            elif gi == 37:
                if len(contour) >= 3:
                    lay.ops.append(("r", contour, dark))
                region = False; contour = []
        rest = re.sub(r"G\d+", "", cmd)
        m = re.search(r"D(\d+)$", rest)
        if not m:
            if re.match(r"^[XYIJ]", rest):      # coordinata senza D: modale D01
                nx, ny, i, j = parse_xy(rest, x, y)
                emit_seg(x, y, nx, ny, i, j); x, y = nx, ny
            continue
        d = int(m.group(1))
        coords = rest[:m.start()]
        if d >= 10:
            cur = d
            continue
        nx, ny, i, j = parse_xy(coords, x, y)
        if d == 1:
            emit_seg(x, y, nx, ny, i, j)
        elif d == 2:
            if region and len(contour) >= 3:
                lay.ops.append(("r", contour, dark)); contour = []
            if region:
                contour = [(nx, ny)]
        elif d == 3:
            if cur in aps:
                lay.ops.append(("f", nx, ny, cur, dark))
        x, y = nx, ny
    return lay


def _shift(o, dx, dy):
    if o[0] == "s":
        return ("s", o[1] + dx, o[2] + dy, o[3] + dx, o[4] + dy, o[5], o[6])
    if o[0] == "f":
        return ("f", o[1] + dx, o[2] + dy, o[3], o[4])
    return ("r", [(a + dx, b + dy) for a, b in o[1]], o[2])


# ---------------------------------------------------------------- excellon
def load_excellon(path: str) -> GerberLayer:
    with open(path, "rb") as f:
        text = f.read().decode("latin-1")
    lay = GerberLayer(os.path.basename(path), role="drill")
    metric = True; zero = "L"; tools: dict[int, float] = {}
    header = True; cur = None; decfmt = None
    px = py = 0.0
    route = None
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith(";"):
            m = re.search(r"FORMAT=\{(\d*|-):(\d*|-)", ln)
            continue
        u = ln.upper()
        if u.startswith("M48"):
            header = True; continue
        if u in ("%", "M95"):
            header = False; continue
        if header or re.match(r"^T\d+C", u):
            if u.startswith(("METRIC", "M71")):
                metric = True
                if "TZ" in u: zero = "T"
                if "LZ" in u: zero = "L"
            elif u.startswith(("INCH", "M72")):
                metric = False
                if "TZ" in u: zero = "T"
                if "LZ" in u: zero = "L"
            m = re.match(r"T(\d+).*?C([\d.]+)", u)
            if m:
                d = float(m.group(2)); tools[int(m.group(1))] = d * (1.0 if metric else 25.4)
            continue
        if u.startswith(("M30", "M00")):
            break
        if u.startswith("T"):
            m = re.match(r"T(\d+)", u)
            cur = int(m.group(1)) if m else cur
            continue
        if u.startswith("G85"):
            m = re.match(r"X([+-]?[\d.]+)Y([+-]?[\d.]+)G85X([+-]?[\d.]+)Y([+-]?[\d.]+)", u)
            if m and cur in tools:
                a = [_exc(m.group(k), metric, zero) for k in (1, 2, 3, 4)]
                lay.slots.append((a[0], a[1], a[2], a[3], tools[cur]))
            continue
        if u.startswith(("G05", "G90", "G91", "G93", "FMAT", "ICI", "VER")):
            continue
        if u.startswith(("G00", "G01", "G02", "G03", "M15", "M16", "M17")):
            route = u[:3]; continue
        m = re.match(r"X([+-]?[\d.]+)(?:Y([+-]?[\d.]+))?|Y([+-]?[\d.]+)", u)
        if m and cur in tools:
            if m.group(1) is not None:
                px = _exc(m.group(1), metric, zero)
                if m.group(2) is not None: py = _exc(m.group(2), metric, zero)
            else:
                py = _exc(m.group(3), metric, zero)
            lay.holes.append((px, py, tools[cur]))
    return lay


def _exc(s, metric, zero):
    if "." in s:
        v = float(s)
        return v if metric else v * 25.4
    neg = s.startswith("-"); s = s.lstrip("+-")
    total = 6
    s = s.zfill(total) if zero == "T" else s.ljust(total, "0")
    v = int(s) / (1000.0 if metric else 10000.0)
    v = -v if neg else v
    return v if metric else v * 25.4


def load_any(path: str) -> GerberLayer:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        head = f.read(300).decode("latin-1")
    if ext in (".drl", ".xln", ".exc", ".drd") or head.lstrip().startswith("M48"):
        return load_excellon(path)
    return load_gerber(path)
