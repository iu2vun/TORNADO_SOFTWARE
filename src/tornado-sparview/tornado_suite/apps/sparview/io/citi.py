"""Lettura CITIfile (.cti/.citi) di analizzatori HP/Agilent/Keysight."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import numpy as np

from ..core.network import Network
from .touchstone import read_text


class CitiError(ValueError):
    pass


def parse_citi(text: str, name: str = "", path: Optional[str] = None, z0: float = 50.0) -> list[Network]:
    lines = [ln.strip() for ln in text.splitlines()]
    if not lines or not lines[0].upper().startswith("CITIFILE"):
        raise CitiError("non è un CITIfile (manca l'intestazione CITIFILE)")
    packages: list[dict] = []
    cur: Optional[dict] = None

    def new_pkg():
        return {"name": "", "comments": [], "var": None, "npts": 0, "data": [], "var_list": [],
                "seg": [], "arrays": []}

    i = 0
    cur = new_pkg()
    while i < len(lines):
        ln = lines[i]
        up = ln.upper()
        if not ln:
            i += 1
            continue
        if up.startswith("NAME "):
            if cur["data"] or cur["arrays"]:
                packages.append(cur)
                cur = new_pkg()
            cur["name"] = ln[5:].strip()
        elif up.startswith("VAR "):
            tk = ln.split()
            cur["var"] = tk[1]
            cur["npts"] = int(tk[-1])
        elif up.startswith("DATA "):
            tk = ln.split()
            cur["data"].append((tk[1], tk[2].upper() if len(tk) > 2 else "RI"))
        elif up.startswith("SEG_LIST_BEGIN"):
            i += 1
            while i < len(lines) and not lines[i].upper().startswith("SEG_LIST_END"):
                tk = lines[i].split()
                if tk and tk[0].upper() == "SEG":
                    cur["seg"].append((float(tk[1]), float(tk[2]), int(tk[3])))
                i += 1
        elif up.startswith("VAR_LIST_BEGIN"):
            i += 1
            vals = []
            while i < len(lines) and not lines[i].upper().startswith("VAR_LIST_END"):
                if lines[i]:
                    vals.append(float(lines[i].split(",")[0]))
                i += 1
            cur["var_list"] = vals
        elif up == "BEGIN":
            i += 1
            arr = []
            while i < len(lines) and lines[i].upper() != "END":
                if lines[i]:
                    tk = lines[i].replace(";", ",").split(",")
                    arr.append([float(t) for t in tk[:2]] if len(tk) >= 2 else [float(tk[0]), 0.0])
                i += 1
            cur["arrays"].append(np.asarray(arr, float))
        elif ln.startswith("#") or up.startswith("CITIFILE") or up.startswith("CONSTANT"):
            cur["comments"].append(ln.lstrip("# ").strip())
        i += 1
    if cur["data"] or cur["arrays"]:
        packages.append(cur)
    if not packages:
        raise CitiError("CITIfile senza dati")

    nets: list[Network] = []
    for k, pkg in enumerate(packages):
        if pkg["var_list"]:
            freq = np.asarray(pkg["var_list"], float)
        elif pkg["seg"]:
            freq = np.concatenate([np.linspace(a, b, n) for a, b, n in pkg["seg"]])
        else:
            raise CitiError("CITIfile senza lista frequenze (VAR_LIST/SEG_LIST)")
        sdata: dict[tuple[int, int], np.ndarray] = {}
        for (nm, fmt), arr in zip(pkg["data"], pkg["arrays"]):
            m = re.match(r"S\[(\d+)\s*,\s*(\d+)\]", nm, re.I)
            if not m:
                continue
            if arr.shape[0] != freq.size:
                raise CitiError(f"{nm}: {arr.shape[0]} punti, attesi {freq.size}")
            a, b = arr[:, 0], arr[:, 1]
            if fmt == "RI":
                v = a + 1j * b
            elif fmt in ("MAGANGLE", "MA"):
                v = a * np.exp(1j * np.deg2rad(b))
            elif fmt in ("DBANGLE", "DB"):
                v = 10 ** (a / 20) * np.exp(1j * np.deg2rad(b))
            else:
                raise CitiError(f"formato dati CITI sconosciuto: {fmt}")
            sdata[(int(m.group(1)) - 1, int(m.group(2)) - 1)] = v
        if not sdata:
            continue
        n = max(max(i, j) for i, j in sdata) + 1
        s = np.zeros((freq.size, n, n), complex)
        for (a, b), v in sdata.items():
            s[:, a, b] = v
        nm = name if len(packages) == 1 else f"{name}[{pkg['name'] or k + 1}]"
        nets.append(Network(freq, s, z0, name=nm, path=path, comments=pkg["comments"], source_format="CITIfile"))
    if not nets:
        raise CitiError("nessun parametro S[i,j] trovato nel CITIfile")
    return nets


def read_citi(path: str | Path, **kw) -> list[Network]:
    return parse_citi(read_text(path), name=Path(path).stem, path=str(path), **kw)
