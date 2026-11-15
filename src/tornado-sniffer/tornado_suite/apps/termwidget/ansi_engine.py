"""AnsiEngine — parser ANSI/VT, screen buffer, modalita' hex dump.

Nessuna dipendenza da Qt Widgets: l'engine e' headless e testabile senza GUI.
Il rendering sta in terminal_widget.py, il trasporto arrivera' allo step 3.
"""

from __future__ import annotations

import codecs
from enum import Enum

import pyte
from PySide6.QtCore import QObject, Signal


# Codifiche dei dispositivi: etichetta → codec Python. «ANSI» su Windows = Windows-1252.
ENCODINGS = {
    "UTF-8": "utf-8",
    "ANSI (Windows-1252)": "cp1252",
    "DOS (CP437)": "cp437",
    "DOS Latin-1 (CP850)": "cp850",
    "ISO-8859-1": "latin-1",
    "ISO-8859-15": "iso8859-15",
    "Windows Latin 2 - Europa centrale (1250)": "cp1250",
    "Windows Cirillico (1251)": "cp1251",
    "Windows Greco (1253)": "cp1253",
    "Windows Turco (1254)": "cp1254",
    "Windows Ebraico (1255)": "cp1255",
}


class HexDumpMode(Enum):
    """Modalita' hex dump. WIDE/NARROW = byte per riga (cfr. eIHexDumpMode)."""

    OFF = 0
    NARROW = 8
    WIDE = 16


class _HexDumper:
    """Formatta un flusso di byte in righe di hex dump.

    Le righe parziali vengono ridisegnate in place con CR: il dump e' live,
    ogni byte compare appena arriva senza attendere la riga completa.
    """

    _PRINTABLE = bytes(c if 32 <= c < 127 else 0x2E for c in range(256))

    def __init__(self, width: int = 16) -> None:
        self._width = width
        self._pending = bytearray()
        self._offset = 0

    def reset(self) -> None:
        self._pending.clear()
        self._offset = 0

    @property
    def width(self) -> int:
        return self._width

    @width.setter
    def width(self, value: int) -> None:
        if value != self._width:
            self._width = value
            self.reset()

    def _render(self, chunk: bytes) -> bytes:
        width, half = self._width, self._width // 2
        cells = [
            f"{chunk[i]:02x}" if i < len(chunk) else "  " for i in range(width)
        ]
        hex_part = " ".join(cells[:half]) + "  " + " ".join(cells[half:])
        ascii_part = bytes(self._PRINTABLE[b] for b in chunk).decode("ascii")
        ascii_part = ascii_part.ljust(width)
        return f"{self._offset:08x}  {hex_part}  |{ascii_part}|".encode("ascii")

    def feed(self, data: bytes) -> bytes:
        """Converte byte grezzi nel testo del dump, pronto per lo stream ANSI."""
        out = bytearray()
        for byte in data:
            self._pending.append(byte)
            if len(self._pending) == self._width:
                out += b"\r" + self._render(bytes(self._pending)) + b"\r\n"
                self._offset += self._width
                self._pending.clear()
        if self._pending:
            out += b"\r" + self._render(bytes(self._pending))
        return bytes(out)


class _Screen(pyte.HistoryScreen):
    """HistoryScreen pyte con eventi fuori banda inoltrati all'engine.

    HistoryScreen mantiene le righe uscite dall'alto in history.top, cosi'
    l'engine puo' comporre una vista scrollata senza perdere lo scorrimento.
    """

    def __init__(self, columns: int, lines: int, engine: "AnsiEngine") -> None:
        self._engine = engine
        super().__init__(columns, lines, history=5000, ratio=0.5)

    def bell(self) -> None:
        self._engine.bell.emit()

    def set_title(self, param: str) -> None:
        super().set_title(param)
        self._engine.titleChanged.emit(param)

    def write_process_input(self, data: str) -> None:
        # Risposte del terminale al device: DSR, DA, cursor position report.
        self._engine.deviceReport.emit(data.encode("utf-8", "replace"))


class AnsiEngine(QObject):
    """Screen buffer ANSI con hex dump come modalita' interna.

    I byte grezzi entrano da feed(). Se l'hex dump e' attivo vengono
    riformattati prima di raggiungere il parser: il percorso di rendering
    e' lo stesso in entrambe le modalita'.
    """

    invalidated = Signal(set)  # indici delle righe da ridisegnare
    bufferSizeChanged = Signal(int, int)  # cols, rows
    titleChanged = Signal(str)
    deviceReport = Signal(bytes)  # da inoltrare al trasporto (step 3)
    bell = Signal()
    scrollChanged = Signal(int, int)  # offset corrente, max offset

    def __init__(self, cols: int = 80, rows: int = 24, parent=None) -> None:
        super().__init__(parent)
        self._screen = _Screen(cols, rows, self)
        self._stream = pyte.ByteStream(self._screen)
        self._tstream = pyte.Stream(self._screen)          # per le codifiche a 8 bit non UTF-8
        self._enc = "utf-8"
        self._dec = None
        self._dumper = _HexDumper(HexDumpMode.WIDE.value)
        self._hex_mode = HexDumpMode.OFF
        self._scroll = 0  # righe di storico sopra il fondo; 0 = live

    # ---- proprieta' -----------------------------------------------------

    @property
    def encoding(self) -> str:
        return self._enc

    @encoding.setter
    def encoding(self, codec: str) -> None:
        codec = codecs.lookup(codec).name
        if codec != self._enc:
            self._enc = codec
            self._dec = None if codec == "utf-8" else codecs.getincrementaldecoder(codec)(errors="replace")
            self._stream = pyte.ByteStream(self._screen)

    @property
    def screen(self) -> pyte.Screen:
        return self._screen

    @property
    def cols(self) -> int:
        return self._screen.columns

    @property
    def rows(self) -> int:
        return self._screen.lines

    @property
    def app_cursor(self) -> bool:
        """DECCKM: frecce in forma applicativa (ESC O A) invece che ESC [ A.

        pyte rappresenta il modo privato 1 come 1<<5 nel set screen.mode.
        """
        return (1 << 5) in self._screen.mode

    @property
    def hex_mode(self) -> HexDumpMode:
        return self._hex_mode

    @hex_mode.setter
    def hex_mode(self, mode: HexDumpMode) -> None:
        if mode is self._hex_mode:
            return
        self._hex_mode = mode
        self._dumper.reset()
        if mode is not HexDumpMode.OFF:
            self._dumper.width = mode.value
        self._flush_dirty(force=True)

    # ---- scrollback -----------------------------------------------------

    def set_history_size(self, n: int) -> None:
        """Righe massime di storico (back-scroll)."""
        from collections import deque
        h = self._screen.history
        n = max(100, int(n))
        self._screen.history = h._replace(top=deque(h.top, maxlen=n), bottom=deque(h.bottom, maxlen=n), size=n)

    @property
    def history_len(self) -> int:
        """Righe disponibili nello storico sopra la vista corrente."""
        return len(self._screen.history.top)

    @property
    def scroll_offset(self) -> int:
        return self._scroll

    def scroll_to(self, offset: int) -> None:
        """Sposta la vista: 0 = fondo (live), max = riga piu' vecchia."""
        offset = max(0, min(offset, self.history_len))
        if offset != self._scroll:
            self._scroll = offset
            self.scrollChanged.emit(self._scroll, self.history_len)
            self._flush_dirty(force=True)

    def scroll_by(self, delta: int) -> None:
        self.scroll_to(self._scroll + delta)

    def scroll_to_bottom(self) -> None:
        self.scroll_to(0)

    @property
    def at_bottom(self) -> bool:
        return self._scroll == 0

    def visible_lines(self):
        """Le righe da renderizzare: lista di (line_mapping, is_current_row).

        A offset 0 sono le righe del buffer corrente. Scrollando, le righe
        piu' recenti dello storico entrano dal basso e il buffer esce in alto.
        """
        rows = self.rows
        if self._scroll == 0:
            return [(self._screen.buffer[y], y) for y in range(rows)]
        top = list(self._screen.history.top)
        # Vista completa = storico + buffer; prendo una finestra di 'rows'
        # righe che termina 'scroll' righe sopra il fondo.
        combined = top + [self._screen.buffer[y] for y in range(rows)]
        end = len(combined) - self._scroll
        start = end - rows
        window = combined[max(0, start):end]
        # -1 come indice di riga: nessuna riga dello storico e' "la corrente"
        return [(line, -1) for line in window]

    @property
    def total_lines(self) -> int:
        """Righe totali indirizzabili: storico + buffer visibile."""
        return self.history_len + self.rows

    def line_at(self, index: int):
        """Riga per indice assoluto (0 = piu' vecchia dello storico).

        Permette alla selezione di riferirsi a righe stabili nel contenuto,
        anche mentre si scrolla. None se l'indice e' fuori range.
        """
        top = self._screen.history.top
        n_top = len(top)
        if index < 0 or index >= n_top + self.rows:
            return None
        if index < n_top:
            return top[index]
        return self._screen.buffer[index - n_top]

    def abs_row_of_viewport(self, viewport_row: int) -> int:
        """Indice assoluto della riga mostrata alla riga 'viewport_row'."""
        return self.history_len - self._scroll + viewport_row

    # ---- I/O ------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        if self._hex_mode is not HexDumpMode.OFF:
            data = self._dumper.feed(data)
        if self._dec is None:
            self._stream.feed(data)
        else:
            self._tstream.feed(self._dec.decode(data))
        # Nuovo output riporta al fondo, come ogni terminale.
        if self._scroll != 0:
            self._scroll = 0
            self.scrollChanged.emit(0, self.history_len)
            self._flush_dirty(force=True)
        else:
            self.scrollChanged.emit(0, self.history_len)
            self._flush_dirty()

    def reset(self) -> None:
        self._screen.reset()
        self._dumper.reset()
        self._flush_dirty(force=True)

    def resize(self, cols: int, rows: int) -> None:
        if cols < 1 or rows < 1:
            return
        if cols == self.cols and rows == self.rows:
            return
        self._screen.resize(rows, cols)
        self.bufferSizeChanged.emit(cols, rows)
        self._flush_dirty(force=True)

    # ---- interni --------------------------------------------------------

    def _flush_dirty(self, force: bool = False) -> None:
        if force:
            self._screen.dirty.clear()
            self.invalidated.emit(set(range(self.rows)))
            return
        if self._screen.dirty:
            dirty = set(self._screen.dirty)
            self._screen.dirty.clear()
            self.invalidated.emit(dirty)
