"""I/O: riconoscimento automatico del formato e salvataggio."""
from __future__ import annotations

import re
from pathlib import Path

from ..core.network import Network
from . import citi, csvio, touchstone

OPEN_FILTER = ("File S-parameter (*.s1p *.s2p *.s3p *.s4p *.s5p *.s6p *.s7p *.s8p *.s9p *.s10p *.s12p "
               "*.s16p *.ts *.cti *.citi *.csv *.txt *.dat);;Touchstone (*.s*p *.ts);;CSV (*.csv *.txt *.dat);;"
               "CITIfile (*.cti *.citi);;Tutti i file (*)")


def sniff_format(path: str | Path) -> str:
    p = str(path).lower()
    if re.search(r"\.s\d+p$", p) or p.endswith(".ts"):
        return "touchstone"
    if p.endswith((".cti", ".citi")):
        return "citi"
    head = touchstone.read_text(path)[:4000]
    first = next((ln.strip() for ln in head.splitlines() if ln.strip()), "")
    if first.upper().startswith("CITIFILE"):
        return "citi"
    if re.search(r"^\s*#\s*(HZ|KHZ|MHZ|GHZ)\b", head, re.I | re.M) or re.search(r"^\s*\[Version\]", head, re.I | re.M):
        return "touchstone"
    return "csv"


def load_networks(path: str | Path, **kw) -> list[Network]:
    """Carica un file in qualsiasi formato supportato; ritorna una lista di reti."""
    fmt = sniff_format(path)
    if fmt == "touchstone":
        return [touchstone.read_touchstone(path)]
    if fmt == "citi":
        return citi.read_citi(path)
    return [csvio.read_csv(path, **kw)]


def save_network(net: Network, path: str | Path, **kw) -> str:
    p = str(path).lower()
    if p.endswith(".csv"):
        return csvio.write_csv(net, path, **{k: v for k, v in kw.items() if k in ("fmt", "freq_unit", "sep")})
    return touchstone.write_touchstone(net, path, **{k: v for k, v in kw.items()
                                                     if k in ("fmt", "freq_unit", "version", "precision")})
