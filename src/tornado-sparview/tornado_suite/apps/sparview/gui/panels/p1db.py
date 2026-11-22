"""Pannello P1dB: da sweep di potenza (CSV) o da un set di S2P a potenze diverse."""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMessageBox, QPushButton, QSlider, QSpinBox, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...analysis import compression as cp
from ...core.units import fmt_eng
from ..model import Workspace
from ..plot import draw as D
from ..plot.ticks import decimals_for, fmt_tick, linear_ticks
from ..theme import DARK, LIGHT, Theme, qcolor


class XYPlot(QWidget):
    """Grafico XY minimale (QPainter) per curve di compressione."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.curves: list[tuple[np.ndarray, np.ndarray, str, str]] = []   # x, y, colore, etichetta
        self.hlines: list[tuple[float, str, str]] = []
        self.vlines: list[tuple[float, str, str]] = []
        self.points: list[tuple[float, float, str, str]] = []
        self.xlabel = self.ylabel = ""

    def set_data(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw(p, QRectF(self.rect()), DARK)
        p.end()

    def render_image(self, w: int = 900, h: int = 560, th: Theme = LIGHT, scale: float = 2.0):
        from PySide6.QtGui import QImage
        img = QImage(int(w * scale), int(h * scale), QImage.Format.Format_ARGB32)
        img.fill(qcolor(th.bg))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        self.draw(p, QRectF(0, 0, w, h), th)
        p.end()
        return img

    def draw(self, p: QPainter, r: QRectF, th: Theme) -> None:
        p.fillRect(r, qcolor(th.bg))
        plot = QRectF(r.left() + 62, r.top() + 14, r.width() - 80, r.height() - 56)
        p.fillRect(plot, qcolor(th.plot_bg))
        xs = np.concatenate([c[0] for c in self.curves]) if self.curves else np.array([0.0, 1.0])
        ys = np.concatenate([c[1] for c in self.curves]) if self.curves else np.array([0.0, 1.0])
        xs, ys = xs[np.isfinite(xs)], ys[np.isfinite(ys)]
        if xs.size == 0 or ys.size == 0:
            p.setPen(qcolor(th.text_dim))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Nessun dato")
            return
        x0, x1 = float(xs.min()), float(xs.max())
        y0, y1 = float(ys.min()), float(ys.max())
        for v, _, _ in self.hlines:
            y0, y1 = min(y0, v), max(y1, v)
        if x1 <= x0:
            x1 = x0 + 1
        if y1 <= y0:
            y1 = y0 + 1
        py = 0.06 * (y1 - y0)
        y0, y1 = y0 - py, y1 + py
        sx = lambda v: plot.left() + (v - x0) / (x1 - x0) * plot.width()
        sy = lambda v: plot.bottom() - (v - y0) / (y1 - y0) * plot.height()
        f = D.font(10)
        p.setFont(f)
        xt, xstep = linear_ticks(x0, x1, 8)
        yt, ystep = linear_ticks(y0, y1, 8)
        for t in xt:
            if x0 <= t <= x1:
                p.setPen(QPen(qcolor(th.grid), 1))
                p.drawLine(QPointF(sx(t), plot.top()), QPointF(sx(t), plot.bottom()))
                p.setPen(qcolor(th.text_dim))
                p.drawText(QRectF(sx(t) - 30, plot.bottom() + 3, 60, 14), Qt.AlignmentFlag.AlignCenter,
                           fmt_tick(t, decimals_for(xstep)))
        for t in yt:
            if y0 <= t <= y1:
                p.setPen(QPen(qcolor(th.grid), 1))
                p.drawLine(QPointF(plot.left(), sy(t)), QPointF(plot.right(), sy(t)))
                p.setPen(qcolor(th.text_dim))
                p.drawText(QRectF(plot.left() - 58, sy(t) - 7, 52, 14),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           fmt_tick(t, decimals_for(ystep)))
        p.setPen(QPen(qcolor(th.axis), 1))
        p.drawRect(plot)
        p.save()
        p.setClipRect(plot)
        for n, (v, col, lab) in enumerate(self.hlines):
            p.setPen(QPen(qcolor(col), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(plot.left(), sy(v)), QPointF(plot.right(), sy(v)))
            p.drawText(QPointF(plot.left() + 6, sy(v) - 4 if n == 0 else sy(v) + 14), lab)
        for v, col, lab in self.vlines:
            p.setPen(QPen(qcolor(col), 1.2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(sx(v), plot.top()), QPointF(sx(v), plot.bottom()))
        for x, y, c, l in self.curves:
            m = np.isfinite(x) & np.isfinite(y)
            if m.sum() < 2:
                continue
            pts = [QPointF(sx(a), sy(b)) for a, b in zip(x[m], y[m])]
            p.setPen(QPen(qcolor(c), 2))
            for a, b in zip(pts[:-1], pts[1:]):
                p.drawLine(a, b)
            p.setBrush(qcolor(c))
            for q in pts if len(pts) <= 120 else []:
                p.drawEllipse(q, 2.2, 2.2)
        p.restore()
        for x, y, c, lab in self.points:
            q = QPointF(sx(x), sy(y))
            p.setPen(QPen(qcolor(th.bg), 1.5))
            p.setBrush(qcolor(c))
            p.drawEllipse(q, 6, 6)
            p.setPen(qcolor(c))
            p.setFont(D.font(11, bold=True))
            p.drawText(QPointF(min(q.x() + 10, plot.right() - 150), max(q.y() - 10, plot.top() + 14)), lab)
        p.setFont(D.font(11))
        p.setPen(qcolor(th.text))
        p.drawText(QRectF(plot.left(), r.bottom() - 22, plot.width(), 18), Qt.AlignmentFlag.AlignCenter, self.xlabel)
        p.save()
        p.translate(r.left() + 12, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-plot.height() / 2, -8, plot.height(), 16), Qt.AlignmentFlag.AlignCenter, self.ylabel)
        p.restore()


class P1dBPanel(QWidget):
    def __init__(self, ws: Workspace, message: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.ws, self.message = ws, message
        self._csv: Optional[tuple[list[str], np.ndarray]] = None
        self._sweep: Optional[cp.SweepSet] = None
        self._vs: Optional[tuple] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()
        self.src = QComboBox()
        self.src.addItem("Sweep di potenza da CSV", 0)
        self.src.addItem("Set di S2P a potenze diverse", 1)
        top.addWidget(QLabel("Sorgente:"))
        top.addWidget(self.src, 1)
        lay.addLayout(top)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        # ---- CSV
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        b = QPushButton("Apri CSV sweep…")
        b.clicked.connect(self.load_csv)
        self.csv_name = QLabel("—")
        self.csv_name.setProperty("dim", True)
        self.c_x, self.c_y, self.c_type = QComboBox(), QComboBox(), QComboBox()
        self.c_type.addItem("Guadagno [dB]", "gain")
        self.c_type.addItem("Pout [dBm]", "pout")
        f.addRow(b, self.csv_name)
        f.addRow("Colonna Pin [dBm]:", self.c_x)
        f.addRow("Colonna Y:", self.c_y)
        f.addRow("Y è:", self.c_type)
        self.stack.addWidget(w)
        # ---- S2P
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Usa", "Rete", "Pin [dBm]"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(130)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        v.addWidget(self.table)
        r = QHBoxLayout()
        bb = QPushButton("Aggiorna elenco / potenze dal nome")
        bb.clicked.connect(self.fill_table)
        r.addWidget(bb)
        self.sij = QComboBox()
        for lab, ij in (("S21", (1, 0)), ("S12", (0, 1))):
            self.sij.addItem(lab, ij)
        r.addWidget(QLabel("Parametro:"))
        r.addWidget(self.sij)
        v.addLayout(r)
        self.stack.addWidget(w)
        self.src.currentIndexChanged.connect(self.stack.setCurrentIndex)

        g = QGroupBox("Definizione")
        f = QFormLayout(g)
        self.n_ref = QSpinBox()
        self.n_ref.setRange(1, 1000)
        self.n_ref.setValue(5)
        self.n_ref.setToolTip("Media dei primi N punti (potenza più bassa) = guadagno small-signal")
        self.drop = QDoubleSpinBox()
        self.drop.setRange(0.1, 10)
        self.drop.setDecimals(2)
        self.drop.setValue(1.0)
        self.drop.setSuffix(" dB")
        f.addRow("Punti di riferimento N:", self.n_ref)
        f.addRow("Compressione:", self.drop)
        lay.addWidget(g)

        r = QHBoxLayout()
        b = QPushButton("Calcola P1dB")
        b.setProperty("accent", True)
        b.clicked.connect(self.compute)
        r.addWidget(b)
        self.view = QComboBox()
        self.view.addItem("Guadagno vs Pin", 0)
        self.view.addItem("P1dB vs frequenza", 1)
        self.view.currentIndexChanged.connect(self._redraw)
        r.addWidget(self.view)
        lay.addLayout(r)
        self.fslider = QSlider(Qt.Orientation.Horizontal)
        self.fslider.valueChanged.connect(self._redraw)
        self.flabel = QLabel("")
        self.flabel.setProperty("dim", True)
        lay.addWidget(self.fslider)
        lay.addWidget(self.flabel)

        self.plot = XYPlot()
        lay.addWidget(self.plot, 1)
        self.out = QLabel("")
        self.out.setTextFormat(Qt.TextFormat.RichText)
        self.out.setWordWrap(True)
        self.out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.out)
        r = QHBoxLayout()
        b = QPushButton("Esporta grafico…")
        b.clicked.connect(self.export)
        r.addWidget(b)
        b = QPushButton("Copia risultati")
        b.clicked.connect(self.copy)
        r.addWidget(b)
        lay.addLayout(r)
        self._text = ""
        ws.network_added.connect(lambda *_: self.fill_table())
        ws.network_removed.connect(lambda *_: self.fill_table())
        self.fill_table()
        self._update_controls()

    # ------------------------------------------------------------------ input
    def load_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CSV sweep di potenza", "", "CSV/testo (*.csv *.txt *.dat);;Tutti (*)")
        if not path:
            return
        try:
            names, arr = cp.read_power_sweep(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "P1dB", str(e))
            return
        self._csv = (names, arr)
        self.csv_name.setText(path.replace("\\", "/").split("/")[-1])
        for c in (self.c_x, self.c_y):
            c.clear()
            for k, n in enumerate(names):
                c.addItem(n, k)
        # euristica: X = colonna con 'pin'/'power', Y = 'gain'/'pout'
        low = [n.lower() for n in names]
        for k, n in enumerate(low):
            if "pin" in n or "power" in n or "potenza" in n:
                self.c_x.setCurrentIndex(k)
                break
        for k, n in enumerate(low):
            if "gain" in n or "guad" in n or "s21" in n:
                self.c_y.setCurrentIndex(k)
                self.c_type.setCurrentIndex(0)
                break
            if "pout" in n or "out" in n:
                self.c_y.setCurrentIndex(k)
                self.c_type.setCurrentIndex(1)
        else:
            if self.c_y.count() > 1 and self.c_y.currentIndex() == self.c_x.currentIndex():
                self.c_y.setCurrentIndex(1)

    def fill_table(self) -> None:
        self.table.setRowCount(0)
        for e in self.ws.entries:
            if e.net.nports != 2:
                continue
            r = self.table.rowCount()
            self.table.insertRow(r)
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            pw = cp.power_from_name(e.net.name)
            it.setCheckState(Qt.CheckState.Checked if pw is not None else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, e.net.uid)
            self.table.setItem(r, 0, it)
            n = QTableWidgetItem(e.net.name)
            n.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, 1, n)
            self.table.setItem(r, 2, QTableWidgetItem("" if pw is None else f"{pw:g}"))
        self.table.resizeColumnToContents(0)

    # ------------------------------------------------------------------ calcolo
    def compute(self) -> None:
        self._vs = None
        self._sweep = None
        if self.src.currentData() == 0:
            self._compute_csv()
        else:
            self._compute_set()
        self._update_controls()
        self._redraw()

    def _compute_csv(self) -> None:
        if self._csv is None:
            self.message("Apri prima un CSV")
            return
        names, arr = self._csv
        x = arr[:, self.c_x.currentData()]
        y = arr[:, self.c_y.currentData()]
        g = y if self.c_type.currentData() == "gain" else cp.gain_from_pout(x, y)
        self._curve = (x, g)
        self._res = cp.p1db(x, g, self.n_ref.value(), self.drop.value())

    def _compute_set(self) -> None:
        nets, pw = [], []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it.checkState() != Qt.CheckState.Checked:
                continue
            try:
                pw.append(float(self.table.item(r, 2).text().replace(",", ".")))
            except ValueError:
                QMessageBox.warning(self, "P1dB", f"Potenza non valida per «{self.table.item(r, 1).text()}»")
                return
            nets.append(self.ws.network(it.data(Qt.ItemDataRole.UserRole)))
        try:
            self._sweep = cp.sweep_from_networks(nets, pw, *self.sij.currentData())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "P1dB", str(e))
            return
        self._vs = cp.p1db_vs_freq(self._sweep, self.n_ref.value(), self.drop.value())
        self.fslider.blockSignals(True)
        self.fslider.setRange(0, self._sweep.freq.size - 1)
        # frequenza di partenza: dove il guadagno small-signal è massimo
        self.fslider.setValue(int(np.nanargmax(self._vs[2])))
        self.fslider.blockSignals(False)

    def _update_controls(self) -> None:
        on = self._sweep is not None
        self.fslider.setVisible(on)
        self.flabel.setVisible(on)
        self.view.setEnabled(on)
        if not on:
            self.view.setCurrentIndex(0)

    # ------------------------------------------------------------------ disegno
    def _redraw(self, *_) -> None:
        pl = self.plot
        rows = []
        if self._sweep is not None and self._vs is not None:
            sw = self._sweep
            k = self.fslider.value()
            fk = sw.freq[k]
            self.flabel.setText(f"Frequenza: {fmt_eng(fk, 'Hz', 6)}")
            ip, op, gr = self._vs
            if self.view.currentData() == 0:
                res = cp.p1db(sw.power, sw.gain_db[:, k], self.n_ref.value(), self.drop.value())
                self._plot_curve(sw.power, sw.gain_db[:, k], res, f"Guadagno S{self.sij.currentText()[1:]} [dB] @ {fmt_eng(fk, 'Hz', 5)}")
                rows = self._result_rows(res, fk)
            else:
                pl.set_data(curves=[(sw.freq / 1e9, ip, "#FFFFFF", "IP1dB"), (sw.freq / 1e9, op, "#FFFF00", "OP1dB")],
                            hlines=[], vlines=[(fk / 1e9, "#FFFFFF", "")], points=[],
                            xlabel="Frequenza [GHz]", ylabel="P1dB [dBm]  (giallo: ingresso, ciano: uscita)")
                fin = np.isfinite(ip)
                rows = [("Frequenze con compressione", f"{int(fin.sum())} / {fin.size}")]
                if fin.any():
                    i = int(np.nanargmin(ip))
                    rows += [("IP1dB min", f"{ip[i]:.2f} dBm a {fmt_eng(sw.freq[i], 'Hz', 5)}"),
                             ("OP1dB min", f"{np.nanmin(op):.2f} dBm"), ("OP1dB max", f"{np.nanmax(op):.2f} dBm")]
        elif getattr(self, "_res", None) is not None and self.src.currentData() == 0 and self._csv is not None:
            x, g = self._curve
            self._plot_curve(x, g, self._res, "Guadagno [dB]")
            rows = self._result_rows(self._res)
        else:
            pl.set_data(curves=[], hlines=[], vlines=[], points=[])
        txt = "<table cellspacing=2>" + "".join(
            f"<tr><td style='color:#FFFFFF'>{a}</td><td>&nbsp;&nbsp;{b}</td></tr>" for a, b in rows) + "</table>"
        self.out.setText(txt)
        self._text = "\n".join(f"{a}\t{b}" for a, b in rows)

    def _plot_curve(self, pin, g, res: cp.P1dBResult, ylabel: str) -> None:
        o = np.argsort(pin)
        h = []
        pts = []
        if np.isfinite(res.g_ref):
            h = [(res.g_ref, "#FFFFFF", "riferimento small-signal"), (res.level, "#FF0000", f"−{self.drop.value():g} dB")]
        if res.found:
            pts = [(res.pin, res.level, "#FFFFFF", f"IP1dB {res.pin:.2f} dBm")]
        self.plot.set_data(curves=[(np.asarray(pin)[o], np.asarray(g)[o], "#FFFF00", "G")], hlines=h,
                           vlines=[(res.pin, "#FFFFFF", "")] if res.found else [], points=pts,
                           xlabel="Pin [dBm]", ylabel=ylabel)

    def _result_rows(self, r: cp.P1dBResult, f: Optional[float] = None) -> list:
        rows = []
        if f is not None:
            rows.append(("Frequenza", fmt_eng(f, "Hz", 6)))
        rows.append(("Guadagno small-signal", f"{r.g_ref:.3f} dB  (media primi {r.n_ref} punti)"))
        if r.found:
            rows += [(f"IP1dB (−{self.drop.value():g} dB)", f"<b style='color:#FFFF00'>{r.pin:.3f} dBm</b>"),
                     ("OP1dB", f"<b style='color:#FFFF00'>{r.pout:.3f} dBm</b>")]
        else:
            rows.append(("P1dB", f"non determinato — {r.note}"))
        return rows

    # ------------------------------------------------------------------ output
    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Esporta grafico P1dB", "p1db.png", "PNG (*.png)")
        if path:
            self.plot.render_image(1000, 620, LIGHT).save(path)
            self.message(f"Salvato {path}")

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self._text)
        self.message("Risultati copiati negli appunti")
