"""Editor C/C++ con completamento, vai-alla-definizione (F12 / Ctrl+clic), breakpoint nel margine e riga di esecuzione."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPolygon, QTextCursor
from PySide6.QtWidgets import QCompleter, QTextEdit

from ...core.theme import P
from ...modules.editor import CodeEditor
from . import symbols as sy

BREAK_COLOR = "#FF0000"
EXEC_COLOR = "#FFFF00"


class PioEditorBase(CodeEditor):
    goto_definition = Signal(str, int)        # parola, posizione nel documento
    breakpoint_toggled = Signal(int)          # riga (1-based)
    exec_line = 0                             # default di classe: la base chiama _on_cursor prima del nostro __init__
    breakpoints: set = frozenset()  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.index: Optional[sy.ProjectIndex] = None
        self.breakpoints: set[int] = set()
        self.exec_line = 0
        self.auto_complete = True
        self._model = QStringListModel(self)
        self._kinds: dict[str, str] = {}
        self._comp = QCompleter(self._model, self)
        self._comp.setWidget(self)
        self._comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._comp.activated.connect(self._insert_completion)
        self._comp.popup().setStyleSheet(f"QListView{{background:{P.panel};color:{P.text};border:1px solid {P.border};"
                                         f"selection-background-color:{P.accent};selection-color:{P.on_accent};}}")
        self.gutter.installEventFilter(self)

    # ------------------------------------------------------------- completamento
    def _prefix(self) -> str:
        c = self.textCursor()
        return sy.prefix_before(c.block().text(), c.positionInBlock())

    def complete_now(self, force: bool = False) -> None:
        if self.isReadOnly() or self.language != "c/c++" or self.index is None:
            return
        pre = self._prefix()
        if len(pre) < (1 if force else 3):
            self._comp.popup().hide()
            return
        cands = self.index.complete(pre, sy.words_in(self.toPlainText()) - {pre})
        if not cands:
            self._comp.popup().hide()
            return
        self._kinds = dict(cands)
        self._model.setStringList([c[0] for c in cands])
        self._comp.setCompletionPrefix(pre)
        pop = self._comp.popup()
        pop.setCurrentIndex(self._comp.completionModel().index(0, 0))
        r = self.cursorRect()
        r.setWidth(max(260, pop.sizeHintForColumn(0) + pop.verticalScrollBar().sizeHint().width() + 24))
        self._comp.complete(r)

    def _insert_completion(self, text: str) -> None:
        c = self.textCursor()
        pre = self._prefix()
        c.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(pre))
        c.insertText(text)
        self.setTextCursor(c)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        pop = self._comp.popup()
        if pop.isVisible():
            if e.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab, Qt.Key.Key_Escape, Qt.Key.Key_Backtab):
                if e.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                    ix = pop.currentIndex()
                    if ix.isValid():
                        self._insert_completion(ix.data())
                pop.hide()
                e.accept()
                return
        if e.key() == Qt.Key.Key_Space and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.complete_now(force=True)
            return
        if e.key() == Qt.Key.Key_F12 and not e.modifiers():
            self._request_goto(self.textCursor().position())
            return
        if e.key() == Qt.Key.Key_F9 and not e.modifiers():
            self.breakpoint_toggled.emit(self.textCursor().blockNumber() + 1)
            return
        super().keyPressEvent(e)
        if self.auto_complete and e.text() and (e.text().isalnum() or e.text() == "_") and not e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.complete_now()
        elif pop.isVisible() and e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.complete_now()
        elif e.text() and not (e.text().isalnum() or e.text() == "_"):
            pop.hide()

    # ------------------------------------------------------ vai alla definizione
    def _request_goto(self, pos: int) -> None:
        w = sy.word_at(self.toPlainText(), pos)
        if w:
            self.goto_definition.emit(w, pos)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._request_goto(self.cursorForPosition(e.position().toPoint()).position())
            return
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        super().mouseMoveEvent(e)
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)

    # ---------------------------------------------------- breakpoint e esecuzione
    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is getattr(self, "gutter", None) and ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
            c = self.cursorForPosition(QPoint(0, int(ev.position().y())))
            self.breakpoint_toggled.emit(c.blockNumber() + 1)
            return True
        return super().eventFilter(obj, ev)

    def set_breakpoints(self, lines: set[int]) -> None:
        self.breakpoints = set(lines)
        self.gutter.update()

    def set_exec_line(self, line: int) -> None:
        self.exec_line = line
        self._on_cursor()
        self.gutter.update()
        if line:
            blk = self.document().findBlockByNumber(line - 1)
            c = self.textCursor()
            c.setPosition(blk.position())
            self.setTextCursor(c)
            self.centerCursor()

    def _bracket_selections(self) -> list:
        sels = super()._bracket_selections()
        if self.exec_line:
            blk = self.document().findBlockByNumber(self.exec_line - 1)
            if blk.isValid():
                s = QTextEdit.ExtraSelection()
                col = QColor(EXEC_COLOR)
                col.setAlpha(70)
                s.format.setBackground(col)
                from PySide6.QtGui import QTextFormat
                s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                s.cursor = QTextCursor(blk)
                s.cursor.clearSelection()
                sels.append(s)
        return sels

    def paint_gutter(self, ev) -> None:
        super().paint_gutter(ev)
        if not self.breakpoints and not self.exec_line:
            return
        p = QPainter(self.gutter)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        blk = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(blk).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while blk.isValid() and top <= ev.rect().bottom():
            n = blk.blockNumber() + 1
            if blk.isVisible():
                if n in self.breakpoints:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(BREAK_COLOR))
                    p.drawEllipse(2, top + h // 2 - 5, 11, 11)
                if n == self.exec_line:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(EXEC_COLOR))
                    y = top + h // 2
                    p.drawPolygon(QPolygon([QPoint(1, y - 6), QPoint(1, y + 6), QPoint(13, y)]))
            top += round(self.blockBoundingRect(blk).height())
            blk = blk.next()
        p.end()
