"""Parametri UART (baud, bit, parità, stop, flow control) e parsing delle specifiche porta."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import serial

FLOW_MODES = ("none", "rtscts", "xonxoff", "dsrdtr")

_PARITY = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
    "M": serial.PARITY_MARK,
    "S": serial.PARITY_SPACE,
}
_STOP = {
    1.0: serial.STOPBITS_ONE,
    1.5: serial.STOPBITS_ONE_POINT_FIVE,
    2.0: serial.STOPBITS_TWO,
}

_SEP = r"[ ,/:;-]\s*"
_CONFIG_RE = re.compile(
    rf"^\s*(\d+)"
    rf"(?:{_SEP}([5-8])\s*([NEOMS])\s*(1\.5|1|2))?"
    rf"(?:{_SEP}(none|rtscts|xonxoff|dsrdtr))?\s*$",
    re.IGNORECASE,
)
_NAME_RE = re.compile(r"^([\w.-]+)=(.+)$")


@dataclass(frozen=True)
class PortConfig:
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0
    flow: str = "none"

    def __post_init__(self) -> None:
        parity = str(self.parity).upper()
        flow = str(self.flow).lower()
        if self.baudrate <= 0:
            raise ValueError(f"baud non valido: {self.baudrate}")
        if self.bytesize not in (5, 6, 7, 8):
            raise ValueError(f"bit di dato non validi: {self.bytesize}")
        if parity not in _PARITY:
            raise ValueError(f"parità non valida: {self.parity!r} (N/E/O/M/S)")
        if float(self.stopbits) not in _STOP:
            raise ValueError(f"bit di stop non validi: {self.stopbits} (1/1.5/2)")
        if flow not in FLOW_MODES:
            raise ValueError(f"flow control non valido: {self.flow!r} {FLOW_MODES}")
        object.__setattr__(self, "parity", parity)
        object.__setattr__(self, "flow", flow)
        object.__setattr__(self, "stopbits", float(self.stopbits))

    @classmethod
    def parse(cls, text: str) -> "PortConfig":
        """'115200', '115200 8N1', '9600,7E2', '57600-8O1.5 rtscts'."""
        m = _CONFIG_RE.match(text)
        if not m:
            raise ValueError(f"configurazione seriale non valida: {text!r}")
        baud, bits, par, stop, flow = m.groups()
        return cls(
            baudrate=int(baud),
            bytesize=int(bits) if bits else 8,
            parity=par or "N",
            stopbits=float(stop) if stop else 1.0,
            flow=flow or "none",
        )

    def serial_kwargs(self) -> dict:
        return dict(
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=_PARITY[self.parity],
            stopbits=_STOP[self.stopbits],
            xonxoff=self.flow == "xonxoff",
            rtscts=self.flow == "rtscts",
            dsrdtr=self.flow == "dsrdtr",
        )

    def __str__(self) -> str:
        stop = "1.5" if self.stopbits == 1.5 else str(int(self.stopbits))
        text = f"{self.baudrate} {self.bytesize}{self.parity}{stop}"
        return text if self.flow == "none" else f"{text} {self.flow}"


def parse_port_spec(
    spec: str, default: Optional[PortConfig] = None
) -> tuple[Optional[str], str, Optional[PortConfig]]:
    """'[nome=]URL[@config]' -> (nome|None, url, config|default).

    URL: COM3, /dev/ttyUSB0, loop://, socket://host:porta, rfc2217://host:porta, ...
    """
    name = None
    rest = spec.strip()
    m = _NAME_RE.match(rest)
    if m:
        name, rest = m.group(1), m.group(2)
    cfg = default
    url, sep, tail = rest.rpartition("@")
    if sep:
        try:
            cfg = PortConfig.parse(tail)
        except ValueError:
            url = rest  # la '@' fa parte dell'URL
    else:
        url = rest
    if not url:
        raise ValueError(f"specifica porta non valida: {spec!r}")
    return name, url, cfg
