"""Componenti riusabili: rail laterale, palette comandi, schede/pannelli, barra zoom."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QSlider, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from .icons import icon
from .registry import COMMANDS


class Card(QFrame):
    """Riquadro con bordo arrotondato, cliccabile."""
    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None, clickable: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Card" if clickable else "Panel")
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clickable = clickable

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._clickable and e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def h2(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("H2")
    return l


def dim(text: str) -> QLabel:
    l = QLabel(text)
    l.setObjectName("Dim")
    l.setWordWrap(True)
    return l


class IconRail(QWidget):
    """Colonna di icone che commuta un QStackedWidget (pannelli laterali stile PDFCraft/Lightcraft)."""
    changed = Signal(int)

    def __init__(self, stack: QStackedWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Rail")
        self.setFixedWidth(46)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(5, 8, 5, 8)
        self._lay.setSpacing(4)
        self._lay.addStretch(1)
        self._group = QButtonGroup(self)
        self._group.setExclusive(False)
        self._stack = stack
        self._btns: list[QToolButton] = []

    def add(self, ic: str, tip: str, page: QWidget) -> QToolButton:
        b = QToolButton()
        b.setObjectName("RailBtn")
        b.setIcon(icon(ic, size=22))
        b.setIconSize(QSize(22, 22))
        b.setToolTip(tip)
        b.setCheckable(True)
        idx = self._stack.addWidget(page)
        b.clicked.connect(lambda _=False, i=idx, bb=b: self._toggle(i, bb))
        self._lay.insertWidget(self._lay.count() - 1, b)
        self._btns.append(b)
        return b

    def _toggle(self, idx: int, btn: QToolButton) -> None:
        if btn.isChecked():
            for o in self._btns:
                if o is not btn:
                    o.setChecked(False)
            self._stack.setCurrentIndex(idx)
            self._stack.setVisible(True)
        else:
            self._stack.setVisible(False)
        self.changed.emit(idx)

    def show_page(self, idx: int) -> None:
        for i, b in enumerate(self._btns):
            b.setChecked(i == idx)
        if 0 <= idx < self._stack.count():
            self._stack.setCurrentIndex(idx)
            self._stack.setVisible(True)


class ZoomBar(QWidget):
    """Slider zoom + percentuale (barra di stato dei moduli documento)."""
    zoom_changed = Signal(int)

    def __init__(self, lo: int = 25, hi: int = 400, val: int = 100) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        mi = QToolButton(); mi.setIcon(icon("zoom_out", size=16)); mi.setAutoRaise(True)
        pl = QToolButton(); pl.setIcon(icon("zoom_in", size=16)); pl.setAutoRaise(True)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(val)
        self.slider.setFixedWidth(110)
        self.label = QLabel(f"{val}%")
        self.label.setMinimumWidth(40)
        for w in (mi, self.slider, pl, self.label):
            h.addWidget(w)
        mi.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 10))
        pl.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 10))
        self.slider.valueChanged.connect(self._ch)

    def _ch(self, v: int) -> None:
        self.label.setText(f"{v}%")
        self.zoom_changed.emit(v)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, v: int) -> None:
        self.slider.setValue(v)


class CommandPalette(QDialog):
    """Palette comandi (Ctrl+K): ricerca fuzzy su tutti i comandi registrati."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Cerca comandi, moduli e azioni…")
        self.list = QListWidget()
        self.list.setMinimumHeight(300)
        v.addWidget(self.edit)
        v.addWidget(self.list)
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._run)
        self._cmds: list = []

    def open_centered(self) -> None:
        self._cmds = sorted(COMMANDS.all(), key=lambda c: (c.categoria, c.titolo))
        self.edit.clear()
        self._filter("")
        p = self.parentWidget()
        if p is not None:
            g = p.mapToGlobal(p.rect().center())
            self.move(g.x() - self.width() // 2, p.mapToGlobal(p.rect().topLeft()).y() + 70)
        self.show()
        self.edit.setFocus()

    @staticmethod
    def _score(q: str, text: str) -> int:
        t = text.lower()
        if not q:
            return 1
        if q in t:
            return 100 - t.index(q)
        it = iter(t)
        return 10 if all(ch in it for ch in q) else 0

    def _filter(self, q: str) -> None:
        q = q.lower().strip()
        self.list.clear()
        scored = [(self._score(q, f"{c.categoria} {c.titolo}"), c) for c in self._cmds]
        for s, c in sorted((x for x in scored if x[0] > 0), key=lambda x: -x[0])[:60]:
            it = QListWidgetItem(f"{c.titolo}    ·  {c.categoria}" + (f"    {c.scorciatoia}" if c.scorciatoia else ""))
            if c.icona:
                it.setIcon(icon(c.icona, size=18))
            it.setData(Qt.ItemDataRole.UserRole, c)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run(self, item: QListWidgetItem) -> None:
        c = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        if c:
            c.callback()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                r = self.list.currentRow() + (1 if k == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.list.currentItem():
                self._run(self.list.currentItem())
                return True
            if k == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, ev)


def shortcut(widget: QWidget, seq: str, cb: Callable[[], None]) -> QShortcut:
    s = QShortcut(QKeySequence(seq), widget)
    s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    s.activated.connect(cb)
    return s
