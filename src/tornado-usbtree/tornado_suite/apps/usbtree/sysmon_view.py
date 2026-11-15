"""Scheda "Sistema": monitor diagnostico con grafici (pyqtgraph)."""
from __future__ import annotations

import csv
import json
import math
import time
from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import sysmon

PALETTE = ["#4fc3f7", "#ef5350", "#66bb6a", "#ffa726", "#ab47bc", "#26c6da", "#d4e157", "#8d6e63"]
EMPTY = np.array([], dtype=float)
ALARM_DEFS = [  # chiave, etichetta, unita', soglia predefinita (0 = disattivata)
    ("cpu.temp", "Temperatura CPU", "°C", 90.0),
    ("gpu.temp", "Temperatura GPU", "°C", 85.0),
    ("storage.temp_max", "Temperatura disco", "°C", 70.0),
    ("ram.percent", "RAM usata", "%", 92.0),
    ("cpu.total", "CPU (sostenuto)", "%", 0.0),
    ("gpu.load", "Carico GPU (sostenuto)", "%", 0.0),
    ("disk.busy", "Attivita' disco", "%", 0.0),
]
OVERVIEW_PLOTS = [  # titolo, unita', [(chiave, nome)], intervallo Y fisso
    ("CPU", "%", [("cpu.total", "Totale")], (0, 100)),
    ("Temperature", "°C", [("cpu.temp", "CPU"), ("gpu.temp", "GPU"), ("storage.temp_max", "Disco")], None),
    ("Memoria", "%", [("ram.percent", "RAM"), ("swap.percent", "Swap")], (0, 100)),
    ("GPU", "%", [("gpu.load", "Carico"), ("gpu.mem_pct", "VRAM")], (0, 100)),
    ("Disco", "MB/s", [("disk.read_mbs", "Lettura"), ("disk.write_mbs", "Scrittura")], None),
    ("Rete", "MB/s", [("net.down_mbs", "Download"), ("net.up_mbs", "Upload")], None),
    ("Potenza", "W", [("cpu.power_w", "CPU"), ("gpu.power_w", "GPU")], None),
    ("Frequenza CPU", "MHz", [("cpu.freq_mhz", "CPU")], None),
]


# --------------------------------------------------------------------------
# Utilita'
# --------------------------------------------------------------------------
def human_bytes(n: float) -> str:
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return ""


def fmt_value(v: float, unit: str) -> str:
    if unit == "B/s":
        return human_bytes(v) + "/s"
    if unit == "s":
        v = int(v)
        return f"{v // 3600}h {v % 3600 // 60:02d}m"
    if unit in ("RPM", "MHz", "Hz"):
        return f"{v:.0f} {unit}"
    return f"{v:.2f} {unit}".strip() if abs(v) < 10 and unit in ("V", "A", "W") else f"{v:.1f} {unit}".strip()


class NumItem(QTableWidgetItem):
    def __init__(self, text: str, num: float) -> None:
        super().__init__(text)
        self.num = num
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other) -> bool:
        return self.num < getattr(other, "num", 0.0)


def make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    return t


class History:
    def __init__(self, maxlen: int = 3600) -> None:
        self.t: deque = deque(maxlen=maxlen)
        self.d: dict[str, deque] = {}

    def add(self, s: sysmon.Sample) -> None:
        self.t.append(s.t)
        for k in set(s.m) | set(self.d):
            q = self.d.get(k)
            if q is None:
                q = self.d[k] = deque([float("nan")] * (len(self.t) - 1), maxlen=self.t.maxlen)
            q.append(s.m.get(k, float("nan")))

    def series(self, key: str, window: float):
        q = self.d.get(key)
        if q is None or not self.t:
            return EMPTY, EMPTY
        t = np.fromiter(self.t, float, len(self.t))
        y = np.fromiter(q, float, len(q))
        x = t - t[-1]
        i = int(np.searchsorted(x, -window))
        return x[i:], y[i:]


def _style_plot(p: pg.PlotWidget, title: str) -> None:
    pal = QApplication.palette()
    fg = pal.color(QPalette.Text)
    p.setBackground(pal.color(QPalette.Base))
    for ax in ("left", "bottom"):
        a = p.getAxis(ax)
        a.setPen(fg)
        a.setTextPen(fg)
    p.setTitle(title, color=fg, size="10pt")


def _nice(v: float) -> float:
    if not v > 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 3, 5, 7.5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


class HistPlot(pg.PlotWidget):
    def __init__(self, title: str, unit: str, series: list, yrange=None) -> None:
        super().__init__()
        self.title_text, self.series, self.yrange, self.unit = title, series, yrange, unit
        self._yr = None
        self._xw = None
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.showGrid(x=True, y=True, alpha=0.25)
        self.setLabel("left", unit)
        self.getAxis("left").setWidth(54)
        self.setMinimumHeight(170)
        if len(series) > 1:
            self.addLegend(offset=(8, 4))
        self.curves = [self.plot([], [], pen=pg.mkPen(PALETTE[i % len(PALETTE)], width=2), name=n)
                       for i, (_, n) in enumerate(series)]
        for c in self.curves:
            c.setClipToView(True)
            c.setDownsampling(auto=True, method="peak")
        # scala Y gestita a mano (niente autorange a ogni campione: costoso e "a scatti")
        self.enableAutoRange(axis="y", enable=False)
        self.enableAutoRange(axis="x", enable=False)
        self._set_y(*(yrange or (0, 1)))
        self.restyle()

    def restyle(self) -> None:
        _style_plot(self, self.title_text)

    def _set_y(self, lo: float, hi: float) -> None:
        if self._yr != (lo, hi):
            self._yr = (lo, hi)
            self.setYRange(lo, hi, padding=0)

    def refresh(self, hist: History, window: float) -> None:
        lo = hi = None
        for (key, _), c in zip(self.series, self.curves):
            x, y = hist.series(key, window)
            c.setData(x, y, connect="finite")
            if len(y):
                f = y[np.isfinite(y)]
                if len(f):
                    mn, mx = float(f.min()), float(f.max())
                    lo = mn if lo is None else min(lo, mn)
                    hi = mx if hi is None else max(hi, mx)
        if not self.yrange and hi is not None:
            if self.unit == "°C":
                bot = max(0.0, math.floor((lo - 5) / 10) * 10)
                top = max(math.ceil((hi + 5) / 10) * 10, bot + 20)
                self._set_y(bot, top)
            else:
                self._set_y(0, _nice(hi * 1.1))
        if self._xw != window:
            self._xw = window
            self.setXRange(-window, 0, padding=0)


class BarPlot(pg.PlotWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.title_text = title
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setYRange(0, 100, padding=0)
        self.enableAutoRange(axis="y", enable=False)
        self.showGrid(y=True, alpha=0.25)
        self.setLabel("left", "%")
        self.getAxis("left").setWidth(54)
        self.setMinimumHeight(170)
        self.bars = pg.BarGraphItem(x=[0], height=[0], width=0.7, brush="#4fc3f7")
        self.addItem(self.bars)
        self.restyle()

    def restyle(self) -> None:
        _style_plot(self, self.title_text)

    def update_cores(self, cores: list) -> None:
        n = len(cores)
        if not n:
            return
        brushes = [pg.mkBrush("#66bb6a" if c < 50 else "#ffa726" if c < 80 else "#ef5350")
                   for c in cores]
        self.bars.setOpts(x=np.arange(n), height=np.array(cores, dtype=float), width=0.7,
                          brushes=brushes)
        self.setXRange(-0.7, n - 0.3, padding=0)


class Card(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        self.t = QLabel(title)
        self.t.setStyleSheet("color: gray; font-size: 11px;")
        self.v = QLabel("n/d")
        f = self.v.font()
        f.setPointSize(16)
        f.setBold(True)
        self.v.setFont(f)
        lay.addWidget(self.t)
        lay.addWidget(self.v)

    def set(self, text: str, level: int = 0) -> None:
        self.v.setText(text)
        self.v.setStyleSheet({0: "", 1: "color:#e6a01e;", 2: "color:#e15050;"}[level])


def plot_grid(specs: list, columns: int = 2):
    host = QWidget()
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    plots = []
    for i, (title, unit, series, yr) in enumerate(specs):
        p = HistPlot(title, unit, series, yr)
        plots.append(p)
        grid.addWidget(p, i // columns, i % columns)
    return host, plots


def scrolled(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(widget)
    return sa


# --------------------------------------------------------------------------
# Pagine
# --------------------------------------------------------------------------
def _txt(m: dict, key: str, fmt: str) -> str:
    v = m.get(key)
    return fmt.format(v) if v is not None and v == v else "n/d"


class OverviewPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.card_defs = [
            ("CPU", "cpu.total", lambda m: _txt(m, "cpu.total", "{:.0f} %")),
            ("Temp. CPU", "cpu.temp", lambda m: _txt(m, "cpu.temp", "{:.0f} °C")),
            ("Potenza CPU", "cpu.power_w", lambda m: _txt(m, "cpu.power_w", "{:.1f} W")),
            ("RAM", "ram.percent", lambda m: _txt(m, "ram.percent", "{:.0f} %")),
            ("Ventola max", "fan.max_rpm", lambda m: _txt(m, "fan.max_rpm", "{:.0f} RPM")),
            ("GPU", "gpu.load", lambda m: _txt(m, "gpu.load", "{:.0f} %")),
            ("Temp. GPU", "gpu.temp", lambda m: _txt(m, "gpu.temp", "{:.0f} °C")),
            ("Disco", "disk.busy", lambda m: _txt(m, "disk.total_mbs", "{:.1f} MB/s")),
            ("Rete", "", lambda m: (f"↓ {m['net.down_mbs']:.2f}  ↑ {m['net.up_mbs']:.2f} MB/s"
                                    if "net.down_mbs" in m else "n/d")),
            ("Batteria", "", lambda m: (f"{m['battery.percent']:.0f} %"
                                        + ("  (rete)" if m.get("battery.plugged") else "  (batteria)")
                                        if "battery.percent" in m else "n/d")),
        ]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        cards = QGridLayout()
        self.cards = []
        for i, (title, *_rest) in enumerate(self.card_defs):
            c = Card(title)
            self.cards.append(c)
            cards.addWidget(c, i // 5, i % 5)
        lay.addLayout(cards)
        host, self.plots = plot_grid(OVERVIEW_PLOTS)
        lay.addWidget(scrolled(host), 1)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        m = w.last.m if w.last else {}
        for c, (_, key, fn) in zip(self.cards, self.card_defs):
            c.set(fn(m), w.alarm_level(key, m))
        for p in self.plots:
            p.refresh(w.hist, w.window_s)


class SensorTree(QWidget):
    def __init__(self, group_filter: str | None = None, search: bool = True) -> None:
        super().__init__()
        self.group_filter = group_filter
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(placeholderText="Filtra sensori...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply)
        if search:
            lay.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Sensore", "Valore", "Min", "Max"])
        self.tree.setColumnWidth(0, 340)
        self.tree.setAlternatingRowColors(True)
        lay.addWidget(self.tree)
        self.groups: dict = {}
        self.items: dict = {}
        self.minmax: dict = {}

    def refresh(self, sensors: list) -> None:
        for grp, kind, name, val, unit in sensors:
            if self.group_filter and self.group_filter not in grp:
                continue
            gi = self.groups.get(grp)
            if gi is None:
                gi = QTreeWidgetItem(self.tree, [grp])
                f = gi.font(0)
                f.setBold(True)
                gi.setFont(0, f)
                gi.setExpanded(True)
                self.groups[grp] = gi
            key = (grp, kind, name)
            it = self.items.get(key)
            if it is None:
                it = QTreeWidgetItem(gi, [f"{name}   ({kind})", "", "", ""])
                self.items[key] = it
            lo, hi = self.minmax.get(key, (val, val))
            lo, hi = min(lo, val), max(hi, val)
            self.minmax[key] = (lo, hi)
            it.setText(1, fmt_value(val, unit))
            it.setText(2, fmt_value(lo, unit))
            it.setText(3, fmt_value(hi, unit))
            for c in (1, 2, 3):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        if self.search.text():
            self._apply()

    def _apply(self) -> None:
        text = self.search.text().strip().lower()
        for gi in self.groups.values():
            any_vis = False
            for i in range(gi.childCount()):
                ch = gi.child(i)
                vis = not text or text in ch.text(0).lower() or text in gi.text(0).lower()
                ch.setHidden(not vis)
                any_vis = any_vis or vis
            gi.setHidden(not any_vis)


class CpuPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bars = BarPlot("Carico per core logico")
        self.bars.setMaximumHeight(220)
        lay.addWidget(self.bars)
        host, self.plots = plot_grid([
            ("Carico totale", "%", [("cpu.total", "CPU")], (0, 100)),
            ("Frequenza", "MHz", [("cpu.freq_mhz", "CPU")], None),
            ("Temperatura", "°C", [("cpu.temp", "CPU")], None),
            ("Potenza", "W", [("cpu.power_w", "CPU Package")], None),
        ])
        lay.addWidget(host)
        self.sensors = SensorTree("[Cpu]", search=False)
        lay.addWidget(self.sensors, 1)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        if w.last:
            self.bars.update_cores(w.last.cores)
            self.sensors.refresh(w.last.sensors)
        for p in self.plots:
            p.refresh(w.hist, w.window_s)


class MemoryPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.info = QLabel("")
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        f = self.info.font()
        f.setPointSize(11)
        self.info.setFont(f)
        lay.addWidget(self.info)
        host, self.plots = plot_grid([
            ("RAM e swap", "%", [("ram.percent", "RAM"), ("swap.percent", "Swap")], (0, 100)),
            ("RAM usata", "GB", [("ram.used_gb", "Usata")], None),
        ])
        lay.addWidget(host, 1)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        r = w.last.ram if w.last else {}
        if r:
            used = r["total"] - r["available"]
            self.info.setText(
                f"<b>RAM</b>: {human_bytes(used)} usati di {human_bytes(r['total'])} "
                f"({r['percent']:.0f} %) &nbsp;|&nbsp; disponibili {human_bytes(r['available'])}<br>"
                f"<b>Swap / file di paging</b>: {human_bytes(r['swap_used'])} usati di "
                f"{human_bytes(r['swap_total'])}")
        for p in self.plots:
            p.refresh(w.hist, w.window_s)


class GpuPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.host = QWidget()
        self.box = QVBoxLayout(self.host)
        self.outer.addWidget(scrolled(self.host))
        self.n = -1
        self.groups: list = []

    def _rebuild(self, gpus: list) -> None:
        while self.box.count():
            it = self.box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.groups = []
        if not gpus:
            self.box.addWidget(QLabel(
                "Nessuna GPU rilevata.\nNVIDIA: pip install nvidia-ml-py   |   "
                "Intel/AMD: serve LibreHardwareMonitor (menu Sistema)."))
            self.box.addStretch(1)
            return
        for i, g in enumerate(gpus):
            gb = QGroupBox(f"{g['name']}  [{g['src']}]")
            v = QVBoxLayout(gb)
            info = QLabel("")
            info.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(info)
            k = f"gpu{i}"
            host, plots = plot_grid([
                ("Carico / VRAM", "%", [(f"{k}.load", "Carico"), (f"{k}.mem_pct", "VRAM")], (0, 100)),
                ("Temperatura", "°C", [(f"{k}.temp", "GPU")], None),
                ("Potenza", "W", [(f"{k}.power", "GPU")], None),
                ("Clock", "MHz", [(f"{k}.clock", "Core"), (f"{k}.mem_clock", "Memoria")], None),
            ])
            v.addWidget(host)
            self.box.addWidget(gb)
            self.groups.append((info, plots))
        self.box.addStretch(1)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        gpus = w.last.gpus if w.last else []
        if len(gpus) != self.n:
            self.n = len(gpus)
            self._rebuild(gpus)
        for g, (info, plots) in zip(gpus, self.groups):
            parts = [f"Carico {g['load']:.0f} %" if g.get("load") is not None else None,
                     (f"VRAM {g['mem_used']:.0f}/{g['mem_total']:.0f} MB"
                      if g.get("mem_total") and g.get("mem_used") is not None
                      else f"Mem. condivisa {g['mem_used']:.0f} MB" if g.get("mem_used") is not None
                      else None),
                     f"{g['temp']:.0f} °C" if g.get("temp") is not None else None,
                     (f"{g['power']:.0f} W" + (f" / {g['power_limit']:.0f} W" if g.get("power_limit") else "")
                      if g.get("power") is not None else None),
                     f"Clock {g['clock']:.0f} MHz" if g.get("clock") is not None else None,
                     f"Ventola {g['fan']:.0f} %" if g.get("fan") is not None else None]
            txt = "  |  ".join(p for p in parts if p)
            if g.get("throttle"):
                txt += f"<br><span style='color:#e6a01e'>Limitazione attiva: {g['throttle']}</span>"
            info.setText(txt or "nessun dato (sensori non disponibili per questa GPU)")
            for p in plots:
                p.refresh(w.hist, w.window_s)


class DiskPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.parts = make_table(["Unita'", "Mount", "File system", "Usato", "Totale", "Uso %"])
        self.parts.setMaximumHeight(190)
        self.io = make_table(["Disco fisico", "Lettura MB/s", "Scrittura MB/s", "Attivita' %", "IOPS"])
        self.io.setMaximumHeight(150)
        lay.addWidget(self.parts)
        lay.addWidget(self.io)
        host, self.plots = plot_grid([
            ("Throughput", "MB/s", [("disk.read_mbs", "Lettura"), ("disk.write_mbs", "Scrittura")], None),
            ("Attivita' disco", "%", [("disk.busy", "Attivita'")], (0, 100)),
        ])
        lay.addWidget(host, 1)

    @staticmethod
    def _fill(t: QTableWidget, rows: list) -> None:
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                t.setItem(r, c, cell if isinstance(cell, QTableWidgetItem) else QTableWidgetItem(str(cell)))
        t.setSortingEnabled(True)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        s = w.last
        if s:
            self._fill(self.parts, [
                [p["dev"], p["mount"], p["fs"], NumItem(human_bytes(p["used"]), p["used"]),
                 NumItem(human_bytes(p["total"]), p["total"]), NumItem(f"{p['percent']:.0f} %", p["percent"])]
                for p in s.parts])
            self._fill(self.io, [
                [d["name"], NumItem(f"{d['read']:.2f}", d["read"]), NumItem(f"{d['write']:.2f}", d["write"]),
                 NumItem(f"{d['busy']:.0f}", d["busy"]), NumItem(f"{d['iops']:.0f}", d["iops"])]
                for d in s.dio])
        for p in self.plots:
            p.refresh(w.hist, w.window_s)


class NetPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.t = make_table(["Interfaccia", "Stato", "Velocita'", "Download MB/s", "Upload MB/s",
                             "Ricevuti", "Inviati", "Errori", "Scartati"])
        self.t.setMaximumHeight(260)
        lay.addWidget(self.t)
        host, self.plots = plot_grid([
            ("Throughput totale", "MB/s", [("net.down_mbs", "Download"), ("net.up_mbs", "Upload")], None)],
            columns=1)
        lay.addWidget(host, 1)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        if w.last:
            DiskPage._fill(self.t, [
                [n["name"], "attiva" if n["up"] else "giu'", f"{n['speed']} Mbit/s" if n["speed"] else "-",
                 NumItem(f"{n['down']:.3f}", n["down"]), NumItem(f"{n['upl']:.3f}", n["upl"]),
                 NumItem(human_bytes(n["rx"]), n["rx"]), NumItem(human_bytes(n["tx"]), n["tx"]),
                 NumItem(str(n["errin"] + n["errout"]), n["errin"] + n["errout"]),
                 NumItem(str(n["dropin"] + n["dropout"]), n["dropin"] + n["dropout"])]
                for n in w.last.nics])
        for p in self.plots:
            p.refresh(w.hist, w.window_s)


class SensorsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tree = SensorTree()
        lay.addWidget(self.tree)

    def refresh(self, w: "SystemMonitorWidget") -> None:
        if w.last:
            self.tree.refresh(w.last.sensors)


class ProcPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.t = make_table(["PID", "Processo", "CPU %", "Memoria MB", "Thread"])
        self.t.setColumnWidth(1, 280)
        lay.addWidget(self.t)
        self.t.sortByColumn(2, Qt.DescendingOrder)
        self._stamp = None

    def refresh(self, w: "SystemMonitorWidget") -> None:
        procs = w.procs
        if not procs or id(procs) == self._stamp:
            return
        self._stamp = id(procs)
        DiskPage._fill(self.t, [
            [NumItem(str(p[0]), p[0]), p[1], NumItem(f"{p[2]:.1f}", p[2]),
             NumItem(f"{p[3]:.0f}", p[3]), NumItem(str(p[4]), p[4])] for p in procs[:150]])


class InfoPage(QWidget):
    def __init__(self, w: "SystemMonitorWidget") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        b1 = QPushButton("Scarica / aggiorna LibreHardwareMonitor")
        b1.clicked.connect(w.download_lhm)
        b2 = QPushButton("Riavvia come amministratore")
        b2.clicked.connect(w.request_admin)
        b2.setEnabled(not sysmon.is_admin())
        bar.addWidget(b1)
        bar.addWidget(b2)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.t = make_table(["Voce", "Valore"])
        self.t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        lay.addWidget(self.t)
        self.static = sysmon.system_info()
        self._last = None

    def refresh(self, w: "SystemMonitorWidget") -> None:
        status = w.last.status if w.last else {}
        sig = tuple(sorted((k, v[0]) for k, v in status.items()))
        if sig == self._last:
            return
        self._last = sig
        rows = list(self.static) + [("--- Sorgenti dati ---", "")]
        rows += [(k, ("OK  " if ok else "NO  ") + txt) for k, (txt, ok) in status.items()]
        if status.get("LibreHardwareMonitor", ("", False))[1] and "cpu.temp" not in (w.last.m if w.last else {}):
            rows.append(("Suggerimento", "Temperatura CPU assente: avvia come amministratore; con le "
                         "versioni recenti di LibreHardwareMonitor installa anche il driver PawnIO "
                         "(winget install namazso.PawnIO)."))
        DiskPage._fill(self.t, [[a, b] for a, b in rows])


# --------------------------------------------------------------------------
# Thread di raccolta
# --------------------------------------------------------------------------
class CollectorThread(QThread):
    sample = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._stop = False
        self.paused = False
        self.want_procs = False

    def run(self) -> None:
        col = sysmon.Collector()
        try:
            col.start()
        except Exception as e:  # noqa: BLE001
            col.status["collector"] = (f"errore: {e}", False)
        while not self._stop:
            t0 = time.time()
            if not self.paused:
                col.want_procs = self.want_procs
                try:
                    self.sample.emit(col.sample())
                except Exception as e:  # noqa: BLE001
                    self.sample.emit(e)
            while not self._stop and time.time() - t0 < 1.0:
                self.msleep(50)
        col.close()

    def stop(self) -> None:
        self._stop = True
        self.wait(10000)


class _Job(QThread):
    done = Signal(object, object)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


class AlarmDialog(QDialog):
    def __init__(self, parent, values: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Soglie di allarme")
        form = QFormLayout(self)
        self.spins = {}
        for key, label, unit, _ in ALARM_DEFS:
            sp = QDoubleSpinBox()
            sp.setRange(0, 1000)
            sp.setDecimals(0)
            sp.setSuffix(f" {unit}")
            sp.setSpecialValueText("disattivato")
            sp.setValue(values.get(key, 0.0))
            self.spins[key] = sp
            form.addRow(label, sp)
        form.addRow(QLabel("L'allarme scatta dopo 3 campioni consecutivi sopra soglia."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self) -> dict:
        return {k: s.value() for k, s in self.spins.items()}


# --------------------------------------------------------------------------
# Widget principale
# --------------------------------------------------------------------------
class SystemMonitorWidget(QWidget):
    message = Signal(str)

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("usbtree", "usbtree")
        pal = QApplication.palette()
        pg.setConfigOptions(antialias=False, background=pal.color(QPalette.Base),
                            foreground=pal.color(QPalette.Text))
        self.hist = History()
        self.window_s = 300
        self.last: sysmon.Sample | None = None
        self.procs: list | None = None
        self.thread: CollectorThread | None = None
        self.csv_f = None
        self.csv_w = None
        self._jobs: set = set()
        self._cnt: dict = {}
        self._on: set = set()
        self.alarms = self._load_alarms()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Finestra:"))
        self.win = QComboBox()
        for txt, sec in (("1 minuto", 60), ("5 minuti", 300), ("15 minuti", 900), ("60 minuti", 3600)):
            self.win.addItem(txt, sec)
        self.win.setCurrentIndex(1)
        self.win.currentIndexChanged.connect(self._win_changed)
        bar.addWidget(self.win)
        self.btn_pause = QPushButton("Pausa")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._pause)
        self.btn_csv = QPushButton("Registra CSV")
        self.btn_csv.setCheckable(True)
        self.btn_csv.toggled.connect(self._toggle_csv)
        btn_al = QPushButton("Soglie...")
        btn_al.clicked.connect(self.edit_alarms)
        for b in (self.btn_pause, self.btn_csv, btn_al):
            bar.addWidget(b)
        self.alarm_lbl = QLabel("")
        self.alarm_lbl.setStyleSheet("color:#e15050; font-weight:bold;")
        bar.addWidget(self.alarm_lbl, 1)
        self.status_lbl = QLabel("avvio...")
        bar.addWidget(self.status_lbl)
        lay.addLayout(bar)

        self.tabs = QTabWidget()
        self.pages = [("Panoramica", OverviewPage()), ("CPU", CpuPage()), ("Memoria", MemoryPage()),
                      ("GPU", GpuPage()), ("Dischi", DiskPage()), ("Rete", NetPage()),
                      ("Sensori", SensorsPage()), ("Processi", ProcPage()),
                      ("Informazioni", InfoPage(self))]
        for name, page in self.pages:
            self.tabs.addTab(page, name)
        self.tabs.currentChanged.connect(self._page_changed)
        lay.addWidget(self.tabs, 1)
        self.start_collector()

    # ---- raccolta ---------------------------------------------------------
    def start_collector(self) -> None:
        self.thread = CollectorThread()
        self.thread.paused = self.btn_pause.isChecked()
        self.thread.want_procs = isinstance(self.tabs.currentWidget(), ProcPage)
        self.thread.sample.connect(self._on_sample)
        self.thread.start()

    def restart_collector(self) -> None:
        if self.thread:
            self.thread.stop()
        self.start_collector()

    def shutdown(self) -> None:
        if self.thread:
            self.thread.stop()
            self.thread = None
        self._close_csv()

    def _on_sample(self, obj) -> None:
        if isinstance(obj, Exception):
            self.status_lbl.setText(f"errore: {obj}")
            return
        s: sysmon.Sample = obj
        self.last = s
        if s.procs is not None:
            self.procs = s.procs
        self.hist.add(s)
        self._check_alarms(s)
        self._write_csv(s)
        bad = [k for k, (_, ok) in s.status.items() if not ok]
        if not bad:
            self.status_lbl.setText("sorgenti: tutte ok")
        else:
            why = s.status[bad[0]][0]
            txt = f"{bad[0]}: {why}"
            self.status_lbl.setText(txt if len(txt) <= 80 else txt[:77] + "...")
        self.status_lbl.setToolTip("\n".join(f"{k}: {t}" for k, (t, _) in s.status.items()))
        self._refresh_current()

    def _page_changed(self, _i: int) -> None:
        if self.thread:
            self.thread.want_procs = isinstance(self.tabs.currentWidget(), ProcPage)
        self._refresh_current()

    def _refresh_current(self) -> None:
        page = self.tabs.currentWidget()
        if self.last is not None and hasattr(page, "refresh"):
            page.refresh(self)

    def _win_changed(self) -> None:
        self.window_s = self.win.currentData()
        self._refresh_current()

    def _pause(self, on: bool) -> None:
        if self.thread:
            self.thread.paused = on
        self.btn_pause.setText("Riprendi" if on else "Pausa")

    # ---- soglie -------------------------------------------------------------
    def _load_alarms(self) -> dict:
        d = {k: v for k, _, _, v in ALARM_DEFS}
        try:
            d.update(json.loads(self.settings.value("sys/alarms", "{}")))
        except (TypeError, ValueError):
            pass
        return d

    def edit_alarms(self) -> None:
        dlg = AlarmDialog(self, self.alarms)
        if dlg.exec():
            self.alarms = dlg.values()
            self.settings.setValue("sys/alarms", json.dumps(self.alarms))
            self._cnt.clear()
            self._on.clear()

    def alarm_level(self, key: str, m: dict) -> int:
        lim, v = self.alarms.get(key, 0), m.get(key)
        if not lim or v is None or v != v:
            return 0
        return 2 if v > lim else 1 if v > 0.85 * lim else 0

    def _check_alarms(self, s: sysmon.Sample) -> None:
        for key, label, unit, _ in ALARM_DEFS:
            lim, v = self.alarms.get(key, 0), s.m.get(key)
            if not lim or v is None or v != v:
                self._cnt[key] = 0
                continue
            if v > lim:
                self._cnt[key] = self._cnt.get(key, 0) + 1
                if self._cnt[key] >= 3 and key not in self._on:
                    self._on.add(key)
                    msg = f"ALLARME {label}: {v:.0f} {unit} (soglia {lim:.0f})"
                    self.alarm_lbl.setText(msg)
                    self.message.emit(msg)
            else:
                self._cnt[key] = 0
                if key in self._on and v < lim - 2:
                    self._on.discard(key)
                    self.message.emit(f"rientrato: {label} = {v:.0f} {unit}")
                    if not self._on:
                        self.alarm_lbl.setText("")

    # ---- CSV ------------------------------------------------------------------
    def _toggle_csv(self, on: bool) -> None:
        if not on:
            self._close_csv()
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Registra su CSV", time.strftime("sysmon_%Y%m%d_%H%M%S.csv"), "CSV (*.csv)")
        if not path:
            self.btn_csv.blockSignals(True)
            self.btn_csv.setChecked(False)
            self.btn_csv.blockSignals(False)
            return
        self.csv_f = open(path, "w", newline="", encoding="utf-8")
        self.csv_w = None
        self.btn_csv.setText("Stop CSV")

    def _close_csv(self) -> None:
        if self.csv_f:
            self.csv_f.close()
        self.csv_f = self.csv_w = None
        self.btn_csv.setText("Registra CSV")

    def _write_csv(self, s: sysmon.Sample) -> None:
        if not self.csv_f:
            return
        if self.csv_w is None:
            self.csv_keys = sorted(s.m)
            self.csv_w = csv.writer(self.csv_f)
            self.csv_w.writerow(["timestamp"] + self.csv_keys)
        self.csv_w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s.t))]
                            + [f"{s.m[k]:.3f}" if k in s.m else "" for k in self.csv_keys])
        self.csv_f.flush()

    # ---- amministratore / LibreHardwareMonitor -----------------------------------
    def request_admin(self) -> None:
        if sysmon.is_admin():
            QMessageBox.information(self, "Amministratore", "Il programma e' gia' in esecuzione come amministratore.")
            return
        if sysmon.relaunch_as_admin():
            self.window().close()
        else:
            QMessageBox.warning(self, "Amministratore", "Elevazione annullata o non riuscita.")

    def download_lhm(self) -> None:
        if not sysmon.have_pythonnet():
            QMessageBox.warning(self, "LibreHardwareMonitor",
                                "Serve pythonnet:\n\n    pip install pythonnet\n\npoi riapri il programma.")
            return
        self.status_lbl.setText("download LibreHardwareMonitor (NuGet/GitHub)...")
        job = _Job(sysmon.install_lhm)
        job.done.connect(self._lhm_done)
        self._jobs.add(job)
        job.start()

    def install_pawnio(self) -> None:
        if not sysmon.is_admin():
            QMessageBox.warning(self, "PawnIO", "Serve l'avvio come amministratore (menu Sistema > Riavvia come amministratore).")
            return
        self.status_lbl.setText("installazione PawnIO (winget)...")
        job = _Job(sysmon.install_pawnio)
        job.done.connect(self._pawnio_done)
        self._jobs.add(job)
        job.start()

    def _pawnio_done(self, _res, err) -> None:
        job = self.sender()
        if isinstance(job, _Job):
            job.wait()
            self._jobs.discard(job)
        if err is not None:
            QMessageBox.warning(self, "PawnIO", f"Installazione non riuscita:\n{err}\n\n"
                                "Manuale: scarica PawnIO da pawnio.eu (o 'winget install namazso.PawnIO' da terminale).")
            return
        self.message.emit("PawnIO installato")
        QMessageBox.information(self, "PawnIO", "Installato. Riavvia il programma (come amministratore) per leggere temperatura e potenza CPU.")

    def _lhm_done(self, version, err) -> None:
        job = self.sender()
        if isinstance(job, _Job):
            job.wait()
            self._jobs.discard(job)
        if err is not None:
            QMessageBox.warning(self, "LibreHardwareMonitor", f"Download non riuscito:\n{err}")
            return
        self.message.emit(f"LibreHardwareMonitor {version} installato in {sysmon.lhm_dir()}")
        self.restart_collector()

    def maybe_prompt(self) -> None:
        """Al primo avvio propone amministratore e download dei sensori."""
        s = self.settings
        if not sysmon.is_admin() and not s.value("sys/asked_admin", False, bool):
            s.setValue("sys/asked_admin", True)
            r = QMessageBox.question(
                self, "Monitor di sistema",
                "Per leggere temperature, ventole e potenze serve eseguire il programma come "
                "amministratore.\n\nRiavviare ora con privilegi elevati?")
            if r == QMessageBox.Yes:
                self.request_admin()
                return
        if sysmon.have_pythonnet() and sysmon.find_lhm() is None and not s.value("sys/asked_lhm", False, bool):
            s.setValue("sys/asked_lhm", True)
            r = QMessageBox.question(
                self, "Monitor di sistema",
                "Per i sensori (temperature CPU/SSD, ventole, GPU Intel) serve LibreHardwareMonitor.\n\n"
                "Scaricarlo ora da GitHub (pochi MB)?")
            if r == QMessageBox.Yes:
                self.download_lhm()

    # ---- menu / export -------------------------------------------------------------
    def show_status(self) -> None:
        st = self.last.status if self.last else {}
        box = QMessageBox(QMessageBox.Information, "Stato sorgenti dati",
                          "\n\n".join(f"{k}: {'OK' if ok else 'NO'}\n    {t}" for k, (t, ok) in st.items())
                          or "nessun campione ancora", QMessageBox.Ok, self)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.setInformativeText("Dettagli completi in %LOCALAPPDATA%\\usbtree\\sysmon.log")
        box.exec()

    def fill_menu(self, menu: QMenu) -> None:
        menu.addAction("Pausa / riprendi", self.btn_pause.toggle)
        menu.addAction("Registra su CSV...", self.btn_csv.toggle)
        menu.addAction("Soglie di allarme...", self.edit_alarms)
        menu.addSeparator()
        menu.addAction("Esporta snapshot diagnostico...", self.export_snapshot)
        menu.addSeparator()
        menu.addAction("Stato sorgenti dati...", self.show_status)
        menu.addAction("Scarica LibreHardwareMonitor", self.download_lhm)
        menu.addAction("Installa driver PawnIO (temp./potenza CPU)", self.install_pawnio)
        menu.addAction("Riavvia come amministratore", self.request_admin)

    def export_snapshot(self) -> None:
        if self.last is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Snapshot diagnostico", time.strftime("snapshot_%Y%m%d_%H%M%S.txt"), "Testo (*.txt)")
        if not path:
            return
        s = self.last
        out = [f"{k}: {v}" for k, v in sysmon.system_info()]
        out += ["", "== Sorgenti dati =="] + [f"{k}: {t}" for k, (t, _) in s.status.items()]
        out += ["", "== Metriche =="] + [f"{k}: {v:.2f}" for k, v in sorted(s.m.items())]
        out += ["", "== Sensori =="] + [f"{g} | {n} ({k}): {fmt_value(v, u)}" for g, k, n, v, u in s.sensors]
        out += ["", "== Processi (top) =="] + [f"{p[1]} (pid {p[0]}): cpu {p[2]:.1f}% mem {p[3]:.0f} MB"
                                                 for p in (self.procs or [])[:25]]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))

    def restyle(self) -> None:
        pal = QApplication.palette()
        pg.setConfigOptions(background=pal.color(QPalette.Base), foreground=pal.color(QPalette.Text))
        for p in self.findChildren(pg.PlotWidget):
            if hasattr(p, "restyle"):
                p.restyle()
