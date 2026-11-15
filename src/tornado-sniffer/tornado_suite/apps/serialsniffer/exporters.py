"""Export di una registrazione: CSV, TXT, HEX (hexdump), JSONL, RAW (.bin per flusso), PCAP."""
from __future__ import annotations

import csv
import inspect
import json
import re
import struct
from contextlib import ExitStack
from pathlib import Path
from typing import Iterable, Optional, Union

from . import decoders as _decoders
from .chunk import DIR_NAMES, GLOBAL, RX, TX
from .formats import hexdump, render
from .ssnfile import Recording, select_chunks
from .stats import StatsCollector, write_csv as _stats_csv

PathLike = Union[str, Path]
Selection = Optional[Iterable[int]]

PCAP_LINKTYPE_USER0 = 147  # DLT_USER0: payload = [canale u8][direzione u8][dati]
PCAP_MAX_PAYLOAD = 65533


def _csv_safe(text: str) -> str:
    # evita l'esecuzione di formule quando il CSV viene aperto in un foglio di calcolo
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def export_csv(
    rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
    fmt: str = "escaped", include_events: bool = True,
) -> list[Path]:
    """Colonne: t_s, utc, channel, name, dir, length, hex (lossless), text (vista `fmt`)."""
    path = Path(out)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "utc", "channel", "name", "dir", "length", "hex", "text"])
        for c in select_chunks(rec, channels, directions, include_events):
            base = [f"{c.ts_ns / 1e9:.9f}", rec.utc_iso(c.ts_ns), c.channel, rec.channel_name(c.channel)]
            if c.is_event:
                w.writerow(base + ["EVT", len(c.data), "", _csv_safe(c.text)])
            else:
                w.writerow(base + [DIR_NAMES[c.direction], len(c.data), render(c.data, "hex"),
                                   _csv_safe(render(c.data, fmt))])
    return [path]


def export_txt(
    rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
    fmt: str = "escaped", include_events: bool = True, absolute: bool = False,
) -> list[Path]:
    """Una riga per chunk: [tempo] canale dir contenuto."""
    path = Path(out)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# serialsniffer - {rec.header.get('created_utc', '')}\n")
        for idx, info in sorted(rec.channel_info().items()):
            f.write(f"# CH{idx} {info.get('name', '')} {info.get('url', '')} {info.get('config', '')}\n")
        for c in select_chunks(rec, channels, directions, include_events):
            stamp = rec.utc_iso(c.ts_ns) if absolute else f"{c.ts_ns / 1e9:12.6f}"
            name = rec.channel_name(c.channel)
            if c.is_event:
                f.write(f"[{stamp}] {name:<8} EVT  {c.text}\n")
            else:
                body = render(c.data, fmt).replace("\r", "\\r").replace("\n", "\\n")
                f.write(f"[{stamp}] {name:<8} {DIR_NAMES[c.direction]:<3}  {body}\n")
    return [path]


def export_hex(
    rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
    include_events: bool = True, width: int = 16,
) -> list[Path]:
    """Hexdump per chunk; l'offset prosegue lungo ogni flusso (canale, direzione)."""
    path = Path(out)
    offsets: dict[tuple[int, int], int] = {}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for c in select_chunks(rec, channels, directions, include_events):
            name = rec.channel_name(c.channel)
            if c.is_event:
                f.write(f"# t={c.ts_ns / 1e9:.6f} {name} EVT {c.text}\n")
                continue
            key = (c.channel, c.direction)
            off = offsets.get(key, 0)
            f.write(f"# t={c.ts_ns / 1e9:.6f} {name} {DIR_NAMES[c.direction]} len={len(c.data)}\n")
            f.write(hexdump(c.data, off, width) + "\n")
            offsets[key] = off + len(c.data)
    return [path]


def export_jsonl(
    rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
    include_events: bool = True,
) -> list[Path]:
    """Prima riga {"header": ...}, poi un oggetto per record (dati in hex, eventi in text)."""
    path = Path(out)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"header": rec.header}, ensure_ascii=False) + "\n")
        for c in select_chunks(rec, channels, directions, include_events):
            obj = {"t_ns": c.ts_ns, "ch": c.channel, "name": rec.channel_name(c.channel)}
            if c.is_event:
                obj.update(kind="event", text=c.text)
            else:
                obj.update(kind="data", dir=DIR_NAMES[c.direction], hex=c.data.hex())
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return [path]


def export_raw(
    rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
) -> list[Path]:
    """Un file binario per flusso: <out>.<canale>.<rx|tx>.bin (nessun timestamp, solo i byte)."""
    base = Path(out)
    if base.suffix:
        base = base.with_suffix("")
    written: dict[tuple[int, int], Path] = {}
    with ExitStack() as stack:
        files = {}
        for c in select_chunks(rec, channels, directions, include_events=False):
            key = (c.channel, c.direction)
            if key not in files:
                safe = re.sub(r"[^\w.-]", "_", rec.channel_name(c.channel))
                p = base.with_name(f"{base.name}.{safe}.{DIR_NAMES[c.direction].lower()}.bin")
                files[key] = stack.enter_context(open(p, "wb"))
                written[key] = p
            files[key].write(c.data)
    return list(written.values())


def export_pcap(
    rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
) -> list[Path]:
    """PCAP classico (DLT_USER0=147) apribile in Wireshark: pseudo-header di 2 byte
    [canale][direzione 0=RX 1=TX] + dati. Gli eventi non vengono esportati."""
    path = Path(out)
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, PCAP_LINKTYPE_USER0))
        for c in select_chunks(rec, channels, directions, include_events=False):
            sec, ns = divmod(rec.t0_wall_ns + c.ts_ns, 1_000_000_000)
            for i in range(0, max(len(c.data), 1), PCAP_MAX_PAYLOAD):
                pkt = bytes((c.channel & 0xFF, c.direction)) + c.data[i : i + PCAP_MAX_PAYLOAD]
                f.write(struct.pack("<IIII", sec, ns // 1000, len(pkt), len(pkt)) + pkt)
    return [path]


def decode_recording(rec: Recording, proto: str = "auto", channels: Selection = None, directions: Selection = None,
                     masters: Optional[set] = None) -> list:
    opts = {"masters": masters} if masters and proto in ("auto", "modbus", "modbus_ascii") else {}
    engine = _decoders.DecoderEngine(_decoders.create(proto, **opts))
    msgs = engine.feed_many(select_chunks(rec, channels, directions, include_events=False))
    return msgs + engine.flush()


def write_messages(msgs: Iterable, path: PathLike, names: Optional[dict[int, str]] = None) -> Path:
    """Messaggi decodificati su file: `.jsonl` (un oggetto per messaggio) oppure CSV (separatore ;, UTF-8 con BOM)."""
    path = Path(path)
    names = names or {}
    if path.suffix.lower() == ".jsonl":
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for m in msgs:
                d = m.to_dict()
                d["channel_name"] = names.get(m.channel, str(m.channel))
                d["direction"] = DIR_NAMES.get(m.direction, str(m.direction))
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        return path
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["tempo_s", "canale", "dir", "protocollo", "tipo", "chiave", "valido", "nota", "messaggio", "campi",
                    "latenza_ms", "hex"])
        for m in msgs:
            w.writerow([f"{m.ts_ns / 1e9:.6f}", _csv_safe(names.get(m.channel, str(m.channel))),
                        DIR_NAMES.get(m.direction, "?"), m.protocol, m.kind, _csv_safe(m.key), int(m.ok), _csv_safe(m.note),
                        _csv_safe(m.summary), _csv_safe("; ".join(f"{k}={v}" for k, v in m.fields)),
                        "" if m.pair_ns is None else f"{m.pair_ns / 1e6:.3f}", m.raw.hex()])
    return path


def export_decoded(rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
                   proto: str = "auto") -> list[Path]:
    """Messaggi di protocollo decodificati (CSV o, se il nome termina in .jsonl, JSONL)."""
    msgs = decode_recording(rec, proto, channels, directions)
    return [write_messages(msgs, out, {i: rec.channel_name(i) for i in rec.channel_info()})]


def export_stats(rec: Recording, out: PathLike, *, channels: Selection = None, directions: Selection = None,
                 proto: str = "auto") -> list[Path]:
    """Statistiche per chiave di messaggio (CSV, oppure JSON se il nome termina in .json)."""
    from .stats import write_json

    coll = StatsCollector({i: rec.channel_name(i) for i in rec.channel_info()})
    coll.add_many(decode_recording(rec, proto, channels, directions))
    rows = coll.rows()
    return [(write_json if str(out).lower().endswith(".json") else _stats_csv)(rows, out)]


EXPORTERS = {
    "csv": export_csv,
    "txt": export_txt,
    "hex": export_hex,
    "jsonl": export_jsonl,
    "raw": export_raw,
    "pcap": export_pcap,
    "decoded": export_decoded,
    "stats": export_stats,
}
EXPORT_KINDS = tuple(EXPORTERS)


def export(rec: Recording, kind: str, out: PathLike, **options) -> list[Path]:
    """Dispatcher: passa a ciascun exporter solo le opzioni che accetta."""
    try:
        fn = EXPORTERS[kind.lower()]
    except KeyError:
        raise ValueError(f"formato export sconosciuto: {kind!r} {EXPORT_KINDS}") from None
    accepted = inspect.signature(fn).parameters
    return fn(rec, out, **{k: v for k, v in options.items() if k in accepted and v is not None})
