"""Editor di codice condiviso da Notepad++ e IDE (PySide6 puro, senza QScintilla).

CodeEditor: numeri di riga, riga corrente, parentesi corrispondenti, indentazione
automatica, commento/decommento, evidenziazione sintassi regex-based.
"""
from __future__ import annotations

import os
import re
from typing import Optional

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics, QKeyEvent, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
    QTextDocument, QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from ..core.theme import P

# ----------------------------------------------------------------- linguaggi

PY_KW = ("False None True and as assert async await break class continue def del elif else except finally for from global if "
         "import in is lambda nonlocal not or pass raise return try while with yield match case self cls").split()
PY_BI = "print len range int str float list dict set tuple bool open isinstance super enumerate zip map filter sorted sum min max abs any all type".split()
C_KW = ("auto break case char const continue default do double else enum extern float for goto if inline int long register return "
        "short signed sizeof static struct switch typedef union unsigned void volatile while class namespace template typename "
        "public private protected virtual new delete this try catch throw using bool true false nullptr").split()
JS_KW = ("break case catch class const continue debugger default delete do else export extends finally for function if import in "
         "instanceof let new return super switch this throw try typeof var void while with yield async await of null true false undefined").split()
RS_KW = ("as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return "
         "self Self static struct super trait true type unsafe use where while async await dyn").split()
VB_KW = ("Dim As Sub Function End If Then Else ElseIf For Next While Wend Do Loop Select Case Private Public Const Set Call "
         "Exit Return New True False Nothing And Or Not Is To Step Each In ByVal ByRef Option Explicit Type").split()

LANGS: dict[str, dict] = {
    "python": dict(kw=PY_KW, bi=PY_BI, line="#", block=None, triple=True, deco=True, ext=(".py", ".pyw", ".pyi")),
    "c/c++": dict(kw=C_KW, bi=[], line="//", block=("/*", "*/"), ext=(".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".ino")),
    "javascript": dict(kw=JS_KW, bi=[], line="//", block=("/*", "*/"), ext=(".js", ".ts", ".jsx", ".tsx", ".java", ".cs", ".go")),
    "rust": dict(kw=RS_KW, bi=[], line="//", block=("/*", "*/"), ext=(".rs",)),
    "vb": dict(kw=VB_KW, bi=[], line="'", block=None, ext=(".vb", ".bas", ".frm", ".cls"), icase=True),
    "json": dict(kw=["true", "false", "null"], bi=[], line=None, block=None, ext=(".json",)),
    "ini": dict(kw=[], bi=[], line=";", block=None, ext=(".ini", ".cfg", ".conf", ".toml", ".properties")),
    "xml/html": dict(kw=[], bi=[], line=None, block=("<!--", "-->"), markup=True, ext=(".xml", ".html", ".htm", ".svg", ".ui", ".qrc")),
    "batch": dict(kw="echo set if else goto call for do in not exist rem pause exit cd copy del".split(), bi=[], line="REM", block=None, ext=(".bat", ".cmd"), icase=True),
    "markdown": dict(kw=[], bi=[], line=None, block=None, md=True, ext=(".md", ".markdown")),
    "testo": dict(kw=[], bi=[], line=None, block=None, ext=(".txt", ".log", "")),
}


def language_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    for name, d in LANGS.items():
        if ext in d["ext"] and ext != "":
            return name
    return "testo"


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


C_KW_COL, C_BI, C_STR, C_COM, C_NUM, C_DEC, C_TAG, C_ATTR = "#ffd060", "#ff9a00", "#ffe08a", "#8a5a00", "#ffc933", "#cc8a00", "#ffd060", "#ff9a00"


class Highlighter(QSyntaxHighlighter):
    def __init__(self, doc: QTextDocument, lang: str = "testo") -> None:
        super().__init__(doc)
        self.set_language(lang)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        d = LANGS.get(lang, LANGS["testo"])
        self.d = d
        opt = QRegularExpression.PatternOption.CaseInsensitiveOption if d.get("icase") else QRegularExpression.PatternOption.NoPatternOption
        self.rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        R = lambda p: QRegularExpression(p, opt)  # noqa: E731
        self.rules.append((R(r"\b\d+(\.\d+)?([eE][+-]?\d+)?\b|\b0[xX][0-9a-fA-F]+\b"), _fmt(C_NUM)))
        if d["kw"]:
            self.rules.append((R(r"\b(" + "|".join(d["kw"]) + r")\b"), _fmt(C_KW_COL, bold=True)))
        if d["bi"]:
            self.rules.append((R(r"\b(" + "|".join(d["bi"]) + r")\b"), _fmt(C_BI)))
        if d.get("deco"):
            self.rules.append((R(r"^\s*@[\w.]+"), _fmt(C_DEC)))
            self.rules.append((R(r"\b(?:def|class)\s+(\w+)"), _fmt("#ffd23f", bold=True)))
        if d.get("markup"):
            self.rules.append((R(r"</?[\w:.-]+|/?>"), _fmt(C_TAG)))
            self.rules.append((R(r"\b[\w:-]+(?==)"), _fmt(C_ATTR)))
        if d.get("md"):
            self.rules.append((R(r"^#{1,6}\s.*$"), _fmt(C_KW_COL, bold=True)))
            self.rules.append((R(r"\*\*[^*]+\*\*|`[^`]+`"), _fmt(C_BI)))
        if lang == "ini":
            self.rules.append((R(r"^\s*\[[^\]]+\]"), _fmt(C_KW_COL, bold=True)))
            self.rules.append((R(r"^\s*[\w.-]+(?=\s*[=:])"), _fmt(C_ATTR)))
        if lang == "json":
            self.rules.append((R(r'"[^"\\]*(\\.[^"\\]*)*"(?=\s*:)'), _fmt(C_ATTR)))
        self.rules.append((R(r'"[^"\\]*(\\.[^"\\]*)*"|\'[^\'\\]*(\\.[^\'\\]*)*\''), _fmt(C_STR)))
        self.fmt_com = _fmt(C_COM, italic=True)
        self.fmt_str = _fmt(C_STR)
        line = d.get("line")
        self.line_re = R(re.escape(line) + r".*$") if line else None
        b = d.get("block")
        self.bs = R(re.escape(b[0])) if b else None
        self.be = R(re.escape(b[1])) if b else None
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                g = 1 if rx.captureCount() and rx.pattern().startswith(r"\b(?:def|class)") else 0
                self.setFormat(m.capturedStart(g), m.capturedLength(g), f)
        if self.line_re:
            m = self.line_re.match(text)
            if m.hasMatch() and not self._in_string(text, m.capturedStart()):
                self.setFormat(m.capturedStart(), m.capturedLength(), self.fmt_com)
        self.setCurrentBlockState(0)
        # commenti a blocco /* */ e <!-- -->
        if self.bs is not None:
            start = 0 if self.previousBlockState() == 1 else self.bs.match(text).capturedStart()
            while start >= 0:
                e = self.be.match(text, start + (0 if self.previousBlockState() == 1 and start == 0 else 1))
                if e.hasMatch():
                    ln = e.capturedEnd() - start
                    self.setFormat(start, ln, self.fmt_com)
                    nxt = self.bs.match(text, start + ln)
                    start = nxt.capturedStart() if nxt.hasMatch() else -1
                else:
                    self.setCurrentBlockState(1)
                    self.setFormat(start, len(text) - start, self.fmt_com)
                    break
        # stringhe triple Python
        if self.d.get("triple"):
            for delim, st in (('"""', 2), ("'''", 3)):
                self._triple(text, delim, st)

    def _triple(self, text: str, delim: str, state: int) -> None:
        prev = self.previousBlockState()
        start = 0 if prev == state else text.find(delim)
        if prev != state and self.currentBlockState() > 0 and self.currentBlockState() != state:
            return
        while start >= 0:
            end = text.find(delim, start + (0 if prev == state and start == 0 else 3))
            if end >= 0:
                ln = end - start + 3
                self.setFormat(start, ln, self.fmt_str)
                start = text.find(delim, start + ln)
                prev = 0
            else:
                self.setCurrentBlockState(state)
                self.setFormat(start, len(text) - start, self.fmt_str)
                break

    @staticmethod
    def _in_string(text: str, pos: int) -> bool:
        q = None
        for ch in text[:pos]:
            if q is None and ch in "\"'":
                q = ch
            elif q == ch:
                q = None
        return q is not None


# -------------------------------------------------------------------- editor

class _Gutter(QWidget):
    def __init__(self, ed: "CodeEditor") -> None:
        super().__init__(ed)
        self.ed = ed

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.ed.gutter_width(), 0)

    def paintEvent(self, e) -> None:  # noqa: N802
        self.ed.paint_gutter(e)


def mono_font(size: int = 11) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    for fam in ("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"):
        if fam in QFontDatabase.families():
            f.setFamily(fam)
            break
    f.setPointSize(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


class CodeEditor(QPlainTextEdit):
    cursor_info = Signal(int, int)       # riga, colonna (1-based)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFont(mono_font())
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(QFontMetrics(self.font()).horizontalAdvance(" ") * 4)
        self.setStyleSheet(f"QPlainTextEdit{{background:{P.canvas};border:none;padding:0;}}")
        self.gutter = _Gutter(self)
        self.highlighter = Highlighter(self.document(), "testo")
        self.indent_unit = "    "
        self.path = ""
        self.encoding = "utf-8"
        self.eol = "\n"
        self.blockCountChanged.connect(self._update_margin)
        self.updateRequest.connect(self._scroll_gutter)
        self.cursorPositionChanged.connect(self._on_cursor)
        self._update_margin()
        self._on_cursor()

    # ----------------------------------------------------------- linguaggio
    def set_language(self, lang: str) -> None:
        self.highlighter.set_language(lang)

    @property
    def language(self) -> str:
        return self.highlighter.lang

    # ---------------------------------------------------------------- file
    def load(self, path: str) -> None:
        raw = open(path, "rb").read()
        enc = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            enc = "cp1252"
            text = raw.decode(enc, errors="replace")
        self.eol = "\r\n" if "\r\n" in text else "\r" if "\r" in text and "\n" not in text else "\n"
        self.setPlainText(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.encoding, self.path = enc, path
        self.document().setModified(False)
        self.set_language(language_for(path))

    def save(self, path: Optional[str] = None) -> None:
        path = path or self.path
        text = self.toPlainText().replace("\n", self.eol)
        with open(path, "w", encoding=self.encoding, newline="") as f:
            f.write(text)
        if path != self.path:
            self.path = path
            self.set_language(language_for(path))
        self.document().setModified(False)

    # ---------------------------------------------------------- numeri riga
    def gutter_width(self) -> int:
        digits = max(3, len(str(self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margin(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _scroll_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_margin()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, ev) -> None:
        p = QPainter(self.gutter)
        p.fillRect(ev.rect(), QColor(P.panel))
        blk = self.firstVisibleBlock()
        n = blk.blockNumber()
        top = round(self.blockBoundingGeometry(blk).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(blk).height())
        cur = self.textCursor().blockNumber()
        while blk.isValid() and top <= ev.rect().bottom():
            if blk.isVisible() and bottom >= ev.rect().top():
                p.setPen(QColor(P.accent if n == cur else P.text_dim))
                p.drawText(0, top, self.gutter.width() - 8, self.fontMetrics().height(), Qt.AlignmentFlag.AlignRight, str(n + 1))
            blk = blk.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(blk).height())
            n += 1
        p.setPen(QColor(P.border))
        p.drawLine(self.gutter.width() - 1, ev.rect().top(), self.gutter.width() - 1, ev.rect().bottom())

    # ------------------------------------------------ riga corrente/parentesi
    def _on_cursor(self) -> None:
        sels = []
        if not self.isReadOnly():
            s = QTextEdit.ExtraSelection()
            s.format.setBackground(QColor(255, 255, 255, 12))
            s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            s.cursor = self.textCursor()
            s.cursor.clearSelection()
            sels.append(s)
        sels += self._bracket_selections()
        self.setExtraSelections(sels)
        c = self.textCursor()
        self.cursor_info.emit(c.blockNumber() + 1, c.positionInBlock() + 1)
        self.gutter.update()

    def _bracket_selections(self) -> list:
        pairs = {"(": ")", "[": "]", "{": "}"}
        rev = {v: k for k, v in pairs.items()}
        doc, c = self.document(), self.textCursor()
        pos = c.position()
        for p in (pos, pos - 1):
            if p < 0 or p >= doc.characterCount():
                continue
            ch = doc.characterAt(p)
            if ch in pairs or ch in rev:
                fwd = ch in pairs
                other = pairs.get(ch) or rev[ch]
                depth, q = 0, p
                while 0 <= q < doc.characterCount():
                    x = doc.characterAt(q)
                    if x == ch:
                        depth += 1
                    elif x == other:
                        depth -= 1
                        if depth == 0:
                            out = []
                            for pp in (p, q):
                                s = QTextEdit.ExtraSelection()
                                s.format.setBackground(QColor(255, 138, 31, 90))
                                s.cursor = QTextCursor(doc)
                                s.cursor.setPosition(pp)
                                s.cursor.setPosition(pp + 1, QTextCursor.MoveMode.KeepAnchor)
                                out.append(s)
                            return out
                    q += 1 if fwd else -1
                return []
        return []

    # ------------------------------------------------------------- tastiera
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        k, mod = e.key(), e.modifiers()
        c = self.textCursor()
        if k == Qt.Key.Key_Tab and not mod:
            if c.hasSelection():
                self._indent_selection(True)
            else:
                col = c.positionInBlock()
                c.insertText(self.indent_unit[: len(self.indent_unit) - col % len(self.indent_unit)])
            return
        if k == Qt.Key.Key_Backtab:
            self._indent_selection(False)
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mod & Qt.KeyboardModifier.ControlModifier:
            line = c.block().text()[: c.positionInBlock()]
            ind = re.match(r"\s*", line).group(0)
            if self.language == "python" and line.rstrip().endswith(":"):
                ind += self.indent_unit
            elif self.language in ("c/c++", "javascript", "rust") and line.rstrip().endswith("{"):
                ind += self.indent_unit
            c.insertText("\n" + ind)
            self.setTextCursor(c)
            self.ensureCursorVisible()
            return
        if k == Qt.Key.Key_Slash and mod & Qt.KeyboardModifier.ControlModifier:
            self.toggle_comment()
            return
        if k == Qt.Key.Key_D and mod & Qt.KeyboardModifier.ControlModifier:
            self.duplicate_line()
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if e.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(e)

    def zoom(self, d: int) -> None:
        f = self.font()
        f.setPointSize(max(6, min(40, f.pointSize() + d)))
        self.setFont(f)
        self.setTabStopDistance(QFontMetrics(f).horizontalAdvance(" ") * 4)
        self._update_margin()

    # ------------------------------------------------------------ operazioni
    def _selected_blocks(self) -> tuple[int, int]:
        c = self.textCursor()
        a, b = c.selectionStart(), c.selectionEnd()
        doc = self.document()
        s = doc.findBlock(a).blockNumber()
        e = doc.findBlock(b).blockNumber()
        if c.hasSelection() and b == doc.findBlock(b).position() and e > s:
            e -= 1
        return s, e

    def _indent_selection(self, add: bool) -> None:
        s, e = self._selected_blocks()
        c = self.textCursor()
        c.beginEditBlock()
        for n in range(s, e + 1):
            blk = self.document().findBlockByNumber(n)
            cc = QTextCursor(blk)
            if add:
                cc.insertText(self.indent_unit)
            else:
                t = blk.text()
                k = min(len(self.indent_unit), len(t) - len(t.lstrip(" ")))
                if t.startswith("\t"):
                    k = 1
                cc.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, k)
                cc.removeSelectedText()
        c.endEditBlock()

    def toggle_comment(self) -> None:
        tok = LANGS.get(self.language, {}).get("line")
        if not tok:
            return
        s, e = self._selected_blocks()
        blocks = [self.document().findBlockByNumber(n) for n in range(s, e + 1)]
        all_c = all(b.text().strip().startswith(tok) or not b.text().strip() for b in blocks)
        c = self.textCursor()
        c.beginEditBlock()
        for b in blocks:
            t = b.text()
            if not t.strip():
                continue
            cc = QTextCursor(b)
            ind = len(t) - len(t.lstrip())
            cc.setPosition(b.position() + ind)
            if all_c:
                cc.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(tok) + (1 if t[ind + len(tok): ind + len(tok) + 1] == " " else 0))
                cc.removeSelectedText()
            else:
                cc.insertText(tok + " ")
        c.endEditBlock()

    def duplicate_line(self) -> None:
        c = self.textCursor()
        if c.hasSelection():
            t = c.selectedText()
            c.setPosition(c.selectionEnd())
            c.insertText(t)
        else:
            t = c.block().text()
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            c.insertText("\n" + t)
        self.setTextCursor(c)

    def goto_line(self, n: int) -> None:
        blk = self.document().findBlockByNumber(max(0, n - 1))
        c = self.textCursor()
        c.setPosition(blk.position())
        self.setTextCursor(c)
        self.centerCursor()
        self.setFocus()

    def find_text(self, text: str, backwards: bool = False, case: bool = False, regex: bool = False, whole: bool = False) -> bool:
        if not text:
            return False
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindFlag.FindBackward
        if case:
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if whole:
            flags |= QTextDocument.FindFlag.FindWholeWords
        pat = QRegularExpression(text, QRegularExpression.PatternOption.NoPatternOption if case else QRegularExpression.PatternOption.CaseInsensitiveOption) if regex else text
        found = self.document().find(pat, self.textCursor(), flags)
        if found.isNull():
            c = QTextCursor(self.document())
            if backwards:
                c.movePosition(QTextCursor.MoveOperation.End)
            found = self.document().find(pat, c, flags)
        if found.isNull():
            return False
        self.setTextCursor(found)
        return True

    def replace_all(self, text: str, repl: str, case: bool = False, regex: bool = False) -> int:
        if not text:
            return 0
        src = self.toPlainText()
        flags = 0 if case else re.IGNORECASE
        pat = re.compile(text if regex else re.escape(text), flags)
        new, n = pat.subn(repl if regex else repl.replace("\\", "\\\\"), src)
        if n:
            c = self.textCursor()
            c.beginEditBlock()
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(new)
            c.endEditBlock()
        return n
