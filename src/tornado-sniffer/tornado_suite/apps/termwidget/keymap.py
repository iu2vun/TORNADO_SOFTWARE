"""Mappa da eventi tastiera Qt a sequenze byte VT100/xterm.

Separata dal widget perche' e' pura logica testabile: (key, mods, text,
app_cursor, app_keypad) -> bytes. Nessuna dipendenza da rendering o engine.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

ESC = b"\x1b"

# Tasti navigazione/edit: (normale, applicativo cursore).
# Freccie e Home/End cambiano forma con DECCKM; il resto e' invariante ma
# tabellato uniformemente per un solo punto di lookup.
_CURSOR: dict[int, tuple[bytes, bytes]] = {
    Qt.Key_Up:    (b"\x1b[A", b"\x1bOA"),
    Qt.Key_Down:  (b"\x1b[B", b"\x1bOB"),
    Qt.Key_Right: (b"\x1b[C", b"\x1bOC"),
    Qt.Key_Left:  (b"\x1b[D", b"\x1bOD"),
    Qt.Key_Home:  (b"\x1b[H", b"\x1bOH"),
    Qt.Key_End:   (b"\x1b[F", b"\x1bOF"),
}

# Tasti con sequenza CSI ... ~ (invarianti rispetto a DECCKM).
_TILDE: dict[int, bytes] = {
    Qt.Key_Insert:   b"\x1b[2~",
    Qt.Key_Delete:   b"\x1b[3~",
    Qt.Key_PageUp:   b"\x1b[5~",
    Qt.Key_PageDown: b"\x1b[6~",
}

# Function keys, schema xterm.
_FUNC: dict[int, bytes] = {
    Qt.Key_F1:  b"\x1bOP",  Qt.Key_F2:  b"\x1bOQ",
    Qt.Key_F3:  b"\x1bOR",  Qt.Key_F4:  b"\x1bOS",
    Qt.Key_F5:  b"\x1b[15~", Qt.Key_F6:  b"\x1b[17~",
    Qt.Key_F7:  b"\x1b[18~", Qt.Key_F8:  b"\x1b[19~",
    Qt.Key_F9:  b"\x1b[20~", Qt.Key_F10: b"\x1b[21~",
    Qt.Key_F11: b"\x1b[23~", Qt.Key_F12: b"\x1b[24~",
}

# Tasti semplici a byte fisso. Invio e' gestito a parte (line ending).
_SIMPLE: dict[int, bytes] = {
    Qt.Key_Backspace: b"\x7f",  # DEL: convenzione Unix; vedi nota nel widget
    Qt.Key_Tab:       b"\t",
    Qt.Key_Escape:    b"\x1b",
}


def _ctrl_code(key: int, text: str) -> bytes | None:
    """Ctrl+lettera/simbolo -> control code C0."""
    if Qt.Key_A <= key <= Qt.Key_Z:
        return bytes([key - Qt.Key_A + 1])  # Ctrl+A=0x01 .. Ctrl+Z=0x1a
    extra = {
        Qt.Key_Space:        b"\x00",
        Qt.Key_BracketLeft:  b"\x1b",  # Ctrl+[
        Qt.Key_Backslash:    b"\x1c",  # Ctrl+\
        Qt.Key_BracketRight: b"\x1d",  # Ctrl+]
        Qt.Key_AsciiCircum:  b"\x1e",  # Ctrl+^
        Qt.Key_Underscore:   b"\x1f",  # Ctrl+_
    }
    if key in extra:
        return extra[key]
    return None


def key_to_bytes(
    key: int,
    modifiers: Qt.KeyboardModifiers,
    text: str,
    *,
    app_cursor: bool = False,
    app_keypad: bool = False,
    encoding: str = "utf-8",
    backspace_is_del: bool = True,
    line_ending: bytes = b"\r",
) -> bytes | None:
    """Traduce un evento tasto in byte per il device, o None se da ignorare.

    app_cursor   = DECCKM attivo (frecce in forma applicativa)
    app_keypad   = DECKPAM attivo (riservato, non ancora usato)
    line_ending  = byte inviati su Invio (CR, LF o CRLF)
    """
    ctrl = bool(modifiers & Qt.ControlModifier)
    alt = bool(modifiers & Qt.AltModifier)

    # Solo modificatori premuti: nessun byte.
    if key in (
        Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
        Qt.Key_AltGr, Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock,
    ):
        return None

    # Invio: fine riga configurabile (CR / LF / CRLF), prima del resto.
    if key in (Qt.Key_Return, Qt.Key_Enter):
        return (ESC + line_ending) if alt else line_ending

    # Ctrl+tasto -> control code, ha precedenza sul testo.
    if ctrl and not alt:
        code = _ctrl_code(key, text)
        if code is not None:
            return code

    seq: bytes | None = None
    if key in _CURSOR:
        seq = _CURSOR[key][1 if app_cursor else 0]
    elif key in _TILDE:
        seq = _TILDE[key]
    elif key in _FUNC:
        seq = _FUNC[key]
    elif key in _SIMPLE:
        seq = _SIMPLE[key]
        if key == Qt.Key_Backspace and not backspace_is_del:
            seq = b"\x08"

    if seq is not None:
        # Alt su un tasto speciale = premetti ESC (convenzione meta).
        return (ESC + seq) if alt else seq

    # Testo stampabile.
    if text:
        payload = text.encode(encoding, "replace")
        return (ESC + payload) if alt else payload

    return None
