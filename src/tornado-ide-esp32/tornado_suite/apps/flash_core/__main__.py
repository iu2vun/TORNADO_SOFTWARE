"""Riga di comando di flash_core:  python -m tornado_suite.apps.flash_core <comando> ...

  doctor                       diagnostica completa (da incollare in caso di problemi)
  ports [--no-probe]           elenca le porte e riconosce le schede
  probe  -p COM5               identifica il chip
  write  -p COM5 fw.bin@0x10000 [altri@off] [--erase-all] [--no-verify]     (esptool: offset obbligatorio)
  write  -p COM3 fw.hex --backend avrdude --programmer arduino --part m328p
  read   -p COM5 backup.bin    backup completo della flash
  erase  -p COM5               cancella il chip (chiede conferma)
  fuses  -p COM3 --backend avrdude --programmer arduinoisp --part m162 [--set hfuse=0x99] [--force]
Tutto l'output è copiato anche in <cartella temporanea>/flash_core.log.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from . import AvrdudeBackend, PROGRAMMERS, default_backends
from .avrdude_backend import find_command as avr_find
from .detect import candidates, describe, list_ports, scan
from .esptool_backend import find_command as esp_find
from .models import Cancelled, FlashError, ImagePart, RunContext, WriteRequest

LOG = Path(tempfile.gettempdir()) / "flash_core.log"


def parse_target(spec: str) -> ImagePart:
    """'file.bin@0x10000' -> ImagePart; senza '@' l'offset è 0."""
    path, sep, off = spec.rpartition("@")
    if not sep or os.path.exists(spec):
        path, off = spec, "0"
    try:
        return ImagePart(int(off, 0), Path(path))
    except ValueError:
        raise FlashError(f"Offset non valido in '{spec}' (esempio: fw.bin@0x10000)") from None


def _make_ctx(out: Callable[[str], None]) -> RunContext:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOG, "a", encoding="utf-8")
    state = {"last": -1}

    def log(s: str) -> None:
        f.write(s + "\n"); f.flush()
        out("  " + s)

    def progress(p: int) -> None:
        if p != state["last"]:
            state["last"] = p
            f.write(f"[{p}%]\n")
            sys.stdout.write(f"\r  >> {p:3d}%{'' if p < 100 else chr(10)}"); sys.stdout.flush()

    return RunContext(log=log, progress=progress, cancel=threading.Event())


def _backend(args, backends):
    if args.backend == "avrdude":
        return AvrdudeBackend(args.programmer, args.part)
    if args.backend == "esptool":
        return backends["esptool"]
    if args.port:
        try:
            for p in list_ports():
                if p.device.lower() == args.port.lower():
                    c = candidates(p)
                    if c:
                        return AvrdudeBackend(args.programmer, args.part) if c[0] == "avrdude" else backends[c[0]]
        except Exception:
            pass
    return backends["esptool"]


def _confirm(msg: str, yes: bool, ask: Callable[[str], str]) -> None:
    if yes:
        return
    if ask(f"{msg} Digitare SI per continuare: ").strip().upper() != "SI":
        raise Cancelled("Annullato dall'utente")


def doctor(out: Callable[[str], None]) -> None:
    out(f"Python {sys.version.split()[0]} su {platform.platform()}  (frozen={getattr(sys, 'frozen', False)})")
    try:
        import serial
        out(f"pyserial {serial.__version__}")
    except Exception as e:
        out(f"pyserial: NON disponibile ({e})")
    out(f"esptool (modulo): {'presente' if importlib.util.find_spec('esptool') else 'assente'}")
    for label, finder, ver in (("esptool", esp_find, ["version"]), ("avrdude", avr_find, ["-?"])):
        try:
            cmd = finder()
            out(f"{label}: {' '.join(cmd)}")
            r = subprocess.run([*cmd, *ver], capture_output=True, text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
            first = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()][:2]
            out(f"  -> rc={r.returncode} {' | '.join(first)}")
        except Exception as e:
            out(f"{label}: NON utilizzabile ({e})")
    try:
        ports = list_ports()
        out(f"Porte seriali: {len(ports)}")
        for p in ports:
            vp = f"{p.vid:04X}:{p.pid:04X}" if p.vidpid else "----:----"
            out(f"  {p.device:<8} {vp}  {describe(p)}  [{p.description}]  candidati={candidates(p)}")
    except Exception as e:
        out(f"Elenco porte fallito: {e}")
    out(f"Log: {LOG}")


def main(argv: list[str] | None = None, backends=None, out: Callable[[str], None] = print,
         ask: Callable[[str], str] = input) -> int:
    ap = argparse.ArgumentParser(prog="flash_core", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("doctor", "ports", "probe", "write", "read", "erase", "fuses"))
    ap.add_argument("files", nargs="*", help="write: file[@offset]; read: file di destinazione")
    ap.add_argument("-p", "--port", default="")
    ap.add_argument("-b", "--backend", choices=("auto", "esptool", "avrdude"), default="auto")
    ap.add_argument("--programmer", choices=sorted(PROGRAMMERS), default="arduino")
    ap.add_argument("--part", default="", help="chip avrdude, es. m328p, m162")
    ap.add_argument("--baud", type=int, default=0)
    ap.add_argument("--erase-all", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--set", metavar="FUSE=0xNN", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("-y", "--yes", action="store_true")
    a = ap.parse_intermixed_args(argv)
    backends = backends or default_backends()
    try:
        if a.cmd == "doctor":
            doctor(out)
            return 0
        ctx = _make_ctx(out)
        if a.cmd == "ports":
            for p, info, cands in scan(backends, ctx, probe=not a.no_probe):
                what = f"{info.chip} ({info.backend})" if info else "-"
                out(f"{p.device:<8} {describe(p):<40} candidati={cands}  ->  {what}")
            return 0
        if not a.port and not (a.backend == "avrdude" and not PROGRAMMERS[a.programmer].serial):
            ap.error("serve --port")
        be = _backend(a, backends)
        if a.cmd == "probe":
            info = be.probe(a.port, ctx)
            out(f"{info.chip} [{info.family}] {info.details}" if info else "Nessun dispositivo riconosciuto")
            return 0 if info else 1
        if a.cmd == "write":
            if not a.files:
                ap.error("write: indicare almeno un file")
            parts = [parse_target(f) for f in a.files]
            if be.id == "esptool" and any("@" not in f for f in a.files):
                raise FlashError("esptool: specificare l'offset di ogni file (es. fw.bin@0x10000, immagine completa @0x0)")
            if a.erase_all:
                _confirm("Verrà cancellata TUTTA la flash.", a.yes, ask)
            be.write(WriteRequest(a.port, parts, a.baud, a.erase_all, not a.no_verify, a.part or "auto"), ctx)
            out("Scrittura completata")
        elif a.cmd == "read":
            if len(a.files) != 1:
                ap.error("read: indicare un solo file di destinazione")
            be.read(a.port, Path(a.files[0]), ctx, a.baud, a.part or "auto")
            out(f"Backup salvato in {a.files[0]}")
        elif a.cmd == "erase":
            _confirm("Verrà cancellata TUTTA la flash del chip.", a.yes, ask)
            be.erase(a.port, ctx, a.baud, a.part or "auto")
            out("Cancellazione completata")
        elif a.cmd == "fuses":
            if not hasattr(be, "read_fuses"):
                raise FlashError("I fuse sono gestiti solo dal backend avrdude")
            for k, v in be.read_fuses(a.port, ctx).items():
                out(f"{k:<6} = 0x{v:02x}")
            if a.set:
                name, _, val = a.set.partition("=")
                _confirm(f"Verrà scritto {name} = {val}.", a.yes, ask)
                be.write_fuse(a.port, name.strip(), int(val, 0), ctx, force=a.force)
                out("Fuse scritto; riletti:")
                for k, v in be.read_fuses(a.port, ctx).items():
                    out(f"{k:<6} = 0x{v:02x}")
        return 0
    except Cancelled as e:
        out(f"ANNULLATO: {e}")
        return 130
    except FlashError as e:
        out(f"ERRORE: {e}")
        return 1
    except ValueError as e:
        out(f"ERRORE: valore non valido ({e})")
        return 2


if __name__ == "__main__":
    sys.exit(main())
