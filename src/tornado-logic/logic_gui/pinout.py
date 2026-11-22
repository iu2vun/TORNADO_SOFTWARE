"""Pannello "Pinout": disegno del connettore dell'analizzatore e tabella dei collegamenti."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHeaderView, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from logic_core import (DEFAULT_PINOUT, FUNCTIONS, N_CHANNELS, N_PINS, WIRE_COLORS, channel_index, check_pinout,
                        pin_position, software_name)
from logic_core.verify import OK, WalkResult, walk_summary
from tornado_ui import TRACE_COLORS, Card, P, dim, h2

from .panels import note

KEY = "pinout/assign"


class ConnectorView(QWidget):
    """Header a scatola 2x5 (passo 2,54 mm) in vista frontale, con il nastro dei fili del cavo sotto.

    Pin dispari sulla riga alta, pari su quella bassa, pin 1 a sinistra; lo scasso di polarizzazione è sul lato alto.
    """

    PITCH_X, PITCH_Y = 42.0, 36.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._assign: tuple[str, ...] = DEFAULT_PINOUT
        self.setMinimumSize(300, 292)
        self.setFixedHeight(292)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_assign(self, assign) -> None:
        self._assign = tuple(assign)
        self.update()

    def _origin(self) -> QPointF:
        """Centro del pin 1."""
        return QPointF(self.width() / 2 - 2 * self.PITCH_X, 100.0)

    def pin_center(self, pin: int) -> QPointF:
        col, row = pin_position(pin)
        o = self._origin()
        return QPointF(o.x() + col * self.PITCH_X, o.y() + row * self.PITCH_Y)

    # ------------------------------------------------------------------ disegno
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPixelSize(11)
        p.setFont(f)
        a, b = self.pin_center(1), self.pin_center(10)

        # corpo (shroud): plastica scura con bordo chiaro e riflesso sul lato alto
        body = QRectF(a.x() - 30, a.y() - 28, (b.x() - a.x()) + 60, self.PITCH_Y + 56)
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.0, QColor("#000000"))
        grad.setColorAt(1.0, QColor("#000000"))
        p.setPen(QPen(QColor("#FFFFFF"), 1.4))
        p.setBrush(grad)
        p.drawRoundedRect(body, 5, 5)
        # cavità interna
        cav = body.adjusted(9, 9, -9, -9)
        p.setPen(QPen(QColor("#1010FF"), 1.2))
        p.setBrush(QColor("#000000"))
        p.drawRoundedRect(cav, 2, 2)
        # scasso di polarizzazione sul lato alto (interrompe la parete del corpo)
        notch = QRectF(body.center().x() - 12, body.top() - 1.5, 24, 10.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P.panel))
        p.drawRect(notch)
        p.setPen(QPen(QColor("#FFFFFF"), 1.4))
        p.drawLine(notch.topLeft(), notch.bottomLeft())
        p.drawLine(notch.bottomLeft(), notch.bottomRight())
        p.drawLine(notch.bottomRight(), notch.topRight())

        # triangolo del pin 1 (serigrafia) fuori dal corpo
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(P.accent))
        p.drawPolygon(QPolygonF([QPointF(body.left() - 13, a.y() - 8), QPointF(body.left() - 13, a.y() + 8),
                                 QPointF(body.left() - 3, a.y())]))

        small = QFont(f)
        small.setPixelSize(9)
        for pin in range(1, N_PINS + 1):
            c = self.pin_center(pin)
            fn = self._assign[pin - 1]
            ch = channel_index(fn)
            # pin: quadrato dorato con riflesso
            pg = QLinearGradient(QPointF(c.x() - 4.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() + 4.5))
            pg.setColorAt(0.0, QColor("#FFFF00"))
            pg.setColorAt(1.0, QColor("#FFFF00"))
            p.setPen(QPen(QColor("#000000"), 1))
            p.setBrush(pg)
            p.drawRect(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9))
            # numero serigrafato accanto al pin
            p.setFont(small)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(c.x() + 6, c.y() - 15, 14, 10), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       str(pin))
            # etichetta fuori dal corpo: punto = colore del filo, testo = segnale (colore della traccia per i canali)
            p.setFont(f)
            above = pin_position(pin)[1] == 0
            ly = body.top() - 20 if above else body.bottom() + 6
            label = fn if fn != "NC" else "n.c."
            txt_col = QColor(TRACE_COLORS[ch % len(TRACE_COLORS)]) if ch is not None else QColor(
                P.text if fn == "GND" else P.text_dim)
            tw = QFontMetricsF(f).horizontalAdvance(label)
            x0 = c.x() - (tw + 11) / 2
            p.setPen(QPen(QColor("#FFFFFF"), 1))
            p.setBrush(QColor(WIRE_COLORS[pin - 1][1]))
            p.drawEllipse(QPointF(x0 + 3.5, ly + 8), 3.5, 3.5)
            p.setPen(txt_col)
            p.drawText(QRectF(x0 + 10, ly, tw + 4, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            # linea di richiamo dal pin all'etichetta
            p.setPen(QPen(QColor(P.border), 1, Qt.PenStyle.DotLine))
            if above:
                p.drawLine(QPointF(c.x(), c.y() - 5), QPointF(c.x(), body.top() - 3))
            else:
                p.drawLine(QPointF(c.x(), c.y() + 5), QPointF(c.x(), body.bottom() + 3))

        p.setPen(QColor(P.text_dim))
        p.setFont(f)
        p.drawText(QRectF(0, 2, self.width(), 16), Qt.AlignmentFlag.AlignCenter,
                   "Connettore dell'analizzatore · vista frontale")

        # nastro dei fili del cavo in dotazione
        w = min(28.0, (self.width() - 24) / N_PINS)
        x0 = (self.width() - w * N_PINS) / 2
        ry = body.bottom() + 40
        p.setPen(QColor(P.text_dim))
        p.drawText(QRectF(0, ry - 2, self.width(), 14), Qt.AlignmentFlag.AlignCenter, "Cavo in dotazione: fili 1…10")
        small.setBold(True)
        p.setFont(small)
        for i in range(N_PINS):
            col = QColor(WIRE_COLORS[i][1])
            r = QRectF(x0 + i * w + 1, ry + 14, w - 2, 28)
            p.setPen(QPen(QColor("#FFFFFF"), 1))
            p.setBrush(col)
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor("#000000" if col.lightness() > 140 else "#FFFFFF"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(i + 1))
        p.setFont(f)
        p.setPen(QColor(P.text_dim))
        p.drawText(QRectF(0, self.height() - 34, self.width(), 16), Qt.AlignmentFlag.AlignCenter,
                   "▶ pin 1 · scasso in alto")
        p.drawText(QRectF(0, self.height() - 18, self.width(), 16), Qt.AlignmentFlag.AlignCenter,
                   "punto = filo del cavo · testo = colore traccia")
        p.end()


class PinoutPanel(QScrollArea):
    """Pinout con assegnazione modificabile (salvata) e controlli di coerenza."""

    changed = Signal()
    verifyRequested = Signal()

    def __init__(self, store: Callable[[], QSettings] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 8, 4, 0)
        v.setSpacing(10)

        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.addWidget(h2("Collegamenti"))
        self.view = ConnectorView()
        cl.addWidget(self.view)
        v.addWidget(card)

        self.table = QTableWidget(N_PINS, 4)
        self.table.setHorizontalHeaderLabels(["Pin", "Segnale", "Filo", "Software"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setFixedHeight(34 + 34 * N_PINS)
        self.combos: list[QComboBox] = []
        for i in range(N_PINS):
            it = QTableWidgetItem(str(i + 1))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(i, 0, it)
            cb = QComboBox()
            for fn in FUNCTIONS:
                cb.addItem(fn if fn != "NC" else "n.c. / clock", fn)
            self.table.setCellWidget(i, 1, cb)
            cb.currentIndexChanged.connect(self._on_edit)
            self.combos.append(cb)
            name, hexc = WIRE_COLORS[i]
            wi = QTableWidgetItem(name)
            wi.setForeground(QColor(hexc if QColor(hexc).lightness() > 60 else P.text))
            self.table.setItem(i, 2, wi)
            self.table.setItem(i, 3, QTableWidgetItem(""))
        v.addWidget(self.table)

        self.lbl_warn = note()
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        pal = self.lbl_warn.palette()
        pal.setColor(self.lbl_warn.foregroundRole(), QColor(P.danger))
        self.lbl_warn.setPalette(pal)
        v.addWidget(self.lbl_warn)

        self.btn_reset = QPushButton("Ripristina predefinito")
        self.btn_reset.clicked.connect(lambda: self.set_assign(DEFAULT_PINOUT))
        v.addWidget(self.btn_reset)
        hint = dim("Valori iniziali dai cavi in dotazione ai cloni 24M-8CH (nero…bianco = pin 1…10, CH0…CH7, pin 9 non collegato, pin 10 GND). "
                   "Le unità differiscono: su alcune un secondo GND non è massa (sul tuo il pin 9 non lo è) e il pin 5 può essere un ingresso di clock. "
                   "Verifica con un tester di continuità e correggi qui; la scelta viene salvata. "
                   "CH0…CH7 stampati sul case = CH1…CH8 nel software.")
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        v.addWidget(hint)

        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(8)
        cl.addWidget(h2("Verifica con il tester"))
        how = dim("Collega l'ESP32 col tester (uscite 1…8 ai canali 1…8), nella sua console digita «mode walk», "
                  "poi premi il pulsante: acquisisce 6,5 s a 20 kS/s e controlla che ogni filo vada al canale giusto "
                  "(l'uscita 1 sul CH1 e così via).")
        how.setWordWrap(True)
        how.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        cl.addWidget(how)
        self.btn_verify = QPushButton("Verifica collegamenti")
        self.btn_verify.setProperty("primary", True)
        self.btn_verify.clicked.connect(self.verifyRequested)
        cl.addWidget(self.btn_verify)
        self.lbl_verify = note()
        self.lbl_verify.setWordWrap(True)
        self.lbl_verify.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        cl.addWidget(self.lbl_verify)
        self.vtable = QTableWidget(N_CHANNELS, 4)
        self.vtable.setHorizontalHeaderLabels(["Canale", "Pin", "Esito", "Dettaglio"])
        self.vtable.verticalHeader().setVisible(False)
        self.vtable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vtable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.vtable.setWordWrap(True)
        vh = self.vtable.horizontalHeader()
        for c in (0, 1, 2):
            vh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        vh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.vtable.setFixedHeight(34 + 30 * N_CHANNELS)
        for r in range(N_CHANNELS):
            for c in range(4):
                self.vtable.setItem(r, c, QTableWidgetItem(""))
        self.vtable.setVisible(False)
        cl.addWidget(self.vtable)
        v.addWidget(card)
        v.addStretch(1)

        self.set_assign(self._load(), save=False)

    # ---- stato -------------------------------------------------------------------------
    def assign(self) -> tuple[str, ...]:
        return tuple(cb.currentData() for cb in self.combos)

    def set_assign(self, assign, save: bool = True) -> None:
        for cb in self.combos:
            cb.blockSignals(True)
        for cb, fn in zip(self.combos, assign):
            cb.setCurrentIndex(max(0, cb.findData(fn)))
        for cb in self.combos:
            cb.blockSignals(False)
        self._refresh(save)

    def set_verify_busy(self, busy: bool) -> None:
        self.btn_verify.setEnabled(not busy)
        if busy:
            self.lbl_verify.setText("Acquisizione in corso (circa 7 s)…")
            self.lbl_verify.setVisible(True)

    def show_verify(self, results: list[WalkResult]) -> bool:
        """Mostra l'esito per canale (con il pin secondo l'assegnazione corrente); True se è tutto a posto."""
        a = self.assign()
        for row, r in enumerate(results):
            pins = [i + 1 for i, fn in enumerate(a) if fn == f"CH{r.channel}"]
            cells = (f"CH{r.channel + 1}", ", ".join(map(str, pins)) or "—", r.status, r.message)
            for c, txt in enumerate(cells):
                it = self.vtable.item(row, c)
                it.setText(txt)
                it.setForeground(QColor(P.ok if r.status == OK else P.danger) if c == 2 else QColor(P.text))
                it.setToolTip(r.message)
        for row in range(len(results), N_CHANNELS):
            for c in range(4):
                self.vtable.item(row, c).setText("")
        self.vtable.setVisible(True)
        self.vtable.resizeRowsToContents()
        ok, text = walk_summary(results)
        self.lbl_verify.setText(text)
        self.lbl_verify.setVisible(True)
        return ok

    def _on_edit(self) -> None:
        self._refresh(True)

    def _refresh(self, save: bool) -> None:
        a = self.assign()
        self.view.set_assign(a)
        for i, fn in enumerate(a):
            self.table.item(i, 3).setText(software_name(fn) if fn != "NC" else "—")
        problems = check_pinout(a)
        self.lbl_warn.setText("\n".join(problems))
        self.lbl_warn.setVisible(bool(problems))
        if save and self._store is not None:
            self._store().setValue(KEY, ",".join(a))
        self.changed.emit()

    def _load(self) -> tuple[str, ...]:
        if self._store is None:
            return DEFAULT_PINOUT
        raw = self._store().value(KEY, "", type=str)
        parts = tuple(x for x in raw.split(",")) if raw else ()
        return parts if len(parts) == N_PINS and all(x in FUNCTIONS for x in parts) else DEFAULT_PINOUT
