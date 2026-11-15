"""Widget UI riutilizzabili stile R&S ZNB."""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QWidget, QSizePolicy,
)

FREQ_UNITS = [("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3), ("Hz", 1)]


def fmt_freq(hz: float) -> str:
    a = abs(hz)
    if a >= 1e9:  return f"{hz/1e9:.6g} GHz"
    if a >= 1e6:  return f"{hz/1e6:.6g} MHz"
    if a >= 1e3:  return f"{hz/1e3:.6g} kHz"
    return f"{hz:.6g} Hz"


class SoftkeyPanel(QFrame):
    SLOTS = 10

    def __init__(self):
        super().__init__()
        self.setObjectName("softpanel")
        self.setFixedWidth(148)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6); lay.setSpacing(3)
        self.title = QLabel("MENU")
        self.title.setObjectName("softtitle")
        self.title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)
        self._btns: list[QPushButton] = []
        self._items: list[tuple] = []      # item della pagina corrente
        self._all_items: list[tuple] = []  # tutti gli item del menu
        self._title = "MENU"
        self._page = 0
        for i in range(self.SLOTS):
            b = QPushButton("")
            b.setObjectName("softkey")
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            b.clicked.connect(self._make_cb(i))
            lay.addWidget(b); self._btns.append(b)

    def _make_cb(self, i):
        def h():
            if i < len(self._items) and callable(self._items[i][1]):
                self._items[i][1]()
        return h

    def set_menu(self, title: str, items: list[tuple]):
        """items: list of (label, callback | None).

        Se gli item superano gli slot disponibili, vengono paginati
        automaticamente con voci 'Altro ▼' / '▲ Su' così che il pulsante
        Indietro (in fondo) resti sempre raggiungibile.
        """
        self._title = title
        self._all_items = list(items)
        self._page = 0
        self._render_page()

    def _render_page(self):
        items = self._all_items
        n = len(items)

        if n <= self.SLOTS:
            # entra tutto: nessuna paginazione
            page_items = items
            self.title.setText(self._title)
        else:
            # servono più pagine. Riserva 1 slot per la navigazione pagina.
            # Separa l'ultima voce (di solito "◀ Indietro") per tenerla sempre.
            nav_back = None
            body = items
            if items and str(items[-1][0]).startswith("◀"):
                nav_back = items[-1]
                body = items[:-1]

            per_page = self.SLOTS - 1        # 1 slot per Altro/Su
            if nav_back is not None:
                per_page -= 1                # 1 slot per Indietro sempre visibile

            total_pages = (len(body) + per_page - 1) // per_page
            self._page = max(0, min(self._page, total_pages - 1))
            start = self._page * per_page
            chunk = body[start:start + per_page]

            page_items = list(chunk)
            # voce di navigazione pagina
            if self._page < total_pages - 1:
                page_items.append(("Altro ▼", self._next_page))
            else:
                page_items.append(("▲ Su", self._first_page))
            if nav_back is not None:
                page_items.append(nav_back)

            self.title.setText(f"{self._title}  {self._page+1}/{total_pages}")

        self._items = page_items
        for i, b in enumerate(self._btns):
            if i < len(page_items):
                lbl, _ = page_items[i]
                b.setText(lbl)
                b.setVisible(True)
                b.setProperty("nav", str(lbl).startswith("◀") or
                                     str(lbl).startswith("Altro") or
                                     str(lbl).startswith("▲"))
            else:
                b.setVisible(False)
            b.style().unpolish(b); b.style().polish(b)

    def _next_page(self):
        self._page += 1
        self._render_page()

    def _first_page(self):
        self._page = 0
        self._render_page()


class NumericEntry(QFrame):
    """Barra di immissione stile ZNB: appare in fondo al diagramma."""
    def __init__(self):
        super().__init__()
        self.setObjectName("entry")
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5); lay.setSpacing(8)
        self.lbl  = QLabel(""); self.lbl.setMinimumWidth(100)
        self.edit = QLineEdit(); self.edit.returnPressed.connect(self._enter)
        lay.addWidget(self.lbl); lay.addWidget(self.edit, 1)
        self._ubx  = QHBoxLayout(); self._ubx.setSpacing(4)
        lay.addLayout(self._ubx)
        self._ubtns: list[QPushButton] = []
        self._units: list[tuple] = []; self._cb = None

    def prompt(self, name: str, value: float, units: list[tuple], cb):
        self._units, self._cb = units, cb
        self.lbl.setText(name)
        self.edit.setText(f"{value / units[0][1]:g}")
        for b in self._ubtns:
            b.setParent(None)
        self._ubtns.clear()
        for lbl, fac in units:
            if lbl:
                b = QPushButton(lbl); b.setObjectName("unitbtn"); b.setFixedWidth(58)
                b.clicked.connect(lambda _=False, f=fac: self._apply(f))
                self._ubx.addWidget(b); self._ubtns.append(b)
        self.setVisible(True); self.edit.setFocus(); self.edit.selectAll()

    def _enter(self):
        self._apply(self._units[0][1])

    def _apply(self, factor: float):
        try:
            v = float(self.edit.text().replace(",", ".")) * factor
        except ValueError:
            return
        cb = self._cb; self.setVisible(False)
        if cb: cb(v)


class DiagramArea(QWidget):
    """Contenitore grafico: stack + barra entry + overlay trace-info / marker-box."""
    def __init__(self, stack, entry):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        lay.addWidget(stack, 1)
        lay.addWidget(entry)
        self.info = QLabel(self); self.info.setObjectName("overlay")
        self.info.setTextFormat(Qt.RichText)
        self.info.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.mbox = QLabel(self); self.mbox.setObjectName("overlay")
        self.mbox.setTextFormat(Qt.RichText)
        self.mbox.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.info.move(10, 6)

    def set_info(self, html: str):
        self.info.setText(html); self.info.adjustSize(); self.info.move(10, 6)

    def set_mbox(self, html: str):
        self.mbox.setText(html); self.mbox.adjustSize()
        self.mbox.move(max(12, self.width() - self.mbox.width() - 12), 6)
        self.mbox.setVisible(bool(html))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.mbox.move(max(12, self.width() - self.mbox.width() - 12), 6)


def parse_freq(text: str, default_mult: float = 1e6):
    """'2.45 GHz', '100k', '2400' (MHz di default), '1e9hz' -> Hz. None se non valido."""
    s = text.strip().lower().replace(",", ".").replace(" ", "")
    mult = default_mult
    for suf, m in (("ghz", 1e9), ("mhz", 1e6), ("khz", 1e3), ("hz", 1.0),
                   ("g", 1e9), ("m", 1e6), ("k", 1e3)):
        if s.endswith(suf):
            s, mult = s[: -len(suf)], m
            break
    try:
        return float(s) * mult
    except ValueError:
        return None
