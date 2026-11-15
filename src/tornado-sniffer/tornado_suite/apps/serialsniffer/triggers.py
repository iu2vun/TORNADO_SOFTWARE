"""Trigger: regole «quando compare questo pattern, fai questa azione». Valutati sul traffico live."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .chunk import Chunk
from .search import PatternError, compile_pattern

ACTIONS = ("mark", "notify", "start_rec", "stop_rec", "stop")
ACTION_LABELS = {
    "mark": "Inserisci marker", "notify": "Notifica (barra di stato)", "start_rec": "Avvia registrazione",
    "stop_rec": "Ferma registrazione", "stop": "Ferma acquisizione",
}


@dataclass
class TriggerRule:
    name: str = "trigger"
    pattern: str = ""
    mode: str = "text"           # text | hex | regex
    ignore_case: bool = False
    channel: Optional[int] = None  # None = qualsiasi
    direction: Optional[int] = None
    action: str = "mark"
    cooldown_s: float = 1.0
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TriggerRule":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        r = cls(**known)
        if r.action not in ACTIONS:
            r.action = "mark"
        return r


@dataclass
class Fired:
    rule: TriggerRule
    chunk: Chunk


@dataclass
class TriggerEngine:
    rules: list[TriggerRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._compiled: dict[int, object] = {}
        self._last: dict[int, int] = {}
        self.errors: dict[str, str] = {}

    def set_rules(self, rules: list[TriggerRule]) -> None:
        self.rules = list(rules)
        self._compiled.clear()
        self._last.clear()
        self.errors.clear()
        for i, r in enumerate(self.rules):
            try:
                self._compiled[i] = compile_pattern(r.pattern, r.mode, r.ignore_case)
            except PatternError as exc:
                self.errors[r.name] = str(exc)

    def feed(self, chunks: list[Chunk]) -> list[Fired]:
        out: list[Fired] = []
        for c in chunks:
            if c.is_event or not c.data:
                continue
            for i, r in enumerate(self.rules):
                pat = self._compiled.get(i)
                if pat is None or not r.enabled:
                    continue
                if r.channel is not None and c.channel != r.channel:
                    continue
                if r.direction is not None and c.direction != r.direction:
                    continue
                if not pat.search(c.data):
                    continue
                last = self._last.get(i)
                if last is not None and c.ts_ns - last < r.cooldown_s * 1e9:
                    continue
                self._last[i] = c.ts_ns
                out.append(Fired(r, c))
        return out
