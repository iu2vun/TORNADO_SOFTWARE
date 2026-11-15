"""Lettura/scrittura Touchstone v1.x e v2.0 (.sNp, .ts), N porte generico.

Supporta: S/Y/Z/H/G, formati RI/MA/DB, unità Hz..GHz, impedenze di riferimento per porta
([Reference]), matrici Full/Lower/Upper, ordine 2-porte 12_21/21_12, dati di rumore, commenti.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Optional

import numpy as np

from ..core import params
from ..core.network import Network, NoiseData

FREQ_MULT = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


class TouchstoneError(ValueError):
    pass


def read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            txt = raw.decode(enc)
            if enc == "utf-16" and "\x00" in txt:
                continue
            return txt
        except UnicodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def nports_from_name(path: str) -> Optional[int]:
    m = re.search(r"\.s(\d+)p$", path.lower())
    return int(m.group(1)) if m else None


def _floats(tokens: list[str], lineno: int) -> list[float]:
    out = []
    for t in tokens:
        try:
            out.append(float(t))
        except ValueError:
            try:
                out.append(float(t.replace("D", "E").replace("d", "e")))
            except ValueError:
                raise TouchstoneError(f"riga {lineno}: valore non numerico {t!r}")
    return out


def _infer_nports(lines: list[list[float]]) -> int:
    flat = [v for ln in lines for v in ln]
    for n in range(1, 65):
        rec = 1 + 2 * n * n
        if len(flat) >= rec and len(flat) % rec == 0:
            f = np.array(flat[0::rec])
            if np.all(np.diff(f) > 0):
                return n
    first = len(lines[0])
    n2 = (first - 1) // 2
    r = int(round(math.sqrt(n2)))
    if first >= 3 and r * r == n2:
        return r
    if first >= 7 and (first - 1) % 2 == 0:
        return (first - 1) // 2
    raise TouchstoneError("impossibile determinare il numero di porte (usare estensione .sNp)")


def _to_complex(a: np.ndarray, b: np.ndarray, fmt: str) -> np.ndarray:
    if fmt == "RI":
        return a + 1j * b
    ang = np.deg2rad(b)
    if fmt == "MA":
        return a * np.exp(1j * ang)
    if fmt == "DB":
        return 10 ** (a / 20.0) * np.exp(1j * ang)
    raise TouchstoneError(f"formato dati sconosciuto: {fmt}")


def parse_touchstone(text: str, name: str = "", path: Optional[str] = None,
                     nports_hint: Optional[int] = None,
                     zyhg_normalized: bool = False) -> Network:
    version = 1.0
    nports = nports_hint
    nfreq_decl = None
    two_port_order = "21_12"
    matrix_format = "full"
    reference: list[float] = []
    ref_pending = False
    mixed_mode = None
    opt = None
    comments: list[str] = []
    section = "data"
    prev_section = "data"
    data_lines: list[tuple[str, list[float]]] = []

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if section == "info":
            if line.lower().startswith("[end information]"):
                section = prev_section
            else:
                comments.append(line)
            continue
        if "!" in line:
            k = line.index("!")
            cm = line[k + 1:].strip()
            if cm or k == 0:
                comments.append(cm)
            line = line[:k].strip()
            if not line:
                continue
        if line.startswith("["):
            m = re.match(r"\[([^\]]+)\]\s*(.*)", line)
            if not m:
                raise TouchstoneError(f"riga {lineno}: parola chiave malformata")
            key, rest = m.group(1).strip().lower(), m.group(2).strip()
            ref_pending = False
            if key == "version":
                version = float(rest.split()[0])
            elif key == "number of ports":
                nports = int(rest)
            elif key == "two-port data order":
                two_port_order = rest.replace(" ", "").lower()
            elif key == "number of frequencies":
                nfreq_decl = int(rest)
            elif key == "reference":
                reference = _floats(rest.split(), lineno) if rest else []
                ref_pending = True
            elif key == "matrix format":
                matrix_format = rest.lower()
            elif key == "mixed-mode order":
                mixed_mode = rest
            elif key == "network data":
                section = "data"
            elif key == "noise data":
                section = "noise"
            elif key == "begin information":
                prev_section = section
                section = "info"
            elif key == "end":
                break
            continue
        if line.startswith("#"):
            if opt is None:
                tk = line[1:].upper().split()
                fmult, param, fmt, rval = 1e9, "S", "MA", 50.0
                i = 0
                while i < len(tk):
                    t = tk[i]
                    if t in FREQ_MULT:
                        fmult = FREQ_MULT[t]
                    elif t in ("S", "Y", "Z", "H", "G"):
                        param = t
                    elif t in ("RI", "MA", "DB"):
                        fmt = t
                    elif t == "R":
                        if i + 1 >= len(tk):
                            raise TouchstoneError(f"riga {lineno}: 'R' senza valore")
                        rval = float(tk[i + 1])
                        i += 1
                    else:
                        raise TouchstoneError(f"riga {lineno}: token opzione sconosciuto {t!r}")
                    i += 1
                opt = (fmult, param, fmt, rval)
            continue
        toks = _floats(line.split(), lineno)
        if ref_pending and nports and len(reference) < nports:
            reference.extend(toks)
            continue
        ref_pending = False
        data_lines.append((section, toks))

    if opt is None:
        opt = (1e9, "S", "MA", 50.0)
    fmult, param, fmt, rval = opt
    if not data_lines:
        raise TouchstoneError("nessun dato nel file")

    net_lines = [t for s, t in data_lines if s == "data"]
    if nports is None:
        nports = _infer_nports(net_lines)
    n = nports

    if matrix_format == "full":
        pairs = n * n
    elif matrix_format in ("lower", "upper"):
        pairs = n * (n + 1) // 2
    else:
        raise TouchstoneError(f"[Matrix Format] sconosciuto: {matrix_format}")
    rec = 1 + 2 * pairs

    rows: list[list[float]] = []
    noise_rows: list[list[float]] = []
    acc: list[float] = []
    last_f = None
    in_noise = False
    for sec, toks in data_lines:
        if sec == "noise" or in_noise:
            noise_rows.append(toks)
            continue
        if n == 2 and not acc and len(toks) == 5 and last_f is not None and toks[0] < last_f:
            in_noise = True
            noise_rows.append(toks)
            continue
        acc.extend(toks)
        while len(acc) >= rec:
            rows.append(acc[:rec])
            last_f = acc[0]
            del acc[:rec]
    if acc:
        raise TouchstoneError(f"record dati incompleto: {len(acc)} valori residui (attesi {rec} per record)")
    if not rows:
        raise TouchstoneError("nessun record dati completo")
    if nfreq_decl is not None and nfreq_decl != len(rows):
        comments.append(f"ATTENZIONE: [Number of Frequencies]={nfreq_decl} ma letti {len(rows)} punti")

    arr = np.asarray(rows, float)
    nf = arr.shape[0]
    freq = arr[:, 0] * fmult
    vals = _to_complex(arr[:, 1::2], arr[:, 2::2], fmt)     # (nf, pairs)

    mat = np.zeros((nf, n, n), complex)
    if matrix_format == "full":
        if n == 2 and two_port_order == "21_12":
            mat[:, 0, 0], mat[:, 1, 0], mat[:, 0, 1], mat[:, 1, 1] = (vals[:, 0], vals[:, 1],
                                                                       vals[:, 2], vals[:, 3])
        else:
            mat = vals.reshape(nf, n, n).copy()
    else:
        k = 0
        for i in range(n):
            rng = range(i + 1) if matrix_format == "lower" else range(i, n)
            for j in rng:
                mat[:, i, j] = vals[:, k]
                mat[:, j, i] = vals[:, k]
                k += 1

    if reference:
        if len(reference) < n:
            raise TouchstoneError("[Reference] con meno valori delle porte")
        z0 = np.array(reference[:n], float)
    else:
        z0 = np.full(n, rval)

    if param == "S":
        s = mat
    else:
        if param in ("Y", "Z") and zyhg_normalized:
            mat = mat * z0[None, :, None] if param == "Z" else mat / z0[None, :, None]
        if param == "Z":
            s = params.z_to_s(mat, z0)
        elif param == "Y":
            s = params.y_to_s(mat, z0)
        elif param == "H":
            s = params.z_to_s(params.h_to_z(mat), z0)
        else:
            s = params.z_to_s(params.g_to_z(mat), z0)

    noise = None
    if noise_rows and n == 2:
        try:
            nr = np.asarray([r for r in noise_rows if len(r) == 5], float)
            if len(nr):
                rn = nr[:, 4] * (z0[0] if version < 2.0 else 1.0)
                noise = NoiseData(nr[:, 0] * fmult, nr[:, 1],
                                  nr[:, 2] * np.exp(1j * np.deg2rad(nr[:, 3])), rn)
        except Exception:
            noise = None
    if mixed_mode:
        comments.append(f"[Mixed-Mode Order] {mixed_mode}")

    net = Network(freq, s, z0, name=name, path=path, comments=comments, noise=noise,
                  source_format=f"Touchstone v{version:g} ({param} {fmt})")
    return net


def read_touchstone(path: str | Path, zyhg_normalized: bool = False) -> Network:
    path = str(path)
    return parse_touchstone(read_text(path), name=Path(path).stem, path=path,
                            nports_hint=nports_from_name(path), zyhg_normalized=zyhg_normalized)


# ---------------------------------------------------------------------------- writer
_UNITS = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}


def _fmt_pair(c: complex, fmt: str, prec: int) -> tuple[str, str]:
    if fmt == "RI":
        a, b = c.real, c.imag
    else:
        mag = abs(c)
        ang = math.degrees(math.atan2(c.imag, c.real)) if mag > 0 else 0.0
        a = mag if fmt == "MA" else 20 * math.log10(max(mag, 1e-30))
        b = ang
    return f"{a:.{prec}e}", f"{b:.{prec}e}"


def format_touchstone(net: Network, fmt: str = "RI", freq_unit: str = "Hz", version: int = 1,
                      precision: int = 10, extra_comments: Optional[list[str]] = None) -> str:
    fmt = fmt.upper()
    if fmt not in ("RI", "MA", "DB"):
        raise ValueError("fmt deve essere RI, MA o DB")
    if freq_unit not in _UNITS:
        raise ValueError("unità di frequenza non valida")
    mult = _UNITS[freq_unit]
    n = net.nports
    out: list[str] = []
    for c in (net.comments or []):
        if not c.startswith("[Mixed-Mode"):
            out.append(f"! {c}")
    for c in (extra_comments or []):
        out.append(f"! {c}")
    uniform_z0 = bool(np.allclose(net.z0, net.z0[0]))
    if version == 2:
        out.insert(0, "[Version] 2.0")
    out.append(f"# {freq_unit.upper()} S {fmt} R {net.z0[0]:g}")
    if version == 2:
        out.append(f"[Number of Ports] {n}")
        if n == 2:
            out.append("[Two-Port Data Order] 21_12")
        out.append(f"[Number of Frequencies] {net.nfreq}")
        out.append("[Reference] " + " ".join(f"{z:g}" for z in net.z0))
        out.append("[Matrix Format] Full")
        out.append("[Network Data]")
    elif not uniform_z0:
        out.append("! ATTENZIONE: impedenze di riferimento diverse per porta non rappresentabili in v1: "
                   + " ".join(f"{z:g}" for z in net.z0))
    for k in range(net.nfreq):
        f = f"{net.freq[k] / mult:.15g}"
        m = net.s[k]
        if n == 1:
            a, b = _fmt_pair(m[0, 0], fmt, precision)
            out.append(f"{f} {a} {b}")
        elif n == 2:
            parts = []
            for (i, j) in ((0, 0), (1, 0), (0, 1), (1, 1)):
                parts.extend(_fmt_pair(m[i, j], fmt, precision))
            out.append(f"{f} " + " ".join(parts))
        else:
            for i in range(n):
                pairs = []
                for j in range(n):
                    pairs.extend(_fmt_pair(m[i, j], fmt, precision))
                # max 4 coppie per riga (specifica)
                chunks = [pairs[c:c + 8] for c in range(0, len(pairs), 8)]
                for ci, ch in enumerate(chunks):
                    prefix = f"{f} " if (i == 0 and ci == 0) else "    "
                    out.append(prefix + " ".join(ch))
    if net.noise is not None and n == 2:
        if version == 2:
            out.append("[Noise Data]")
        nz = net.noise
        for k in range(nz.freq.size):
            g = nz.gamma_opt[k]
            rn = nz.rn[k] / (net.z0[0] if version < 2 else 1.0)
            out.append(f"{nz.freq[k] / mult:.15g} {nz.nfmin_db[k]:.{precision}e} "
                       f"{abs(g):.{precision}e} {math.degrees(math.atan2(g.imag, g.real)):.{precision}e} "
                       f"{rn:.{precision}e}")
    if version == 2:
        out.append("[End]")
    return "\n".join(out) + "\n"


def write_touchstone(net: Network, path: str | Path, **kw) -> str:
    path = Path(path)
    txt = format_touchstone(net, **kw)
    path.write_text(txt, encoding="utf-8")
    return str(path)
