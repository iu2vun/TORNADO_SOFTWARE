"""Ribbon in stile Office (replica del riferimento WordCraft/Word).

    rib = Ribbon()
    home = rib.add_tab("Home")
    g = home.add_group("Appunti")
    g.add_large("Incolla", "paste", cb, menu=...)
    col = g.add_column()            # colonna di bottoni piccoli (3 righe)
    col.add_small("Taglia", "cut", cb)
    g.add_widget(QComboBox())       # widget libero
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QStackedWidget, QTabBar, QToolButton,
    QVBoxLayout, QWidget,
)

from .icons import icon


def _button(text: str, ic: str, cb: Optional[Callable], large: bool, menu: Optional[QMenu] = None,
            checkable: bool = False, tip: str = "", enabled: bool = True) -> QToolButton:
    b = QToolButton()
    b.setObjectName("RibbonBtn")
    b.setText(text)
    b.setIcon(icon(ic, size=32 if large else 18))
    b.setIconSize(QSize(32, 32) if large else QSize(18, 18))
    b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon if large else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    b.setCheckable(checkable)
    b.setAutoRaise(True)
    b.setToolTip(tip or text)
    b.setEnabled(enabled and (cb is not None or menu is not None or checkable))
    if menu is not None:
        b.setMenu(menu)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup if cb else QToolButton.ToolButtonPopupMode.InstantPopup)
    if cb is not None:
        b.clicked.connect(lambda *_: cb())
    if large:
        b.setMinimumWidth(52)
        b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    else:
        b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return b


class RibbonColumn(QWidget):
    """Colonna verticale di controlli piccoli."""

    def __init__(self) -> None:
        super().__init__()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)

    def _push(self, w: QWidget) -> QWidget:
        self._lay.insertWidget(self._lay.count() - 1, w)
        return w

    def add_small(self, text: str, ic: str, cb: Optional[Callable] = None, **kw) -> QToolButton:
        return self._push(_button(text, ic, cb, False, **kw))  # type: ignore[return-value]

    def add_widget(self, w: QWidget) -> QWidget:
        return self._push(w)

    def add_row(self, *widgets: QWidget) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(1)
        for w in widgets:
            h.addWidget(w)
        h.addStretch(1)
        return self._push(row)


class RibbonGroup(QFrame):
    dialog_requested = Signal()

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("RibbonGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 2)
        outer.setSpacing(0)
        self._body = QHBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(2)
        outer.addLayout(self._body, 1)
        t = QLabel(title)
        t.setObjectName("RibbonGroupTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(t)

    def add_large(self, text: str, ic: str, cb: Optional[Callable] = None, **kw) -> QToolButton:
        b = _button(text, ic, cb, True, **kw)
        self._body.addWidget(b)
        return b

    def add_column(self) -> RibbonColumn:
        c = RibbonColumn()
        self._body.addWidget(c)
        return c

    def add_widget(self, w: QWidget) -> QWidget:
        self._body.addWidget(w)
        return w


class RibbonPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 0, 4, 0)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)
        self.groups: dict[str, RibbonGroup] = {}

    def add_group(self, title: str) -> RibbonGroup:
        g = RibbonGroup(title)
        self._lay.insertWidget(self._lay.count() - 1, g)
        self.groups[title] = g
        return g


class Ribbon(QWidget):
    """Barra a schede + pagina a gruppi. Doppio clic su una scheda la comprime."""

    file_requested = Signal()

    def __init__(self, file_tab: str = "File", height: int = 112) -> None:
        super().__init__()
        self.setObjectName("Ribbon")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.bar = QTabBar()
        self.bar.setObjectName("RibbonTabs")
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.stack = QStackedWidget()
        self.stack.setFixedHeight(height)
        self._height = height
        self._file_tab = file_tab
        self._pages: list[RibbonPage] = []
        v.addWidget(self.bar)
        v.addWidget(self.stack)
        self.bar.currentChanged.connect(self._on_tab)
        self.bar.tabBarDoubleClicked.connect(lambda _i: self.toggle_collapsed())
        self._last = 0
        self._collapsed = False

    def add_tab(self, title: str) -> RibbonPage:
        page = RibbonPage()
        self.stack.addWidget(page)
        self.bar.addTab(title)
        self._pages.append(page)
        return page

    def add_file_tab(self, title: str = "File") -> None:
        """Scheda 'File' che non ha pagina propria: emette file_requested (backstage)."""
        page = RibbonPage()
        self.stack.addWidget(page)
        self.bar.addTab(title)
        self._pages.append(page)
        self._file_idx = self.bar.count() - 1

    def _on_tab(self, i: int) -> None:
        if i < 0:
            return
        if hasattr(self, "_file_idx") and i == self._file_idx:
            self.file_requested.emit()
            self.bar.blockSignals(True)
            self.bar.setCurrentIndex(self._last)
            self.bar.blockSignals(False)
            return
        self._last = i
        self.stack.setCurrentIndex(i)
        if self._collapsed:
            self.toggle_collapsed()

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.stack.setVisible(not self._collapsed)

    def select(self, title: str) -> None:
        for i in range(self.bar.count()):
            if self.bar.tabText(i) == title:
                self.bar.setCurrentIndex(i)
                return


def placeholder_group(page: RibbonPage, title: str, items: list[tuple[str, str]]) -> RibbonGroup:
    """Gruppo con bottoni grandi non ancora implementati (disabilitati)."""
    g = page.add_group(title)
    for text, ic in items:
        g.add_large(text, ic, None, tip=f"{text} (da implementare)")
    return g
