"""Backend esptool: ESP32 / ESP32-S2/S3/C3/C6 / ESP8266.

esptool viene lanciato come processo (``python -m esptool``): niente dipendenza dalla sua API interna, che cambia
tra le versioni 4.x e 5.x, e annullamento pulito terminando il processo. Si usano i nomi di comando con underscore
(``write_flash``…), accettati da entrambe le versioni (la 5.x segnala solo la deprecazione).
Nell'eseguibile PyInstaller ``sys.executable`` non è Python: si usa ``esptool`` nel PATH oppure quello di PlatformIO
(vedi ``find_command``), senza includere esptool nell'eseguibile.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .backend import Backend, run_streaming
from .models import Cancelled, DeviceInfo, FlashError, RunContext, WriteRequest

DEFAULT_BAUD = 460800

_CHIP = re.compile(r"Chip(?:\s+is|\s+type:)\s+(?P<chip>ESP\d+(?:-[A-Za-z0-9]+)*)(?P<rest>[^\n]*)")
_REV = re.compile(r"revision\s+(v?[\d.]+)", re.I)
_MAC = re.compile(r"MAC:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
_FLASH = re.compile(r"(?:Detected flash size|Flash size):\s*(\S+)", re.I)
_XTAL = re.compile(r"Crystal(?:\s+is|\s+frequency:)\s*(\d+)\s?MHz", re.I)
_FEAT = re.compile(r"Features:\s*(.+)")
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
_FAMILY = re.compile(r"^(ESP32-(?:S2|S3|C2|C3|C5|C6|H2|P4)|ESP32|ESP8266)", re.I)


def family_of(chip: str) -> str:
    """'ESP32-D0WD-V3' -> 'ESP32', 'ESP32-S3' -> 'ESP32-S3' (le varianti D0WD/PICO restano ESP32 classico)."""
    m = _FAMILY.match(chip)
    return m.group(1).upper() if m else chip.upper()


def parse_chip_info(text: str) -> dict[str, str]:
    """Estrae chip/revisione/MAC/flash/cristallo/feature dall'output di ``flash_id`` (esptool 4.x e 5.x)."""
    info: dict[str, str] = {}
    m = _CHIP.search(text)
    if m:
        info["chip"] = m.group("chip")
        r = _REV.search(m.group("rest"))
        if r:
            info["revision"] = r.group(1)
    for key, rx in (("mac", _MAC), ("flash_size", _FLASH), ("crystal_mhz", _XTAL), ("features", _FEAT)):
        mm = rx.search(text)
        if mm:
            info[key] = mm.group(1).strip()
    return info


def parse_progress(line: str) -> float | None:
    if "Wrote" in line or "Hash" in line:
        return None
    m = _PCT.search(line)
    if not m:
        return None
    v = float(m.group(1))
    return v if 0 <= v <= 100 else None


_PIO = Path.home() / ".platformio"
_cache: list[str] | None = None


def _penv_pythons() -> list[Path]:
    """Python dell'ambiente PlatformIO (lo crea PlatformIO stesso; l'IDE ne ha comunque bisogno per compilare)."""
    root = _PIO / "penv"
    return [p for p in (root / "Scripts" / "python.exe", root / "bin" / "python") if p.is_file()]


def _runs(cmd: Sequence[str]) -> bool:
    """True se ``cmd version`` parte e termina bene (verifica che esptool e le sue dipendenze ci siano davvero)."""
    try:
        r = subprocess.run([*cmd, "version"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_command(refresh: bool = False) -> list[str]:
    """Come lanciare esptool, in ordine: modulo importabile (esecuzione da sorgente/venv) → ``esptool`` nel PATH →
    Python di PlatformIO con il modulo ``esptool`` → script ``tool-esptoolpy`` di PlatformIO con il suo Python.
    Nell'eseguibile PyInstaller ``sys.executable`` non è Python, quindi il primo ramo è saltato. Il risultato è in cache."""
    global _cache
    if _cache and not refresh:
        return list(_cache)
    found: list[str] | None = None
    if not getattr(sys, "frozen", False) and importlib.util.find_spec("esptool"):
        found = [sys.executable, "-m", "esptool"]
    if not found:
        for name in ("esptool", "esptool.py"):
            p = shutil.which(name)
            if p:
                found = [p]
                break
    if not found:
        for py in _penv_pythons():
            if _runs([str(py), "-m", "esptool"]):
                found = [str(py), "-m", "esptool"]
                break
    if not found:
        for tool in sorted((_PIO / "packages").glob("tool-esptoolpy*")):
            script = tool / "esptool.py"
            for py in _penv_pythons():
                if script.is_file() and _runs([str(py), str(script)]):
                    found = [str(py), str(script)]
                    break
            if found:
                break
    if not found:
        raise FlashError("esptool non trovato: installarlo con 'pip install esptool', oppure compilare una volta un "
                         "progetto ESP32 nell'IDE (PlatformIO lo scarica)")
    _cache = found
    return list(found)


class EsptoolBackend(Backend):
    id = "esptool"
    name = "ESP32 / ESP8266 (esptool)"

    def __init__(self, command: Sequence[str] | None = None) -> None:
        self._command = list(command) if command else None

    def command(self) -> list[str]:
        return list(self._command) if self._command else find_command()

    def build_argv(self, port: str, op_args: Sequence[str], baud: int = 115200, chip: str = "auto") -> list[str]:
        return [*self.command(), "--chip", chip, "--port", port, "--baud", str(baud), *op_args]

    # ------------------------------------------------------------------ operazioni
    def probe(self, port: str, ctx: RunContext) -> DeviceInfo | None:
        argv = self.build_argv(port, ["flash_id"])   # fuori dal try: esptool mancante deve vedersi
        try:
            lines = run_streaming(argv, ctx)
        except Cancelled:
            raise
        except FlashError:
            ctx.log(f"[esptool] nessuna risposta su {port}")
            return None
        info = parse_chip_info("\n".join(lines))
        if "chip" not in info:
            return None
        return DeviceInfo(port=port, backend=self.id, chip=info["chip"], family=family_of(info["chip"]), details=info)

    def write(self, req: WriteRequest, ctx: RunContext) -> None:
        if not req.parts:
            raise FlashError("Nessun file da scrivere")
        for p in req.parts:
            if not Path(p.path).is_file():
                raise FlashError(f"File non trovato: {p.path}")
        if len({p.offset for p in req.parts}) != len(req.parts):
            raise FlashError("Due file hanno lo stesso offset")
        args = ["write_flash"]
        if req.erase_all:
            args.append("--erase-all")
        if not req.verify:
            args.append("--no-verify")
        for p in sorted(req.parts, key=lambda x: x.offset):
            args += [f"0x{p.offset:x}", str(p.path)]
        total, state = len(req.parts), {"done": 0, "last": -1}

        def on_line(line: str) -> None:
            if line.startswith("Wrote "):
                state["done"] += 1
                pct = (state["done"] / total) * 100
            else:
                v = parse_progress(line)
                if v is None:
                    return
                pct = (state["done"] + v / 100) / total * 100
            pct_i = min(100, int(pct))
            if pct_i != state["last"]:
                state["last"] = pct_i
                ctx.progress(pct_i)

        run_streaming(self.build_argv(req.port, args, req.baud or DEFAULT_BAUD, req.chip), ctx, on_line)
        ctx.progress(100)

    def read(self, port: str, dest: Path, ctx: RunContext, baud: int = 0, chip: str = "auto") -> None:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        last = {"p": -1}

        def on_line(line: str) -> None:
            v = parse_progress(line)
            if v is not None and int(v) != last["p"]:
                last["p"] = int(v)
                ctx.progress(int(v))

        run_streaming(self.build_argv(port, ["read_flash", "0", "ALL", str(dest)], baud or DEFAULT_BAUD, chip), ctx, on_line)
        if not dest.is_file() or dest.stat().st_size == 0:
            raise FlashError("Lettura terminata ma il file di backup è vuoto")
        ctx.progress(100)

    def erase(self, port: str, ctx: RunContext, baud: int = 0, chip: str = "auto") -> None:
        run_streaming(self.build_argv(port, ["erase_flash"], baud or DEFAULT_BAUD, chip), ctx)
        ctx.progress(100)
