"""TerminalWidget — rendering a celle di un AnsiEngine.

Step 1: solo output. Tastiera allo step 2, trasporto allo step 3.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt, QRect, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication,
    QPainter,
)
from PySide6.QtWidgets import QMenu, QWidget

from ...core.theme import P
from .ansi_engine import AnsiEngine
from .keymap import key_to_bytes

# Palette ANSI a 16 colori, tema HP 9845C (8 colori puri). Le chiavi sono i nomi che usa pyte.
# I colori semantici (rosso/verde/giallo/blu) restano riconoscibili: ls, git e i messaggi di errore dipendono da quelli.
PALETTE: dict[str, str] = {
    "black": "#000000",
    "red": "#FF0000",
    "green": "#00FF00",
    "brown": "#FFFF00",  # pyte chiama "brown" il giallo ANSI 33
    "blue": "#1010FF",
    "magenta": "#FF00FF",
    "cyan": "#00FFFF",
    "white": "#FFFFFF",
    "brightblack": "#1010FF",   # grigio ANSI (commenti, testo spento): blu del tema
    "brightred": "#FF0000",
    "brightgreen": "#00FF00",
    "brightbrown": "#FFFF00",
    "brightblue": "#00FFFF",
    "brightmagenta": "#FF00FF",
    "brightcyan": "#00FFFF",
    "brightwhite": "#FFFFFF",
}

DEFAULT_FG = P.text
DEFAULT_BG = P.canvas
SELECTION_BG = "#80FFFFFF"  # selezione: bianco semitrasparente (#AARRGGBB)
CURSOR_BG = P.accent        # cursore a blocco bianco (video inverso), carattere sotto il cursore in P.on_accent
CURSOR_FG = P.on_accent


class ResizeMode(Enum):
    """Cosa fa il resize della finestra (cfr. eWindowMode)."""

    BUFFER = auto()  # cambia righe/colonne, font fisso
    FONT = auto()    # righe/colonne fisse, scala il font


class TerminalWidget(QWidget):
    """Disegna lo screen buffer dell'engine su una griglia di celle."""

    fontSizeChanged = Signal(int)
    dataToSend = Signal(bytes)  # byte da inviare al device (step 3: -> trasporto)

    def __init__(
        self,
        engine: AnsiEngine,
        font_family: str | None = None,
        font_size: int = 11,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._resize_mode = ResizeMode.BUFFER
        self._bold_is_bright = True
        self._blink_on = True
        self._local_echo = False        # off: l'eco lo fa il device (stile pyterm)
        self._backspace_is_del = True   # Backspace -> 0x7f (DEL); False -> 0x08 (BS)
        self._line_ending = b"\r"       # byte su Invio: CR / LF / CRLF

        # Selezione, ancorata a indici assoluti (contenuto), non al viewport.
        self._sel_anchor: tuple[int, int] | None = None  # (abs_line, col)
        self._sel_focus: tuple[int, int] | None = None
        self._selecting = False
        self._sel_block = False  # selezione rettangolare (Alt)
        self._sel_color = QColor(SELECTION_BG)
        self._cursor_bg = QColor(CURSOR_BG)
        self._cursor_fg = QColor(CURSOR_FG)

        self._colors = {k: QColor(v) for k, v in PALETTE.items()}
        self._default_fg = QColor(DEFAULT_FG)
        self._default_bg = QColor(DEFAULT_BG)

        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.IBeamCursor)

        if font_family is None:
            base = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font_family = base.family()
        self._font_family = font_family
        self._font_size = font_size
        self._font_cache: dict[tuple, QFont] = {}
        self._apply_font(font_size)

        engine.invalidated.connect(self._on_invalidated)
        engine.bufferSizeChanged.connect(lambda *_: self.updateGeometry())
        # Le risposte del terminale al device (cursor position report, DA...)
        # escono dallo stesso canale della tastiera.
        engine.deviceReport.connect(self.dataToSend)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_timer.start()

    # ---- font -----------------------------------------------------------

    def _base_font(self, size: int) -> QFont:
        font = QFont(self._font_family, size)
        font.setFixedPitch(True)
        font.setStyleHint(QFont.Monospace, QFont.PreferAntialias)
        font.setKerning(False)
        return font

    def _apply_font(self, size: int) -> None:
        self._font_size = size
        self._font_cache.clear()
        metrics = QFontMetricsF(self._base_font(size))
        # "M" e' il riferimento: su un font monospace ogni cella ha questa
        # larghezza. L'arrotondamento a intero evita il drift cumulativo.
        self._cell_w = max(1, round(metrics.horizontalAdvance("M")))
        self._cell_h = max(1, round(metrics.height()))
        self._ascent = round(metrics.ascent())
        self.fontSizeChanged.emit(size)
        self.updateGeometry()
        self.update()

    def _styled_font(self, bold: bool, italic: bool, under: bool, strike: bool) -> QFont:
        key = (bold, italic, under, strike)
        font = self._font_cache.get(key)
        if font is None:
            font = self._base_font(self._font_size)
            font.setBold(bold)
            font.setItalic(italic)
            font.setUnderline(under)
            font.setStrikeOut(strike)
            self._font_cache[key] = font
        return font

    def _fit_font_to_size(self, width: int, height: int) -> None:
        """Trova il font piu' grande che fa entrare cols x rows (cfr. FindFontForSize)."""
        cols, rows = self._engine.cols, self._engine.rows
        best = 6
        for size in range(6, 49):
            metrics = QFontMetricsF(self._base_font(size))
            cw = max(1, round(metrics.horizontalAdvance("M")))
            ch = max(1, round(metrics.height()))
            if cw * cols <= width and ch * rows <= height:
                best = size
            else:
                break
        if best != self._font_size:
            self._apply_font(best)

    @property
    def font_size(self) -> int:
        return self._font_size

    @font_size.setter
    def font_size(self, size: int) -> None:
        self._apply_font(max(6, min(48, size)))

    @property
    def resize_mode(self) -> ResizeMode:
        return self._resize_mode

    @resize_mode.setter
    def resize_mode(self, mode: ResizeMode) -> None:
        self._resize_mode = mode

    @property
    def local_echo(self) -> bool:
        return self._local_echo

    @local_echo.setter
    def local_echo(self, value: bool) -> None:
        self._local_echo = value

    @property
    def backspace_is_del(self) -> bool:
        return self._backspace_is_del

    @backspace_is_del.setter
    def backspace_is_del(self, value: bool) -> None:
        self._backspace_is_del = value

    @property
    def line_ending(self) -> bytes:
        return self._line_ending

    @line_ending.setter
    def line_ending(self, value: bytes) -> None:
        self._line_ending = value

    # ---- selezione mouse ------------------------------------------------

    def _pixel_to_cell(self, pos) -> tuple[int, int]:
        """Punto in pixel -> (indice assoluto di riga, colonna), con clamp."""
        col = max(0, min(self._engine.cols - 1, pos.x() // self._cell_w))
        vp_row = max(0, min(self._engine.rows - 1, pos.y() // self._cell_h))
        abs_line = self._engine.abs_row_of_viewport(vp_row)
        return abs_line, col

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._selecting = True
        self._sel_block = bool(event.modifiers() & Qt.AltModifier)
        cell = self._pixel_to_cell(event.position().toPoint())
        self._sel_anchor = cell
        self._sel_focus = cell
        self.setFocus()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self._selecting:
            return
        self._sel_focus = self._pixel_to_cell(event.position().toPoint())
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._selecting = False
        # Click senza trascinamento = deseleziona.
        if self._sel_anchor == self._sel_focus:
            self._sel_anchor = self._sel_focus = None
            self.update()

    def _selection_range(self):
        """Selezione normalizzata: (l0, c0, l1, c1) con l0<=l1, o None."""
        if self._sel_anchor is None or self._sel_focus is None:
            return None
        a, b = self._sel_anchor, self._sel_focus
        (l0, c0), (l1, c1) = sorted([a, b])
        return l0, c0, l1, c1

    def _is_selected(self, abs_line: int, col: int) -> bool:
        rng = self._selection_range()
        if rng is None:
            return False
        l0, c0, l1, c1 = rng
        if abs_line < l0 or abs_line > l1:
            return False
        if self._sel_block:
            lo, hi = sorted((c0, c1))
            return lo <= col <= hi
        # Selezione lineare: prima e ultima riga parziali, quelle in mezzo piene.
        if l0 == l1:
            return min(c0, c1) <= col <= max(c0, c1)
        if abs_line == l0:
            return col >= c0
        if abs_line == l1:
            return col <= c1
        return True

    def has_selection(self) -> bool:
        rng = self._selection_range()
        return rng is not None and (rng[0] != rng[2] or rng[1] != rng[3])

    def selected_text(self) -> str:
        rng = self._selection_range()
        if rng is None:
            return ""
        l0, c0, l1, c1 = rng
        cols = self._engine.cols
        lines_out = []
        for abs_line in range(l0, l1 + 1):
            line = self._engine.line_at(abs_line)
            if line is None:
                lines_out.append("")
                continue
            if self._sel_block:
                lo, hi = sorted((c0, c1))
                start, stop = lo, hi + 1
            elif l0 == l1:
                start, stop = min(c0, c1), max(c0, c1) + 1
            elif abs_line == l0:
                start, stop = c0, cols
            elif abs_line == l1:
                start, stop = 0, c1 + 1
            else:
                start, stop = 0, cols
            text = "".join(line[c].data for c in range(start, min(stop, cols)))
            lines_out.append(text.rstrip())
        return "\n".join(lines_out)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste_clipboard(self) -> None:
        text = QGuiApplication.clipboard().text()
        if not text:
            return
        # Normalizza a fine riga configurato e invia al device.
        data = text.replace("\r\n", "\n").replace("\r", "\n")
        data = data.replace("\n", self._line_ending.decode("latin-1"))
        self.dataToSend.emit(data.encode(self._engine.encoding, "replace"))

    def clear_selection(self) -> None:
        self._sel_anchor = self._sel_focus = None
        self.update()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        act_copy = QAction("Copia", self)
        act_copy.setEnabled(self.has_selection())
        act_copy.triggered.connect(self.copy_selection)
        act_paste = QAction("Incolla", self)
        act_paste.setEnabled(bool(QGuiApplication.clipboard().text()))
        act_paste.triggered.connect(self.paste_clipboard)
        act_all = QAction("Seleziona tutto", self)
        act_all.triggered.connect(self.select_all)
        menu.addAction(act_copy)
        menu.addAction(act_paste)
        menu.addSeparator()
        menu.addAction(act_all)
        menu.exec(event.globalPos())

    def select_all(self) -> None:
        last = self._engine.total_lines - 1
        self._sel_block = False
        self._sel_anchor = (0, 0)
        self._sel_focus = (last, self._engine.cols - 1)
        self.update()

    # ---- tastiera -------------------------------------------------------

    def wheelEvent(self, event) -> None:
        # Rotella = scroll dello storico, 3 righe per tacca.
        steps = event.angleDelta().y() / 120.0
        if steps:
            self._engine.scroll_by(int(round(steps * 3)))
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers()
        # Copia/incolla: Ctrl+Shift+C/V, per non rubare Ctrl+C (=0x03) al device.
        if (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
            if event.key() == Qt.Key_C:
                self.copy_selection(); event.accept(); return
            if event.key() == Qt.Key_V:
                self.paste_clipboard(); event.accept(); return
        # Un tasto normale annulla la selezione visiva.
        if self.has_selection() and event.text():
            self.clear_selection()

        # Shift+PgUp/PgDn e Shift+Home/End = navigazione storico, non inviati
        # al device. Senza Shift, PgUp/PgDn vanno al device (es. less, vi).
        if event.modifiers() & Qt.ShiftModifier:
            key = event.key()
            page = max(1, self._engine.rows - 1)
            if key == Qt.Key_PageUp:
                self._engine.scroll_by(page); event.accept(); return
            if key == Qt.Key_PageDown:
                self._engine.scroll_by(-page); event.accept(); return
            if key == Qt.Key_Home:
                self._engine.scroll_to(self._engine.history_len)
                event.accept(); return
            if key == Qt.Key_End:
                self._engine.scroll_to_bottom(); event.accept(); return

        data = key_to_bytes(
            event.key(),
            event.modifiers(),
            event.text(),
            app_cursor=self._engine.app_cursor,
            backspace_is_del=self._backspace_is_del,
            line_ending=self._line_ending,
            encoding=self._engine.encoding,
        )
        if data is None:
            super().keyPressEvent(event)
            return
        event.accept()
        if self._local_echo:
            # Eco a schermo: normalizza il fine riga a CRLF per un a-capo
            # corretto, a prescindere da cosa si invia al device.
            echo = data
            if data == self._line_ending and data in (b"\r", b"\n", b"\r\n"):
                echo = b"\r\n"
            self._engine.feed(echo)
        self.dataToSend.emit(data)

    # ---- colori ---------------------------------------------------------

    def _resolve(self, name: str, default: QColor) -> QColor:
        if name == "default":
            return default
        color = self._colors.get(name)
        if color is not None:
            return color
        if len(name) == 6:  # 256 colori / truecolor: pyte restituisce esadecimale
            return QColor("#" + name)
        return default

    def _cell_colors(self, cell) -> tuple[QColor, QColor]:
        fg_name = cell.fg
        if self._bold_is_bright and cell.bold and fg_name in PALETTE:
            fg_name = fg_name if fg_name.startswith("bright") else "bright" + fg_name
        fg = self._resolve(fg_name, self._default_fg)
        bg = self._resolve(cell.bg, self._default_bg)
        if cell.reverse:
            fg, bg = bg, fg
        return fg, bg

    # ---- geometria ------------------------------------------------------

    def sizeHint(self):
        from PySide6.QtCore import QSize

        return QSize(self._cell_w * self._engine.cols, self._cell_h * self._engine.rows)

    def resizeEvent(self, event) -> None:
        if self._resize_mode is ResizeMode.FONT:
            self._fit_font_to_size(self.width(), self.height())
        else:
            cols = max(1, self.width() // self._cell_w)
            rows = max(1, self.height() // self._cell_h)
            self._engine.resize(cols, rows)
        super().resizeEvent(event)

    # ---- rendering ------------------------------------------------------

    def _on_invalidated(self, rows: set) -> None:
        if len(rows) > self._engine.rows // 2:
            self.update()
            return
        for row in rows:
            self.update(QRect(0, row * self._cell_h, self.width(), self._cell_h))
        cursor = self._engine.screen.cursor
        self.update(QRect(0, cursor.y * self._cell_h, self.width(), self._cell_h))

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        screen = self._engine.screen
        dirty = {
            y
            for y in range(self._engine.rows)
            for x in range(self._engine.cols)
            if screen.buffer[y][x].blink
        }
        dirty.add(screen.cursor.y)
        for row in dirty:
            self.update(QRect(0, row * self._cell_h, self.width(), self._cell_h))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(event.rect(), self._default_bg)

        cw, ch = self._cell_w, self._cell_h
        engine = self._engine
        rect = event.rect()
        row0 = max(0, rect.top() // ch)
        row1 = min(engine.rows - 1, rect.bottom() // ch)

        view = engine.visible_lines()
        has_sel = self.has_selection()
        for row in range(row0, row1 + 1):
            if row >= len(view):
                break
            line, _current = view[row]
            y = row * ch
            abs_line = engine.abs_row_of_viewport(row) if has_sel else -1
            col = 0
            while col < engine.cols:
                cell = line[col]
                style = (
                    cell.fg, cell.bg, cell.bold, cell.italics,
                    cell.underscore, cell.strikethrough, cell.reverse, cell.blink,
                )
                run = col + 1
                while run < engine.cols:
                    nxt = line[run]
                    if (
                        nxt.fg, nxt.bg, nxt.bold, nxt.italics,
                        nxt.underscore, nxt.strikethrough, nxt.reverse, nxt.blink,
                    ) != style:
                        break
                    run += 1

                fg, bg = self._cell_colors(cell)
                if bg != self._default_bg:
                    painter.fillRect(col * cw, y, (run - col) * cw, ch, bg)

                # Evidenziazione selezione, cella per cella (copre anche gli
                # spazi, che nel disegno testo verrebbero saltati).
                if has_sel:
                    for i in range(col, run):
                        if self._is_selected(abs_line, i):
                            painter.fillRect(i * cw, y, cw, ch, self._sel_color)

                if not (cell.blink and not self._blink_on):
                    painter.setFont(
                        self._styled_font(
                            cell.bold, cell.italics,
                            cell.underscore, cell.strikethrough,
                        )
                    )
                    painter.setPen(fg)
                    baseline = y + self._ascent
                    # Disegno per cella: su font non perfettamente monospace
                    # il disegno per run accumulerebbe drift orizzontale.
                    for i in range(col, run):
                        data = line[i].data
                        if data and data != " ":
                            painter.drawText(i * cw, baseline, data)
                col = run

        self._paint_cursor(painter)

    def _paint_cursor(self, painter: QPainter) -> None:
        # Il cursore ha senso solo sulla vista live: scrollando indietro
        # guardi lo storico, dove il cursore non esiste.
        if not self._engine.at_bottom:
            return
        cursor = self._engine.screen.cursor
        if cursor.hidden:
            return
        if not (0 <= cursor.y < self._engine.rows and 0 <= cursor.x < self._engine.cols):
            return
        cw, ch = self._cell_w, self._cell_h
        rect = QRect(cursor.x * cw, cursor.y * ch, cw, ch)
        if not painter.clipRegion().intersects(rect) and not painter.clipRegion().isEmpty():
            return
        cell = self._engine.screen.buffer[cursor.y][cursor.x]
        painter.fillRect(rect, self._cursor_bg)
        painter.setPen(self._cursor_fg)
        painter.setFont(self._styled_font(cell.bold, cell.italics, False, False))
        if cell.data and cell.data != " ":
            painter.drawText(rect.x(), rect.y() + self._ascent, cell.data)
