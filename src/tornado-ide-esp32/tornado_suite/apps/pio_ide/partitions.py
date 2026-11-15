"""Tabella delle partizioni ESP32 (partitions.csv): lettura, verifica, scrittura, modelli."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

FLASH_SIZES = {"2 MB": 0x200000, "4 MB": 0x400000, "8 MB": 0x800000, "16 MB": 0x1000000, "32 MB": 0x2000000}
TABLE_OFFSET = 0x9000            # inizio tabella delle partizioni: la prima partizione parte qui
APP_ALIGN = 0x10000              # le partizioni app vanno allineate a 64 KB
DATA_ALIGN = 0x1000
TYPES = ("app", "data")
SUBTYPES = {
    "app": ("factory", "ota_0", "ota_1", "ota_2", "ota_3", "test"),
    "data": ("nvs", "ota", "phy", "nvs_keys", "coredump", "spiffs", "littlefs", "fat", "undefined", "esphttpd"),
}


def parse_size(s: str) -> int:
    s = s.strip()
    if not s:
        raise ValueError("valore vuoto")
    m = re.fullmatch(r"(0x[0-9a-fA-F]+|\d+)\s*([KkMm]?)", s)
    if not m:
        raise ValueError(f"valore non valido: «{s}»")
    n = int(m[1], 16) if m[1].lower().startswith("0x") else int(m[1])
    return n * {"": 1, "k": 1024, "m": 1024 * 1024}[m[2].lower()]


def fmt_size(n: int) -> str:
    for unit, div in (("M", 1 << 20), ("K", 1 << 10)):
        if n % div == 0 and n >= div:
            return f"{n // div}{unit}"
    return hex(n)


@dataclass
class Part:
    name: str
    type: str
    subtype: str
    offset: Optional[int]     # None = calcolato da ESP-IDF (si può lasciare vuoto)
    size: int
    flags: str = ""


def parse_csv(text: str) -> list[Part]:
    out: list[Part] = []
    for n, raw in enumerate(text.splitlines(), 1):
        ln = raw.split("#", 1)[0].strip()
        if not ln:
            continue
        f = [x.strip() for x in ln.split(",")]
        if len(f) < 5:
            raise ValueError(f"riga {n}: servono almeno 5 campi (Name, Type, SubType, Offset, Size)")
        out.append(Part(f[0], f[1].lower(), f[2].lower(), parse_size(f[3]) if f[3] else None, parse_size(f[4]), f[5] if len(f) > 5 else ""))
    return out


def to_csv(parts: list[Part]) -> str:
    lines = ["# Name,   Type, SubType, Offset,  Size, Flags"]
    for p in parts:
        lines.append(f"{p.name}, {p.type}, {p.subtype}, {hex(p.offset) if p.offset is not None else ''}, {fmt_size(p.size)}" + (f", {p.flags}" if p.flags else ","))
    return "\n".join(lines).replace(",,", ",") + "\n"


def layout(parts: list[Part]) -> list[Part]:
    """Assegna gli offset mancanti come fa ESP-IDF (app allineate a 64 KB, dati a 4 KB)."""
    cur = TABLE_OFFSET
    out: list[Part] = []
    for p in parts:
        off = p.offset
        if off is None:
            al = APP_ALIGN if p.type == "app" else DATA_ALIGN
            off = (cur + al - 1) // al * al
        out.append(Part(p.name, p.type, p.subtype, off, p.size, p.flags))
        cur = off + p.size
    return out


def validate(parts: list[Part], flash: int) -> list[str]:
    """Elenco di problemi (vuoto = tabella valida)."""
    errs: list[str] = []
    if not parts:
        return ["La tabella è vuota"]
    names = [p.name for p in parts]
    for p in parts:
        if not p.name or len(p.name) > 15:
            errs.append(f"«{p.name}»: il nome deve avere 1-15 caratteri")
        if p.type not in TYPES:
            errs.append(f"«{p.name}»: tipo «{p.type}» non valido (app o data)")
        elif p.subtype not in SUBTYPES[p.type] and not p.subtype.isdigit() and not p.subtype.lower().startswith("0x"):
            errs.append(f"«{p.name}»: sottotipo «{p.subtype}» sconosciuto per il tipo {p.type}")
        if p.size <= 0:
            errs.append(f"«{p.name}»: dimensione non valida")
    for n in {x for x in names if names.count(x) > 1}:
        errs.append(f"Nome duplicato: «{n}»")
    placed = layout(parts)
    for p in placed:
        al = APP_ALIGN if p.type == "app" else DATA_ALIGN
        if p.offset is not None and p.offset % al:
            errs.append(f"«{p.name}»: offset {hex(p.offset)} non allineato a {fmt_size(al)}")
        if p.offset is not None and p.offset < TABLE_OFFSET:
            errs.append(f"«{p.name}»: offset {hex(p.offset)} sovrapposto a bootloader/tabella (minimo {hex(TABLE_OFFSET)})")
        if p.type == "app" and p.size % 0x1000:
            errs.append(f"«{p.name}»: la dimensione di una app deve essere multipla di 4 KB")
    ordered = sorted(placed, key=lambda p: p.offset or 0)
    for a, b in zip(ordered, ordered[1:]):
        if (a.offset or 0) + a.size > (b.offset or 0):
            errs.append(f"«{a.name}» e «{b.name}» si sovrappongono")
    end = max(((p.offset or 0) + p.size for p in placed), default=0)
    if end > flash:
        errs.append(f"La tabella occupa {fmt_size(end)} ma la flash è {fmt_size(flash)}")
    subs = [p.subtype for p in parts]
    if any(s.startswith("ota_") for s in subs) and "ota" not in subs:
        errs.append("Con le partizioni ota_N serve anche una partizione data/ota (otadata)")
    if not any(p.type == "app" for p in parts):
        errs.append("Manca una partizione app (factory o ota_0)")
    return errs


def used(parts: list[Part]) -> int:
    pl = layout(parts)
    return max(((p.offset or 0) + p.size for p in pl), default=0)


PRESETS: dict[str, tuple[str, int]] = {}


def _p(name: str, flash: int, text: str) -> None:
    PRESETS[name] = (text, flash)


_p("4 MB · una app da 1,25 MB + file system", 0x400000,
   "nvs, data, nvs, 0x9000, 20K\notadata, data, ota, 0xe000, 8K\napp0, app, ota_0, 0x10000, 1280K\nspiffs, data, spiffs, 0x150000, 0x2B0000\n")
_p("4 MB · OTA, 2 app da 1,9 MB, senza file system", 0x400000,
   "nvs, data, nvs, 0x9000, 20K\notadata, data, ota, 0xe000, 8K\napp0, app, ota_0, 0x10000, 0x1E0000\napp1, app, ota_1, 0x1F0000, 0x1E0000\n")
_p("4 MB · OTA + LittleFS (2 app da 1,25 MB)", 0x400000,
   "nvs, data, nvs, 0x9000, 20K\notadata, data, ota, 0xe000, 8K\napp0, app, ota_0, 0x10000, 1280K\napp1, app, ota_1, 0x150000, 1280K\nspiffs, data, spiffs, 0x290000, 1408K\n")
_p("4 MB · app grande, niente OTA (3 MB)", 0x400000,
   "nvs, data, nvs, 0x9000, 20K\nphy_init, data, phy, 0xe000, 4K\nfactory, app, factory, 0x10000, 3M\nspiffs, data, spiffs, , 0xE0000\n")
_p("8 MB · OTA + LittleFS ampio", 0x800000,
   "nvs, data, nvs, 0x9000, 20K\notadata, data, ota, 0xe000, 8K\napp0, app, ota_0, 0x10000, 3M\napp1, app, ota_1, , 3M\nspiffs, data, spiffs, , 0x1E0000\n")
_p("16 MB · OTA + LittleFS ampio", 0x1000000,
   "nvs, data, nvs, 0x9000, 20K\notadata, data, ota, 0xe000, 8K\napp0, app, ota_0, 0x10000, 6M\napp1, app, ota_1, , 6M\nspiffs, data, spiffs, , 0x3E0000\n")
# tolgo i modelli con valori ancora da verificare quando non superano la propria validazione
for _k in [k for k, (t, f) in PRESETS.items() if validate(parse_csv(t), f)]:
    del PRESETS[_k]
