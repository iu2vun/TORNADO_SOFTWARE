"""Widget terminale ANSI/VT100 per Tornado-Term (pyte + rendering custom).

Costo per frame costante: griglia fissa, repaint coalescato (~60 Hz) solo sulle righe sporche,
scrollback con tetto fisso (deque di pyte).
"""
from __future__ import annotations

import re
import time
from itertools import islice

import pyte
from pyte import modes as pm
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea

from ...core.theme import P

DEFAULT_FG = QColor(P.text)
DEFAULT_BG = QColor(P.canvas)

_BASE = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]
_NORMAL = ["#000000", "#cd0000", "#00cd00", "#cdcd00", "#3b3bff", "#cd00cd", "#00cdcd", "#e5e5e5"]
_BRIGHT = ["#7f7f7f", "#ff0000", "#00ff00", "#ffff00", "#6c6cff", "#ff00ff", "#00ffff", "#ffffff"]

PALETTE: dict[str, QColor] = {}
for _n, _a, _b in zip(_BASE, _NORMAL, _BRIGHT):
    PALETTE[_n] = QColor(_a)
    PALETTE["bright" + _n] = QColor(_b)
PALETTE["yellow"] = PALETTE["brown"]
PALETTE["brightyellow"] = PALETTE["brightbrown"]
_BOLD_BRIGHT = {n: "bright" + n for n in _BASE}
_BOLD_BRIGHT["yellow"] = "brightbrown"

_hex_cache: dict[str, QColor] = {}


def _color(name: str, default: QColor, bold: bool) -> QColor:
    if name == "default":
        return default
    if bold and name in _BOLD_BRIGHT:
        name = _BOLD_BRIGHT[name]
    c = PALETTE.get(name)
    if c is not None:
        return c
    c = _hex_cache.get(name)
    if c is None:
        c = QColor("#" + name) if len(name) == 6 else default
        _hex_cache[name] = c
    return c


FLUSH_MS = 16        # una passata a pyte / repaint ogni ~16 ms
FEED_CHUNK = 16384   # byte massimi dati a pyte per passata (la GUI resta reattiva)

_K = Qt.Key
_ARROWS = {int(_K.Key_Up): b"A", int(_K.Key_Down): b"B", int(_K.Key_Right): b"C", int(_K.Key_Left): b"D"}
# tasto -> (lettera xterm, sequenza VT220)
_HOME_END = {int(_K.Key_Home): (b"H", b"\x1b[1~"), int(_K.Key_End): (b"F", b"\x1b[4~")}
_TILDE = {
    int(_K.Key_Insert): 2, int(_K.Key_Delete): 3, int(_K.Key_PageUp): 5, int(_K.Key_PageDown): 6,
    int(_K.Key_F5): 15, int(_K.Key_F6): 17, int(_K.Key_F7): 18, int(_K.Key_F8): 19,
    int(_K.Key_F9): 20, int(_K.Key_F10): 21, int(_K.Key_F11): 23, int(_K.Key_F12): 24,
}
_PF = {int(_K.Key_F1): b"P", int(_K.Key_F2): b"Q", int(_K.Key_F3): b"R", int(_K.Key_F4): b"S"}
_CTRL_PUNCT = {
    int(_K.Key_BracketLeft): 0x1B, int(_K.Key_Backslash): 0x1C, int(_K.Key_BracketRight): 0x1D,
    int(_K.Key_AsciiCircum): 0x1E, int(_K.Key_Underscore): 0x1F, int(_K.Key_Space): 0x00,
    int(_K.Key_At): 0x00,
}
_MODIFIER_KEYS = {int(_K.Key_Shift), int(_K.Key_Control), int(_K.Key_Alt), int(_K.Key_Meta),
                  int(_K.Key_AltGr), int(_K.Key_CapsLock), int(_K.Key_NumLock)}


class VTScreen(pyte.HistoryScreen):
    """HistoryScreen che traccia DECCKM (ESC[?1h/l: cursore in modo applicativo)."""

    app_cursor = False

    def reset(self):
        self.app_cursor = False
        super().reset()

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private") and 1 in modes:
            self.app_cursor = True
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private") and 1 in modes:
            self.app_cursor = False
        super().reset_mode(*modes, **kwargs)


class TerminalWidget(QAbstractScrollArea):
    send = Signal(bytes)            # byte da trasmettere (tastiera, risposte DSR/DA)
    grid_changed = Signal(int, int)  # colonne, righe
    stats = Signal(float, float, int)  # frame/s, byte RX/s, backlog (byte non ancora elaborati)

    def __init__(self, cols: int = 80, rows: int = 24, scrollback: int = 10000, parent=None):
        super().__init__(parent)
        self.hotkeys: dict = {}     # "Ctrl+F1" -> callable (macro)
        self._cols, self._rows = cols, rows
        self._scrollback = scrollback
        self.enter_seq = b"\r"
        self.backspace_seq = b"\x7f"
        self.home_end_vt220 = False   # False: ESC[H / ESC[F (xterm), True: ESC[1~ / ESC[4~
        self.local_echo = False       # mostra sullo schermo cio' che si trasmette
        self._pending = bytearray()
        self._frames = 0
        self._rx_bytes = 0
        self._t_stats = time.monotonic()
        self._fit = False
        self._offset = 0          # righe di scostamento verso l'alto (0 = in coda)
        self._hist_len = 0
        self._dirty: set[int] = set()
        self._all_dirty = True
        self._cursor_on = True
        self._last_cursor_row = 0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self._font = QFont()
        self._set_font_family()
        self._font_pt = 10
        self._apply_font()

        self._new_screen(cols, rows)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._blink = QTimer(self)
        self._blink.setInterval(530)
        self._blink.timeout.connect(self._blink_tick)
        self._blink.start()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._emit_stats)
        self._stats_timer.start()

    # --- schermo pyte ------------------------------------------------------
    def _new_screen(self, cols: int, rows: int):
        self._pending.clear()
        self.screen = VTScreen(cols, rows, history=self._scrollback, ratio=0.5)
        self.screen.write_process_input = lambda s: self.send.emit(s.encode("latin-1", "replace"))
        self.stream = pyte.ByteStream(self.screen, strict=False)
        self.screen.set_mode(pm.LNM)
        self._cols, self._rows = cols, rows
        self._hist_len = 0
        self._offset = 0
        self._all_dirty = True
        self._update_scrollbar()
        self._update_geometry()

    @property
    def lf_newline(self) -> bool:
        return pm.LNM in self.screen.mode

    def set_lf_newline(self, on: bool):
        (self.screen.set_mode if on else self.screen.reset_mode)(pm.LNM)

    def clear(self):
        self._new_screen(self._cols, self._rows)
        self.viewport().update()

    def set_grid(self, cols: int, rows: int):
        cols, rows = max(10, min(400, cols)), max(2, min(200, rows))
        if (cols, rows) == (self._cols, self._rows):
            return
        self.screen.resize(rows, cols)
        self._cols, self._rows = cols, rows
        self._all_dirty = True
        self._update_scrollbar()
        self._update_geometry()
        self._schedule()
        self.grid_changed.emit(cols, rows)

    def set_fit(self, on: bool):
        self._fit = on
        if on:
            self._fit_to_viewport()

    # --- font --------------------------------------------------------------
    def _set_font_family(self):
        self._font.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"])
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self._font.setKerning(False)
        self._font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)

    def _apply_font(self):
        self._font.setPointSize(self._font_pt)
        self._fonts = {}
        for b in (False, True):
            for i in (False, True):
                f = QFont(self._font)
                f.setBold(b)
                f.setItalic(i)
                self._fonts[(b, i)] = f
        fm = QFontMetrics(self._font)
        self._cw = max(1, fm.horizontalAdvance("M"))
        self._ch = max(1, fm.lineSpacing())
        self._ascent = fm.ascent()
        self._all_dirty = True
        self._update_geometry()
        self.viewport().update()

    def set_font_size(self, pt: int):
        self._font_pt = max(6, min(40, pt))
        self._apply_font()
        if self._fit:
            self._fit_to_viewport()

    @property
    def font_size(self) -> int:
        return self._font_pt

    def _update_geometry(self):
        hb = self.horizontalScrollBar()
        extra = self._cols * self._cw - self.viewport().width()
        hb.setRange(0, max(0, extra))
        hb.setPageStep(self.viewport().width())

    def sizeHint(self) -> QSize:
        sb = self.verticalScrollBar().sizeHint().width()
        return QSize(self._cols * self._cw + sb + 4, self._rows * self._ch + 4)

    def _fit_to_viewport(self):
        vp = self.viewport()
        self.set_grid(vp.width() // self._cw, vp.height() // self._ch)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fit:
            self._fit_to_viewport()
        self._update_geometry()
        self._all_dirty = True

    # --- ingresso dati -----------------------------------------------------
    def feed(self, data: bytes):
        """Accoda i byte RX: l'elaborazione avviene nel timer, accorpata (una passata ogni FLUSH_MS)."""
        if not data:
            return
        self._pending += data
        self._rx_bytes += len(data)
        self._schedule()

    def _consume(self, data: bytes):
        try:
            self.stream.feed(data)
        except Exception:  # sequenze malformate: non fermare il flusso
            pass
        scr = self.screen
        hl = len(scr.history.top)
        if hl != self._hist_len:
            if self._offset > 0:
                self._offset = min(self._offset + max(0, hl - self._hist_len), hl)
            self._hist_len = hl
            self._update_scrollbar()
        if scr.dirty:
            if self._offset > 0:
                self._all_dirty = True
            else:
                self._dirty |= scr.dirty
            scr.dirty.clear()
        self._cursor_on = True

    def _schedule(self):
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _emit_stats(self):
        now = time.monotonic()
        dt = max(1e-3, now - self._t_stats)
        self.stats.emit(self._frames / dt, self._rx_bytes / dt, len(self._pending))
        self._frames = 0
        self._rx_bytes = 0
        self._t_stats = now

    def _flush(self):
        if self._pending:
            chunk = bytes(self._pending[:FEED_CHUNK])
            del self._pending[:FEED_CHUNK]
            self._consume(chunk)
        cur = self.screen.cursor.y
        if self._offset == 0:
            self._dirty.add(cur)
            self._dirty.add(self._last_cursor_row)
        self._last_cursor_row = cur
        vp = self.viewport()
        if self._all_dirty:
            vp.update()
        else:
            for r in self._dirty:
                if 0 <= r < self._rows:
                    vp.update(QRect(0, r * self._ch, vp.width(), self._ch))
        self._dirty.clear()
        self._all_dirty = False
        if self._pending:
            self._flush_timer.start()

    def _blink_tick(self):
        self._cursor_on = not self._cursor_on
        if self._offset == 0:
            self._dirty.add(self.screen.cursor.y)
            self._schedule()

    # --- scrollback --------------------------------------------------------
    def _update_scrollbar(self):
        sb = self.verticalScrollBar()
        sb.blockSignals(True)
        sb.setRange(0, self._hist_len)
        sb.setPageStep(self._rows)
        sb.setSingleStep(1)
        sb.setValue(self._hist_len - self._offset)
        sb.blockSignals(False)

    def _on_scroll(self, value: int):
        self._offset = max(0, self._hist_len - value)
        self._all_dirty = True
        self.viewport().update()

    # --- disegno -----------------------------------------------------------
    def paintEvent(self, e):
        self._frames += 1
        p = QPainter(self.viewport())
        cw, ch, rows = self._cw, self._ch, self._rows
        xoff = -self.horizontalScrollBar().value()
        rect = e.rect()
        reverse_screen = pm.DECSCNM in self.screen.mode
        bg0, fg0 = (DEFAULT_FG, DEFAULT_BG) if reverse_screen else (DEFAULT_BG, DEFAULT_FG)
        p.fillRect(rect, bg0)

        top = self.screen.history.top
        hl = len(top)
        first = hl - self._offset
        r0 = max(0, rect.top() // ch)
        r1 = min(rows - 1, rect.bottom() // ch)
        hist_rows = list(islice(top, max(0, first + r0), max(0, first + r1 + 1))) if first + r0 < hl else []
        buf = self.screen.buffer
        for r in range(r0, r1 + 1):
            idx = first + r
            if idx < hl:
                line = hist_rows[idx - (first + r0)]
            else:
                line = buf[idx - hl]
            self._draw_row(p, r, line, xoff, bg0, fg0)

        cur = self.screen.cursor
        if self._offset == 0 and not cur.hidden and 0 <= cur.y < rows:
            x, y = xoff + cur.x * cw, cur.y * ch
            if self.hasFocus():
                if self._cursor_on:
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
                    p.fillRect(x, y, cw, ch, QColor(P.accent_hover))
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            else:
                p.setPen(QPen(DEFAULT_FG))
                p.drawRect(x, y, cw - 1, ch - 1)
        p.end()

    def _draw_row(self, p: QPainter, r: int, line, xoff: int, bg0: QColor, fg0: QColor):
        cw, ch = self._cw, self._ch
        y = r * ch
        base = y + self._ascent
        runs = []  # [col0, ncols, attrs, [(col, char)]]
        cur_key = None
        run = None
        for x in range(self._cols):
            c = line[x]
            d = c.data
            if d == "":
                if run is not None:
                    run[1] += 1
                continue
            key = (c.fg, c.bg, c.bold, c.italics, c.underscore, c.strikethrough, c.reverse)
            if key != cur_key:
                run = [x, 0, key, []]
                runs.append(run)
                cur_key = key
            run[1] += 1
            run[3].append((x, d))

        for col0, n, key, chars in runs:
            fg_n, bg_n, bold, ital, under, strike, rev = key
            fg = _color(fg_n, fg0, bold)
            bg = _color(bg_n, bg0, False)
            if rev:
                fg, bg = bg, fg
            px = xoff + col0 * cw
            if bg != bg0:
                p.fillRect(px, y, n * cw, ch, bg)
            text = "".join(d for _, d in chars)
            if text.strip() or under or strike:
                p.setFont(self._fonts[(bool(bold), bool(ital))])
                p.setPen(fg)
                if text.isascii():
                    if text.strip():
                        p.drawText(px, base, text)
                else:
                    for col, d in chars:
                        if d != " ":
                            p.drawText(xoff + col * cw, base, d)
                if under:
                    p.drawLine(px, y + ch - 2, px + n * cw - 1, y + ch - 2)
                if strike:
                    p.drawLine(px, y + ch // 2, px + n * cw - 1, y + ch // 2)

    # --- tastiera ----------------------------------------------------------
    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def focusNextPrevChild(self, nxt: bool) -> bool:
        return False

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._dirty.add(self.screen.cursor.y)
        self._schedule()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._dirty.add(self.screen.cursor.y)
        self._schedule()

    def _encode_key(self, e) -> bytes | None:
        key = int(e.key())
        mods = e.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        mod = str(1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)).encode()
        modded = mod != b"1"
        esc = b"\x1b"

        if key in (int(_K.Key_Return), int(_K.Key_Enter)):
            return self.enter_seq
        if key == int(_K.Key_Backspace):
            return self.backspace_seq
        if key == int(_K.Key_Tab):
            return b"\t"
        if key == int(_K.Key_Backtab):
            return esc + b"[Z"
        if key == int(_K.Key_Escape):
            return esc
        if key in _ARROWS:
            letter = _ARROWS[key]
            if modded:
                return esc + b"[1;" + mod + letter
            return (esc + b"O" if self.screen.app_cursor else esc + b"[") + letter
        if key in _HOME_END:
            letter, vt220 = _HOME_END[key]
            if self.home_end_vt220:
                return vt220
            if modded:
                return esc + b"[1;" + mod + letter
            return (esc + b"O" if self.screen.app_cursor else esc + b"[") + letter
        if key in _TILDE:
            return esc + b"[" + str(_TILDE[key]).encode() + (b";" + mod if modded else b"") + b"~"
        if key in _PF:
            return (esc + b"[1;" + mod if modded else esc + b"O") + _PF[key]

        t = e.text()
        if ctrl and alt and t and t.isprintable():   # AltGr su Windows = Ctrl+Alt
            return t.encode("utf-8", "replace")
        if ctrl and int(_K.Key_A) <= key <= int(_K.Key_Z):
            c = bytes([key - 0x40])
            return esc + c if alt else c
        if ctrl and key in _CTRL_PUNCT:
            return bytes([_CTRL_PUNCT[key]])
        if not t or not t.isprintable():
            return None
        data = t.encode("utf-8", "replace")
        return esc + data if alt else data

    def keyPressEvent(self, e):
        key = int(e.key())
        mods = e.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        if key in _MODIFIER_KEYS:
            e.accept()
            return
        if self.hotkeys:
            name = QKeySequence(e.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
            cb = self.hotkeys.get(name)
            if cb is not None:
                cb()
                e.accept()
                return
        if ctrl and shift and key == int(_K.Key_V):
            self._paste()
            e.accept()
            return
        if shift and not ctrl and key in (int(_K.Key_PageUp), int(_K.Key_PageDown)):
            sb = self.verticalScrollBar()
            step = max(1, self._rows - 1)
            sb.setValue(sb.value() + (-step if key == int(_K.Key_PageUp) else step))
            e.accept()
            return
        data = self._encode_key(e)
        if data is None:
            super().keyPressEvent(e)
            return
        if self._offset:
            self.verticalScrollBar().setValue(self._hist_len)
        self._cursor_on = True
        self.send.emit(data)
        if self.local_echo:
            self._echo(data)
        e.accept()

    def _echo(self, data: bytes):
        """Eco locale: stessa via dell'RX (rispetta l'ordine con i dati ricevuti), non conta come RX."""
        if data.startswith(b"\x1b"):          # frecce, tasti funzione, Alt+x: nessun eco
            return
        if data == self.backspace_seq:
            out = b"\x08 \x08"
        else:
            out = re.sub(rb"\r\n|\r|\n", b"\r\n", data)
            out = bytes(b for b in out if (b >= 0x20 and b != 0x7F) or b in (0x09, 0x0A, 0x0D))
        if out:
            self._pending += out
            self._schedule()

    def _paste(self):
        t = QGuiApplication.clipboard().text()
        if t:
            data = t.replace("\r\n", "\r").replace("\n", "\r").encode("utf-8", "replace")
            self.send.emit(data)
            if self.local_echo:
                self._echo(data)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_font_size(self._font_pt + (1 if e.angleDelta().y() > 0 else -1))
            e.accept()
            return
        super().wheelEvent(e)
