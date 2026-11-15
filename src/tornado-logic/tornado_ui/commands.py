"""Registro comandi globali per la palette Ctrl+K (stesso modello di tornado_suite.core.registry)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Command:
    id: str
    titolo: str
    callback: Callable[[], None]
    categoria: str = ""
    scorciatoia: str = ""
    icona: str = ""


class CommandRegistry:
    def __init__(self) -> None:
        self._cmds: dict[str, Command] = {}

    def add(self, cmd: Command) -> None:
        self._cmds[cmd.id] = cmd

    def remove_prefix(self, prefix: str) -> None:
        for k in [k for k in self._cmds if k.startswith(prefix)]:
            del self._cmds[k]

    def all(self) -> list[Command]:
        return list(self._cmds.values())


COMMANDS = CommandRegistry()
