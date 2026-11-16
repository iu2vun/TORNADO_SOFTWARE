"""Enumerazione porte seriali e riconoscimento probabile della scheda da VID/PID USB."""
from __future__ import annotations

from typing import Callable, Mapping, Sequence

from .backend import Backend
from .models import DeviceInfo, PortInfo, RunContext

# (VID, PID) -> (descrizione, backend candidati in ordine di probabilità)
KNOWN_USB: dict[tuple[int, int], tuple[str, tuple[str, ...]]] = {
    (0x303A, 0x1001): ("ESP32-S3/C3/C6 USB-Serial/JTAG nativo", ("esptool",)),
    (0x2341, 0x0043): ("Arduino Uno", ("avrdude",)),
    (0x2341, 0x0001): ("Arduino Uno (vecchio ID)", ("avrdude",)),
    (0x2A03, 0x0043): ("Arduino Uno (Arduino.org)", ("avrdude",)),
    (0x2341, 0x0058): ("Arduino Nano Every", ("pymcuprog", "avrdude")),
}
# Bridge USB-seriale generici: la scheda dietro può essere ESP o AVR (cloni Arduino con CH340).
GENERIC_BRIDGES: dict[tuple[int, int], str] = {
    (0x10C4, 0xEA60): "Silicon Labs CP210x",
    (0x1A86, 0x7523): "WCH CH340",
    (0x1A86, 0x55D4): "WCH CH9102",
    (0x0403, 0x6001): "FTDI FT232",
    (0x0403, 0x6015): "FTDI FT231X",
}
GENERIC_CANDIDATES = ("esptool", "avrdude")


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as lp
    return [PortInfo(p.device, p.vid, p.pid, p.description or "", p.serial_number or "", p.manufacturer or "")
            for p in sorted(lp.comports(), key=lambda x: x.device)]


def candidates(port: PortInfo) -> list[str]:
    """Backend da provare per questa porta, dal più al meno probabile (può essere vuoto)."""
    vp = port.vidpid
    if vp in KNOWN_USB:
        return list(KNOWN_USB[vp][1])
    if vp in GENERIC_BRIDGES:
        return list(GENERIC_CANDIDATES)
    return []


def describe(port: PortInfo) -> str:
    vp = port.vidpid
    if vp in KNOWN_USB:
        return KNOWN_USB[vp][0]
    if vp in GENERIC_BRIDGES:
        return GENERIC_BRIDGES[vp]
    return port.description or "Porta seriale"


def scan(backends: Mapping[str, Backend], ctx: RunContext | None = None, probe: bool = True,
         ports: Sequence[PortInfo] | None = None,
         port_lister: Callable[[], list[PortInfo]] = list_ports) -> list[tuple[PortInfo, DeviceInfo | None, list[str]]]:
    """Elenca le porte e, se ``probe``, interroga i backend candidati registrati finché uno riconosce la scheda.

    Attenzione: il probe apre la porta e può resettare la scheda; va lanciato su richiesta dell'utente.
    """
    ctx = ctx or RunContext()
    out: list[tuple[PortInfo, DeviceInfo | None, list[str]]] = []
    for p in (ports if ports is not None else port_lister()):
        cands = [c for c in candidates(p) if c in backends]
        info = None
        if probe:
            for cid in cands:
                ctx.check_cancel()
                info = backends[cid].probe(p.device, ctx)
                if info:
                    break
        out.append((p, info, cands))
    return out
