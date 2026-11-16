"""Backend avrdude: AVR (ATmega/ATtiny) via bootloader Arduino, ISP (ArduinoISP, USBasp, AVRISP mkII) e fuse.

Valido per gli ATmega con fuse standard (lfuse/hfuse/efuse/lock, SPIEN in hfuse bit 5): ATmega328P, ATmega162, ecc.
Il Nano Every (ATmega4809, UPDI) NON passa da qui: avrà un backend pymcuprog.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .backend import Backend, run_streaming
from .models import Cancelled, DeviceInfo, FlashError, RunContext, WriteRequest


@dataclass(frozen=True)
class Programmer:
    id: str
    label: str
    avrdude_id: str
    serial: bool = True          # richiede -P <porta COM> e -b
    baud: int = 0
    bootloader: bool = False     # via bootloader: niente chip erase, niente fuse
    fixed_port: str = ""         # es. "usb" per AVRISP mkII


PROGRAMMERS: dict[str, Programmer] = {p.id: p for p in (
    Programmer("arduino", "Arduino Uno/Nano (bootloader Optiboot)", "arduino", True, 115200, bootloader=True),
    Programmer("arduinoisp", "Arduino come ISP (sketch ArduinoISP)", "stk500v1", True, 19200),
    Programmer("usbasp", "USBasp", "usbasp", serial=False),
    Programmer("avrispmkii", "Atmel AVRISP mkII", "avrispmkII", serial=False, fixed_port="usb"),
)}

# Segnatura -> (id parte avrdude, nome). Usata solo per etichettare il chip trovato dal probe.
SIGNATURES: dict[int, tuple[str, str]] = {
    0x1E950F: ("m328p", "ATmega328P"), 0x1E9514: ("m328", "ATmega328"), 0x1E9406: ("m168", "ATmega168"),
    0x1E940B: ("m168p", "ATmega168P"), 0x1E930A: ("m88", "ATmega88"), 0x1E9307: ("m8", "ATmega8"),
    0x1E9403: ("m16", "ATmega16"), 0x1E9404: ("m162", "ATmega162"), 0x1E9502: ("m32", "ATmega32"),
    0x1E9801: ("m2560", "ATmega2560"), 0x1E9587: ("m32u4", "ATmega32U4"),
}
FUSE_NAMES = ("lfuse", "hfuse", "efuse", "lock")
_FUSES_BY_PART: dict[str, tuple[str, ...]] = {"m8": ("lfuse", "hfuse", "lock"), "m16": ("lfuse", "hfuse", "lock"),
                                              "m32": ("lfuse", "hfuse", "lock")}
_SIG = re.compile(r"Device signature = (0x[0-9a-fA-F]{6})")
_HEXVAL = re.compile(r"^0x[0-9a-fA-F]{1,2}$")
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
_HINTS = (
    ("not in sync", "Nessuna risposta dal bootloader: porta o baud sbagliati, bootloader assente, scheda non resettata "
                    "(con ArduinoISP serve programmer 'arduinoisp' a 19200)."),
    ("initialization failed", "Il chip non risponde in ISP: controllare cablaggio (RESET/MOSI/MISO/SCK/GND), alimentazione "
                              "e livelli logici; con clock lento verificare la velocità ISP."),
    ("Invalid device signature", "La segnatura letta non corrisponde al chip selezionato: controllare il tipo di chip."),
    ("can't open device", "Porta non apribile: occupata da un monitor seriale o nome sbagliato."),
    ("could not find USB device", "Programmatore USB non trovato: driver (Zadig/libusb) o cavo."),
)


def find_command() -> list[str]:
    p = shutil.which("avrdude")
    if p:
        return [p]
    for root in (Path.home() / ".platformio" / "packages").glob("tool-avrdude*"):
        for name in ("avrdude.exe", "avrdude"):
            exe = root / name
            if exe.is_file():
                conf = root / "avrdude.conf"
                return [str(exe), *(["-C", str(conf)] if conf.is_file() else [])]
    raise FlashError("avrdude non trovato: installarlo, oppure usare quello di PlatformIO (installare un progetto AVR una volta)")


def _fmt_for(path: Path) -> str:
    return {".hex": "i", ".ihx": "i", ".elf": "e", ".bin": "r"}.get(path.suffix.lower(), "a")


def parse_signature(text: str) -> int | None:
    m = _SIG.search(text)
    return int(m.group(1), 16) if m else None


class _Progress:
    """avrdude stampa due barre per scrittura+verifica ('Writing' poi 'Reading'): le mappa su 0-50 e 50-100."""

    def __init__(self, two_phases: bool) -> None:
        self.two = two_phases

    def feed(self, line: str) -> int | None:
        s = line.lstrip()
        m = _PCT.search(s)
        if not m or not s.startswith(("Writing", "Reading")):
            return None
        v = min(100.0, float(m.group(1)))
        if not self.two:
            return int(v)
        return int(v / 2) if s.startswith("Writing") else int(50 + v / 2)


class AvrdudeBackend(Backend):
    id = "avrdude"
    name = "AVR (avrdude)"

    def __init__(self, programmer: str = "arduino", part: str = "", command: Sequence[str] | None = None) -> None:
        if programmer not in PROGRAMMERS:
            raise ValueError(f"Programmatore sconosciuto: {programmer}")
        self.programmer = PROGRAMMERS[programmer]
        self.part = part
        self._command = list(command) if command else None

    def command(self) -> list[str]:
        return list(self._command) if self._command else find_command()

    # ------------------------------------------------------------------ argv
    def build_argv(self, op_args: Sequence[str], port: str = "", baud: int = 0, part: str = "") -> list[str]:
        pr = self.programmer
        part = part or self.part
        if not part:
            raise FlashError("Chip non specificato (es. 'm328p', 'm162')")
        conn = ["-c", pr.avrdude_id]
        if pr.serial:
            if not port:
                raise FlashError("Porta seriale non specificata")
            conn += ["-P", port, "-b", str(baud or pr.baud)]
        elif pr.fixed_port:
            conn += ["-P", pr.fixed_port]
        return [*self.command(), "-p", part, *conn, *op_args]

    def _run(self, argv: Sequence[str], ctx: RunContext, on_line=None) -> list[str]:
        try:
            return run_streaming(argv, ctx, on_line)
        except Cancelled:
            raise
        except FlashError as e:
            for key, hint in _HINTS:
                if key.lower() in str(e).lower():
                    raise FlashError(f"{e}\n→ {hint}") from None
            raise

    # ------------------------------------------------------------------ operazioni
    def probe(self, port: str, ctx: RunContext) -> DeviceInfo | None:
        """Legge la segnatura (sola lettura; ``-F`` serve solo a non fermarsi se la parte ipotizzata è diversa)."""
        argv = self.build_argv(["-F", "-n"], port, part=self.part or "m328p")   # fuori dal try: avrdude mancante deve vedersi
        try:
            lines = run_streaming(argv, ctx)
        except Cancelled:
            raise
        except FlashError:
            ctx.log(f"[avrdude] nessuna risposta su {port}")
            return None
        sig = parse_signature("\n".join(lines))
        if sig is None or sig in (0x000000, 0xFFFFFF):
            return None
        part_id, name = SIGNATURES.get(sig, ("", f"AVR sconosciuto (0x{sig:06x})"))
        return DeviceInfo(port=port, backend=self.id, chip=name, family="AVR",
                          details={"signature": f"0x{sig:06x}", "part": part_id, "programmer": self.programmer.id})

    def write(self, req: WriteRequest, ctx: RunContext) -> None:
        if len(req.parts) != 1 or req.parts[0].offset != 0:
            raise FlashError("avrdude scrive un solo file di flash a offset 0")
        path = Path(req.parts[0].path)
        if not path.is_file():
            raise FlashError(f"File non trovato: {path}")
        pr = self.programmer
        args: list[str] = []
        if req.erase_all:
            if pr.bootloader:
                raise FlashError("Chip erase non disponibile con il bootloader")
            args.append("-e")
        if pr.bootloader:
            args.append("-D")           # il bootloader non può fare chip erase
        if not req.verify:
            args.append("-V")
        args += ["-U", f"flash:w:{path}:{_fmt_for(path)}"]
        part = req.chip if req.chip and req.chip != "auto" else ""
        prog = _Progress(two_phases=req.verify)
        last = {"p": -1}

        def on_line(line: str) -> None:
            v = prog.feed(line)
            if v is not None and v != last["p"]:
                last["p"] = v
                ctx.progress(v)

        self._run(self.build_argv(args, req.port, req.baud, part), ctx, on_line)
        ctx.progress(100)

    def read(self, port: str, dest: Path, ctx: RunContext, baud: int = 0, chip: str = "auto") -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fmt = "i" if dest.suffix.lower() in (".hex", ".ihx") else "r"
        prog = _Progress(two_phases=False)
        last = {"p": -1}

        def on_line(line: str) -> None:
            v = prog.feed(line)
            if v is not None and v != last["p"]:
                last["p"] = v
                ctx.progress(v)

        part = chip if chip and chip != "auto" else ""
        self._run(self.build_argv(["-U", f"flash:r:{dest}:{fmt}"], port, baud, part), ctx, on_line)
        if not dest.is_file() or dest.stat().st_size == 0:
            raise FlashError("Lettura terminata ma il file di backup è vuoto (lock bit attivi?)")
        ctx.progress(100)

    def erase(self, port: str, ctx: RunContext, baud: int = 0, chip: str = "auto") -> None:
        if self.programmer.bootloader:
            raise FlashError("Chip erase non disponibile con il bootloader")
        part = chip if chip and chip != "auto" else ""
        self._run(self.build_argv(["-e"], port, baud, part), ctx)
        ctx.progress(100)

    # ------------------------------------------------------------------ fuse
    def read_fuses(self, port: str, ctx: RunContext, part: str = "") -> dict[str, int]:
        if self.programmer.bootloader:
            raise FlashError("I fuse non sono accessibili via bootloader: serve un programmatore ISP")
        names = _FUSES_BY_PART.get(part or self.part, FUSE_NAMES)
        args = [x for n in names for x in ("-U", f"{n}:r:-:h")]
        lines = self._run(self.build_argv(args, port, part=part), ctx)
        vals = [int(x, 16) for x in (ln.strip() for ln in lines) if _HEXVAL.match(x)]
        if len(vals) != len(names):
            raise FlashError(f"Lettura fuse incompleta: attesi {len(names)} valori, trovati {len(vals)}")
        return dict(zip(names, vals))

    def write_fuse(self, port: str, name: str, value: int, ctx: RunContext, part: str = "", force: bool = False) -> None:
        """Scrive un fuse (avrdude verifica la scrittura). Rifiuta senza ``force`` le scritture che possono bloccare il chip:
        hfuse con SPIEN disattivato (bit 5 = 1 → niente più ISP) e qualunque lock bit (si azzerano solo con chip erase)."""
        if self.programmer.bootloader:
            raise FlashError("I fuse non sono accessibili via bootloader: serve un programmatore ISP")
        if name not in FUSE_NAMES:
            raise FlashError(f"Fuse sconosciuto: {name}")
        if not 0 <= value <= 0xFF:
            raise FlashError("Valore fuse fuori intervallo 0..255")
        if not force:
            if name == "hfuse" and value & 0x20:
                raise FlashError("hfuse con SPIEN disattivato: il chip non sarebbe più programmabile via ISP (serve force)")
            if name == "lock":
                raise FlashError("La scrittura dei lock bit protegge la flash e si annulla solo con chip erase (serve force)")
        self._run(self.build_argv(["-U", f"{name}:w:0x{value:02x}:m"], port, part=part), ctx)
