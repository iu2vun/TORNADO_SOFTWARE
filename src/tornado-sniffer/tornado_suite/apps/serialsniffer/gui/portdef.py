"""Definizione persistente di porte e bridge configurati nella GUI."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from ..config import PortConfig


@dataclass
class PortDef:
    name: str
    url: str
    config: str = "115200 8N1"
    idle_gap_ms: Optional[float] = None  # None = auto, 0 = nessun raggruppamento
    reconnect: bool = True
    monitor_lines: bool = False

    @property
    def gap_s(self) -> Optional[float]:
        return None if self.idle_gap_ms is None else self.idle_gap_ms / 1000.0

    def port_config(self) -> PortConfig:
        return PortConfig.parse(self.config)

    def gap_text(self) -> str:
        if self.idle_gap_ms is None:
            return "auto"
        return "off" if self.idle_gap_ms == 0 else f"{self.idle_gap_ms:g} ms"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PortDef":
        gap = d.get("idle_gap_ms")
        return cls(
            name=str(d["name"]), url=str(d["url"]), config=str(d.get("config", "115200 8N1")),
            idle_gap_ms=None if gap is None else float(gap),
            reconnect=bool(d.get("reconnect", True)), monitor_lines=bool(d.get("monitor_lines", False)),
        )


@dataclass
class BridgeDef:
    a: str
    b: str
    bidirectional: bool = True

    def text(self) -> str:
        return f"{self.a} {'⇄' if self.bidirectional else '→'} {self.b}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BridgeDef":
        return cls(str(d["a"]), str(d["b"]), bool(d.get("bidirectional", True)))
