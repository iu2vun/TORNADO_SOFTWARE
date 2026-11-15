"""CLI: python -m serialsniffer {gui,ports,sniff,replay,export,info,autobaud,decode,stats,demo,simulate-modbus} ..."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from typing import Optional

import serial
import serial.tools.list_ports

from .autobaud import COMMON_BAUDS, detect_baud
from .chunk import DIR_NAMES, RX, TX, Chunk
from . import decoders
from .config import PortConfig, parse_port_spec
from .exporters import EXPORT_KINDS, export
from .formats import NUMERIC_FORMATS, hexdump, render
from .player import Player
from .session import Session
from .ssnfile import Recording, SsnError, read_ssn, select_chunks
from .stats import StatsCollector, format_table, write_csv, write_json

_DIRS = {"rx": RX, "tx": TX}


def _format_line(c: Chunk, names: dict[int, str], fmt: str) -> str:
    head = f"[{c.ts_ns / 1e9:12.6f}] {names.get(c.channel, '*'):<8}"
    if c.is_event:
        return f"{head} {'EVT':<3}  {c.text}"
    if fmt == "hexdump":
        body = hexdump(c.data).replace("\n", "\n" + " " * (len(head) + 6))
    elif fmt in NUMERIC_FORMATS or fmt in ("ascii", "escaped"):
        body = render(c.data, fmt)
    else:
        body = render(c.data, fmt).replace("\r", "\\r").replace("\n", "\\n")
    return f"{head} {DIR_NAMES.get(c.direction, '?'):<3}  {body}"


def _split(text: Optional[str]) -> Optional[list[str]]:
    return None if not text else [t.strip() for t in text.split(",") if t.strip()]


def _parse_dirs(text: Optional[str]) -> Optional[list[int]]:
    toks = _split(text)
    if toks is None or "both" in toks:
        return None
    try:
        return [_DIRS[t.lower()] for t in toks]
    except KeyError as exc:
        raise ValueError(f"direzione non valida: {exc.args[0]!r} (rx, tx, both)") from None


def _parse_channels(rec: Recording, text: Optional[str]) -> Optional[list[int]]:
    toks = _split(text)
    return None if toks is None else [rec.resolve_channel(t) for t in toks]


def cmd_gui(_: argparse.Namespace) -> int:
    try:
        from .gui.app import run
    except ImportError as exc:
        print(f"errore: GUI non disponibile ({exc}). Installa PySide6: pip install PySide6", file=sys.stderr)
        return 2
    return run()


def cmd_ports(_: argparse.Namespace) -> int:
    ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
    if not ports:
        print("nessuna porta seriale trovata")
    for p in ports:
        print(f"{p.device:<16} {p.description}  [{p.hwid}]")
    return 0


def cmd_sniff(a: argparse.Namespace) -> int:
    default = PortConfig.parse(a.config)
    sess = Session()
    gap = None if a.gap is None else a.gap / 1000.0
    for i, spec in enumerate(a.ports):
        name, url, cfg = parse_port_spec(spec, default)
        sess.add_channel(name or f"CH{i}", url, cfg, reconnect=not a.no_reconnect,
                         monitor_lines=a.lines, idle_gap_s=gap)
    for b in a.bridge or []:
        bidir = ":" in b
        left, right = b.split(":" if bidir else ">", 1)
        sess.bridge(left, right, bidirectional=bidir)

    names = {c.index: c.name for c in sess.channels}
    lock = threading.Lock()

    def show(c: Chunk) -> None:
        line = _format_line(c, names, a.fmt)
        with lock:
            print(line, flush=True)

    if not a.quiet:
        sess.add_listener(show)
    sess.start()
    try:
        if a.record:
            path = sess.start_recording(datetime.now().strftime(a.record))
            print(f"# registrazione: {path}", file=sys.stderr)
        deadline = None if not a.duration else time.monotonic() + a.duration
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.2)
            if sess.recording_error:
                print(f"errore registrazione: {sess.recording_error}", file=sys.stderr)
                break
    except KeyboardInterrupt:
        pass
    finally:
        sess.stop()
    for s in sess.stats():
        print(f"# {s['name']}: RX {s['rx_bytes']} B, TX {s['tx_bytes']} B", file=sys.stderr)
    return 0


def cmd_replay(a: argparse.Namespace) -> int:
    rec = read_ssn(a.file)
    if rec.truncated:
        print(f"attenzione: file troncato ({rec.error}), riproduco i record validi", file=sys.stderr)
    channels = _parse_channels(rec, a.channels)
    directions = _parse_dirs(a.dirs)
    info = rec.channel_info()
    names = {i: rec.channel_name(i) for i in info}
    names.setdefault(255, "*")

    opened: list[serial.SerialBase] = []
    sinks = {}
    try:
        for spec in a.map or []:
            name, url, cfg = parse_port_spec(spec)
            if name is None:
                raise ValueError(f"--map richiede CANALE=PORTA[@config]: {spec!r}")
            idx = rec.resolve_channel(name)
            if cfg is None:
                hdr_cfg = info.get(idx, {}).get("config")
                cfg = PortConfig.parse(hdr_cfg) if hdr_cfg else PortConfig()
            ser = serial.serial_for_url(url, do_not_open=True, **cfg.serial_kwargs())
            ser.write_timeout = 2.0
            ser.open()
            opened.append(ser)
            sinks[idx] = ser.write

        lock = threading.Lock()

        def show(c: Chunk) -> None:
            line = _format_line(c, names, a.fmt)
            with lock:
                print(line, flush=True)

        player = Player(
            rec, sinks=sinks, on_chunk=None if a.quiet else show, speed=a.speed, loop=a.loop,
            max_gap_s=a.max_gap, channels=channels, directions=directions,
        )
        try:
            if a.start:
                player.seek(int(a.start * 1e9))
            player.play()
            while not player.wait_finished(0.2):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            player.close()
        if player.last_error:
            print(f"errore durante la riproduzione: {player.last_error}", file=sys.stderr)
            return 1
    finally:
        for ser in opened:
            ser.close()
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    rec = read_ssn(a.file)
    if rec.truncated:
        print(f"attenzione: file troncato ({rec.error}), esporto i record validi", file=sys.stderr)
    paths = export(
        rec, a.to, a.output,
        channels=_parse_channels(rec, a.channels), directions=_parse_dirs(a.dirs),
        fmt=a.fmt, include_events=not a.no_events,
        absolute=a.absolute or None,
    )
    for p in paths:
        print(p)
    return 0


def cmd_info(a: argparse.Namespace) -> int:
    rec = read_ssn(a.file)
    print(json.dumps({"header": rec.header, "summary": rec.summary()}, indent=2, ensure_ascii=False))
    return 0


def _parse_masters(rec: Recording, items: Optional[list[str]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for item in items or []:
        name, _, d = item.rpartition(":")
        if not name or d.lower() not in _DIRS:
            raise ValueError(f"--master: atteso CANALE:rx|tx, ricevuto {item!r}")
        out.add((rec.resolve_channel(name), _DIRS[d.lower()]))
    return out


def _decode_recording(a: argparse.Namespace) -> tuple[Recording, list, decoders.Decoder]:
    for p in a.plugin or []:
        decoders.load_plugin(p)
    rec = read_ssn(a.file)
    if rec.truncated:
        print(f"attenzione: file troncato ({rec.error}), uso i record validi", file=sys.stderr)
    opts: dict = {}
    if a.proto in ("modbus", "modbus_ascii", "auto"):
        masters = _parse_masters(rec, getattr(a, "master", None))
        if masters:
            opts["masters"] = masters
    engine = decoders.DecoderEngine(decoders.create(a.proto, **opts))
    chunks = select_chunks(rec, _parse_channels(rec, a.channels), _parse_dirs(a.dirs), include_events=False)
    msgs = engine.feed_many(chunks)
    msgs += engine.flush()
    return rec, msgs, engine.decoder


def cmd_decode(a: argparse.Namespace) -> int:
    rec, msgs, decoder = _decode_recording(a)
    names = {i: rec.channel_name(i) for i in rec.channel_info()}
    total = bad = 0
    for m in msgs:
        total += 1
        bad += not m.ok
        if a.errors_only and m.ok:
            continue
        if a.json:
            d = m.to_dict()
            d["channel_name"] = names.get(m.channel, str(m.channel))
            print(json.dumps(d, ensure_ascii=False))
            continue
        flag = " " if m.ok else "!"
        print(f"[{m.ts_ns / 1e9:12.6f}] {names.get(m.channel, '*'):<8} {DIR_NAMES.get(m.direction, '?'):<3}{flag} {m.summary}"
              + (f"  <{m.note}>" if m.note and not m.ok else ""))
        if a.fields:
            for k, v in m.fields:
                print(f"{'':<30}{k}: {v}")
    print(f"# {total} messaggi, {bad} non validi", file=sys.stderr)
    assign = getattr(decoder, "assignments", None)
    if assign:
        for idx, proto in assign().items():
            label = decoders.DECODERS[proto].label if proto else "non riconosciuto"
            print(f"# {names.get(idx, idx)}: {label}", file=sys.stderr)
    return 1 if (a.fail_on_error and bad) else 0


def cmd_stats(a: argparse.Namespace) -> int:
    rec, msgs, _ = _decode_recording(a)
    coll = StatsCollector({i: rec.channel_name(i) for i in rec.channel_info()})
    coll.add_many(msgs)
    rows = coll.rows()
    if a.output:
        out = (write_json if a.output.lower().endswith(".json") else write_csv)(rows, a.output)
        print(out)
    elif a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(format_table(rows))
    return 0


def cmd_simulate_modbus(a: argparse.Namespace) -> int:
    import serial

    from .simulate import ModbusSlave, serve

    _name, url, cfg = parse_port_spec(a.port, PortConfig.parse(a.config))
    slave = ModbusSlave(a.addr, a.regs)
    ser = serial.serial_for_url(url, timeout=0.01, **cfg.serial_kwargs())
    stop = threading.Event()

    def show(req: bytes, resp: Optional[bytes]) -> None:
        if a.verbose:
            print(f"<- {req.hex(' ').upper()}\n-> {resp.hex(' ').upper() if resp else '(nessuna risposta)'}", flush=True)

    print(f"# slave Modbus RTU addr={a.addr} su {url} ({cfg}), {a.regs} registri/bobine. Ctrl+C per fermare", file=sys.stderr)
    timer = threading.Timer(a.duration, stop.set) if a.duration else None
    if timer:
        timer.start()
    try:
        serve(ser, slave, stop, cfg.baudrate, show)
    except KeyboardInterrupt:
        pass
    finally:
        if timer:
            timer.cancel()
        ser.close()
    print(f"# richieste servite: {slave.requests}", file=sys.stderr)
    return 0


def cmd_demo(a: argparse.Namespace) -> int:
    from .demo import make_demo

    print(make_demo(a.output, a.seconds))
    return 0


def cmd_autobaud(a: argparse.Namespace) -> int:
    candidates = [int(x) for x in a.candidates.split(",")] if a.candidates else COMMON_BAUDS
    print(f"# ascolto {a.url} a {len(candidates)} baud, {a.time:g}s ciascuno: la linea deve trasmettere",
          file=sys.stderr)
    results = detect_baud(
        a.url, candidates, framing=a.framing, sample_s=a.time,
        on_progress=lambda r: print(f"  {r.baudrate:>8}  byte={r.nbytes:<6} {r.error or ''}",
                                    file=sys.stderr, flush=True),
    )
    print(f"{'baud':>8}  {'score':>5}  byte")
    for r in results[: a.top]:
        print(f"{r.baudrate:>8}  {r.score:5.2f}  {r.nbytes}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="serialsniffer", description="Sniffer seriale: acquisizione, registrazione, replay, export")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gui", help="apre l'interfaccia grafica (default senza argomenti)").set_defaults(func=cmd_gui)
    sub.add_parser("ports", help="elenca le porte seriali").set_defaults(func=cmd_ports)

    s = sub.add_parser("sniff", help="acquisisce da una o più porte")
    s.add_argument("ports", nargs="+", metavar="PORTA",
                   help="[nome=]URL[@config], es. A=COM3@115200,8N1  B=/dev/ttyUSB1  loop://")
    s.add_argument("-c", "--config", default="115200 8N1", help="config di default (baud bit/parità/stop [flow])")
    s.add_argument("-r", "--record", metavar="FILE.ssn", help="registra su file (accetta codici strftime, es. cap_%%Y%%m%%d_%%H%%M%%S.ssn)")
    s.add_argument("-f", "--fmt", default="escaped", help="hex|dec|oct|bin|ascii|escaped|hexdump|<codec> (default escaped)")
    s.add_argument("-d", "--duration", type=float, help="secondi, poi esce")
    s.add_argument("--bridge", action="append", metavar="A:B",
                   help="PC in mezzo alla linea: A:B bidirezionale, A>B solo da A verso B (ripetibile)")
    s.add_argument("--gap", type=float, metavar="MS",
                   help="gap idle per unire i byte in un unico messaggio: default auto (>=20 ms), 0 = nessun raggruppamento")
    s.add_argument("--lines", action="store_true", help="monitora le linee modem (CTS/DSR/RI/CD)")
    s.add_argument("--no-reconnect", action="store_true", help="non riconnettere se la porta sparisce")
    s.add_argument("-q", "--quiet", action="store_true", help="non stampare i dati (solo registrazione)")
    s.set_defaults(func=cmd_sniff)

    r = sub.add_parser("replay", help="riproduce una registrazione con i tempi originali")
    r.add_argument("file")
    r.add_argument("-m", "--map", action="append", metavar="CANALE=PORTA[@config]",
                   help="rilancia i dati del canale su una porta reale (ripetibile); senza --map solo a schermo")
    r.add_argument("-s", "--speed", type=float, default=1.0)
    r.add_argument("--loop", action="store_true")
    r.add_argument("--max-gap", type=float, metavar="SEC", help="comprime le pause più lunghe di SEC")
    r.add_argument("--start", type=float, metavar="SEC", help="parte da questo istante")
    r.add_argument("--channels", help="elenco canali (nomi o indici, separati da virgola)")
    r.add_argument("--dirs", help="rx, tx o both")
    r.add_argument("-f", "--fmt", default="escaped")
    r.add_argument("-q", "--quiet", action="store_true")
    r.set_defaults(func=cmd_replay)

    e = sub.add_parser("export", help="esporta una registrazione")
    e.add_argument("file")
    e.add_argument("--to", required=True, choices=EXPORT_KINDS)
    e.add_argument("-o", "--output", required=True, help="file di uscita (per raw: prefisso)")
    e.add_argument("--channels")
    e.add_argument("--dirs", help="rx, tx o both")
    e.add_argument("-f", "--fmt", default=None, help="vista testuale per csv/txt (default escaped)")
    e.add_argument("--no-events", action="store_true")
    e.add_argument("--absolute", action="store_true", help="txt: timestamp UTC assoluti")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("info", help="header e riepilogo di una registrazione")
    i.add_argument("file")
    i.set_defaults(func=cmd_info)

    b = sub.add_parser("autobaud", help="stima euristica del baud rate (flussi di testo)")
    b.add_argument("url")
    b.add_argument("--candidates", help="elenco baud separati da virgola")
    b.add_argument("--framing", default="8N1")
    b.add_argument("--time", type=float, default=1.0, help="secondi di ascolto per baud")
    b.add_argument("--top", type=int, default=5)
    b.set_defaults(func=cmd_autobaud)
    d = sub.add_parser("decode", help="decodifica una registrazione con un decoder di protocollo")
    d.add_argument("file")
    d.add_argument("-p", "--proto", required=True, help="auto, modbus, modbus_ascii, nmea, at, ubx, mavlink, grbl, sbus (o un decoder caricato con --plugin)")
    d.add_argument("--plugin", action="append", help="file .py con decoder aggiuntivi (codice eseguito: solo file fidati)")
    d.add_argument("--channels", help="canali separati da virgola (nome o indice)")
    d.add_argument("--dirs", help="rx, tx o both")
    d.add_argument("--master", action="append", metavar="CANALE:DIR",
                   help="modbus/auto: flusso da cui parte il master (ripetibile); senza, il ruolo è dedotto")
    d.add_argument("--errors-only", action="store_true", help="mostra solo i messaggi non validi")
    d.add_argument("--fields", action="store_true", help="mostra anche i campi decodificati")
    d.add_argument("--json", action="store_true", help="una riga JSON per messaggio")
    d.add_argument("--fail-on-error", action="store_true", help="exit code 1 se ci sono messaggi non validi")
    d.set_defaults(func=cmd_decode)

    t = sub.add_parser("stats", help="statistiche sui messaggi decodificati (frequenza, jitter, latenze, errori)")
    t.add_argument("file")
    t.add_argument("-p", "--proto", default="auto", help="auto (default), modbus, nmea, at, ubx, mavlink, grbl, sbus...")
    t.add_argument("--plugin", action="append", help="file .py con decoder aggiuntivi")
    t.add_argument("--channels", help="canali separati da virgola (nome o indice)")
    t.add_argument("--dirs", help="rx, tx o both")
    t.add_argument("--master", action="append", metavar="CANALE:DIR", help="modbus/auto: flusso del master (ripetibile)")
    t.add_argument("-o", "--output", help="scrive su file .csv (separatore ;) o .json invece di stampare")
    t.add_argument("--json", action="store_true", help="stampa JSON")
    t.set_defaults(func=cmd_stats)

    sm = sub.add_parser("simulate-modbus", help="simula uno slave Modbus RTU su una porta (FC 1,2,3,4,5,6,15,16)")
    sm.add_argument("port", help="porta, es. COM5 oppure COM5@9600 8N1 oppure loop://")
    sm.add_argument("--config", default="9600 8N1", help="se la porta non porta la config (default 9600 8N1)")
    sm.add_argument("--addr", type=int, default=1, help="indirizzo slave 1..247 (default 1)")
    sm.add_argument("--regs", type=int, default=100, help="numero di registri/bobine per tabella (default 100)")
    sm.add_argument("--duration", type=float, default=0, help="secondi (0 = fino a Ctrl+C)")
    sm.add_argument("-v", "--verbose", action="store_true", help="stampa ogni richiesta/risposta in hex")
    sm.set_defaults(func=cmd_simulate_modbus)

    m = sub.add_parser("demo", help="crea una registrazione di esempio (Modbus, NMEA, AT) per provare la GUI")
    m.add_argument("-o", "--output", default="demo.ssn")
    m.add_argument("--seconds", type=int, default=20)
    m.set_defaults(func=cmd_demo)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return cmd_gui(argparse.Namespace())
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SsnError, serial.SerialException, ValueError, OSError) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
