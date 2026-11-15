"""Splash screen disegnata nel terminale all'avvio.

Banner ANSI a blocchi (font ansi_shadow) + riquadro con nome, versione e
callsign. Puro testo con escape SGR: lo mangia lo stesso engine che rende
tutto il resto, nessun percorso di rendering speciale.
"""

from __future__ import annotations

# Titolo a blocchi, impilato su due parole (TORNADO / TERM) per stare in 80
# colonne. Righe separate cosi' posso colorarle a gradiente continuo.
_TITLE = [
    "████████╗ ██████╗ ██████╗ ███╗   ██╗ █████╗ ██████╗  ██████╗ ",
    "╚══██╔══╝██╔═══██╗██╔══██╗████╗  ██║██╔══██╗██╔══██╗██╔═══██╗",
    "   ██║   ██║   ██║██████╔╝██╔██╗ ██║███████║██║  ██║██║   ██║",
    "   ██║   ██║   ██║██╔══██╗██║╚██╗██║██╔══██║██║  ██║██║   ██║",
    "   ██║   ╚██████╔╝██║  ██║██║ ╚████║██║  ██║██████╔╝╚██████╔╝",
    "   ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═════╝  ╚═════╝ ",
    "            ████████╗███████╗██████╗ ███╗   ███╗",
    "            ╚══██╔══╝██╔════╝██╔══██╗████╗ ████║",
    "               ██║   █████╗  ██████╔╝██╔████╔██║",
    "               ██║   ██╔══╝  ██╔══██╗██║╚██╔╝██║",
    "               ██║   ███████╗██║  ██║██║ ╚═╝ ██║",
    "               ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝",
]

# Gradiente ciano->blu sulle 12 righe del titolo (256 colori xterm).
_GRAD = [51, 45, 39, 39, 33, 33, 27, 27, 21, 21, 20, 20]

_RESET = "\x1b[0m"


def _visible_len(s: str) -> int:
    """Lunghezza in celle ignorando le sequenze SGR (\\x1b[...m)."""
    out = 0
    i = 0
    while i < len(s):
        if s[i] == "\x1b":
            m = s.find("m", i)
            if m != -1:
                i = m + 1
                continue
        out += 1
        i += 1
    return out


def render_splash(
    app_name: str,
    version: str,
    callsign: str | None = None,
    width: int = 80,
) -> bytes:
    """Compone la splash come sequenza di byte pronta per engine.feed()."""
    out: list[str] = []
    out.append("\x1b[2J\x1b[H")  # clear + home
    out.append("\r\n")

    for line, color in zip(_TITLE, _GRAD):
        out.append(f"  \x1b[1;38;5;{color}m{line}{_RESET}\r\n")

    out.append("\r\n")

    # Riquadro info. inner = celle interne tra le due barre verticali.
    inner = width - 6  # 2 margine sx + │ + spazio + ... + spazio + │
    subtitle = "terminale seriale VT100  ·  pyte + PySide6 + QtSerialPort"

    def box_line(left: str, right: str = "") -> str:
        vis = _visible_len(left) + _visible_len(right)
        pad = max(1, inner - vis)
        return (
            f"  \x1b[38;5;240m│{_RESET} "
            f"{left}{' ' * pad}{right}"
            f" \x1b[38;5;240m│{_RESET}\r\n"
        )

    bar = "─" * (inner + 2)
    top = f"  \x1b[38;5;240m┌{bar}┐{_RESET}\r\n"
    bot = f"  \x1b[38;5;240m└{bar}┘{_RESET}\r\n"

    out.append(top)
    out.append(box_line(f"\x1b[1;37m{app_name}{_RESET}", f"\x1b[38;5;244mversione {version}{_RESET}"))
    out.append(box_line(f"\x1b[38;5;250m{subtitle}{_RESET}"))
    if callsign:
        out.append(box_line("", f"\x1b[1;32m{callsign}{_RESET}"))
    out.append(bot)

    out.append("\r\n")
    out.append(
        "  \x1b[38;5;244mSeleziona porta e baud, poi \x1b[0m"
        "\x1b[1;37mConnetti\x1b[0m"
        "\x1b[38;5;244m.  Le porte si aggiornano da sole.\x1b[0m\r\n"
    )
    out.append(
        "  \x1b[38;5;240mF2 hex · F3 resize · rotella/Shift+PgUp scrollback · "
        "Ctrl+/- font\x1b[0m\r\n\r\n"
    )

    return "".join(out).encode("utf-8")
