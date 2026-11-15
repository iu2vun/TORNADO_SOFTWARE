"""Finestra principale TornadoVNA — stile Tornado Suite (ribbon + pannello a card + grafici).

Area di lavoro: griglia di DiagramWidget (fino a 4). Ogni diagramma ha le proprie trace (misura, formato,
scala fissa, marker, maschera). Pannello sinistro: stimolo e trace attiva. Ribbon: acquisizione, tracce,
marker, analisi/maschera, calibrazione, file.
"""
import json

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QAbstractItemView, QHeaderView, QInputDialog, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tornado_ui import Card, P, Ribbon, dim, h2, icon, register_icons

from . import analysis
from .branding import app_icon, bolt_pixmap, logo_pixmap
from .cal_dialog import CalibrationDialog
from .const import Z0
from .diagram import DiagramWidget
from .export import write_csv, write_touchstone
from .formats import FORMATS, FORMAT_MENU_ORDER, scalar_str, transform
from .mask import MASK_FORMATS, Mask, MaskDialog
from .measure import MEAS_MENU, compute, default_format, meas_label
from .trace import Diagram, Marker, Trace, default_layout, new_trace
from .status import StatusCard, StatusDialog, StatusMonitor
from .esp_link import EspLink, EspDialog
from .ui_widgets import fmt_freq, parse_freq
from .worker import VnaWorker

register_icons({
    "stop":   [("R", 6, 6, 12, 12)],
    "pause":  [("R", 6, 5, 4, 14), ("R", 14, 5, 4, 14)],
    "single": [("p", [(7, 4), (7, 20), (17, 12)]), ("l", [(19, 4), (19, 20)])],
    "bolt":   [("p", [(14, 2), (5, 13.5), (11, 13.5), (9.5, 22), (19, 9.5), (12.5, 9.5)])],
    "peak":   [("l", [(3, 19), (9, 7), (13, 14), (16, 10), (21, 19)])],
    "delta":  [("l", [(12, 4), (20, 19), (4, 19), (12, 4)])],
})

IFBW_VALUES = [10, 30, 100, 300, 1000, 3000, 10000, 30000, 50000]


def _fmt_hz(v):
    return f"{v/1e3:g} kHz" if v < 1e6 else f"{v/1e6:g} MHz"


class MainWindow(QMainWindow):
    _cfgSig = Signal(dict)
    _connSig = Signal(str, int, bool)
    _runningSig = Signal(bool)
    _singleSig = Signal()
    _statusModeSig = Signal(str)

    MAX_DIAGRAMS = 4

    def __init__(self, host, port, demo, worker_class=None):
        super().__init__()
        self.setWindowTitle("TornadoVNA — Tornado Laboratori")
        self.setWindowIcon(app_icon())
        self.resize(1500, 880)
        self._host, self._port = host, port
        self._worker_class = worker_class

        self._p = dict(start=1e8, stop=6e9, points=501, ifbw=10000, level=-10, avg=1)
        self._diagrams = default_layout()
        self._active_diag = 0
        self._diag_widgets = []
        self._last_f = None
        self._last_t = {}
        self._rate = 0.0
        self._running = True
        self._search_mode = "min"
        self._syncing = False

        pg.setConfigOptions(antialias=False)
        self._mon = StatusMonitor(self)
        self._conn_ok = False
        self._esp = EspLink(self._esp_state, self)
        self._esp.command.connect(self._esp_command)
        self._esp_dlg = None
        self._status_dlg = None
        self._build_ui()
        self._rebuild_grid()
        self._start_worker(demo)
        self._push_config()
        self._sync_panel()

        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_ribbon())

        split = QSplitter(Qt.Horizontal)
        split.setContentsMargins(12, 12, 12, 12)
        root.addWidget(split, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(320)
        scroll.setMaximumWidth(380)
        scroll.setWidget(self._build_side())
        split.addWidget(scroll)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        rv.setSpacing(8)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        rv.addWidget(self._grid_host, 1)
        rv.addWidget(self._build_marker_table())
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 1160])

        sb = self.statusBar()
        self.lbl_conn = QLabel("")
        self.lbl_conn.setTextFormat(Qt.RichText)
        self.lbl_rate = QLabel("")
        self.lbl_mode = QLabel("")
        self.lbl_temp = QLabel("")
        sb.addPermanentWidget(self.lbl_temp)
        sb.addPermanentWidget(self.lbl_mode)
        sb.addPermanentWidget(self.lbl_rate)
        sb.addPermanentWidget(self.lbl_conn)
        sb.showMessage("Pronto")
        self._set_conn_label(False, "non connesso")
        self._mon.updated.connect(self._on_status)
        self._mon.updated.connect(lambda _d: self._esp.send_state())
        self._mon.event.connect(self._esp.send_event)
        self._mon.event.connect(lambda e: self.statusBar().showMessage(e, 8000))

    def _build_ribbon(self):
        rib = Ribbon()

        # --- Misura ---
        t = rib.add_tab("Misura")
        g = t.add_group("Acquisizione")
        self.btn_cont = g.add_large("Continuo", "play", lambda: self._set_run(True))
        g.add_large("Singolo", "single", self._single)
        g.add_large("Hold", "pause", lambda: self._set_run(False))
        g = t.add_group("Calibrazione")
        g.add_large("Calibra…", "sliders", self._open_cal)
        g = t.add_group("Impostazioni")
        g.add_large("Preset", "undo", self._preset)

        # --- Tracce ---
        t = rib.add_tab("Tracce")
        g = t.add_group("Traccia")
        g.add_large("Aggiungi", "plus", self._add_trace)
        g.add_large("Elimina", "trash", self._delete_trace)
        g = t.add_group("Diagramma")
        g.add_large("Aggiungi", "layers", self._add_diagram)
        g.add_large("Elimina", "trash", self._delete_diagram)
        g = t.add_group("Scala")
        g.add_large("Adatta ai dati", "wand", self._autoscale)
        g.add_large("Scala predefinita", "rotate", self._default_scale)

        # --- Marker ---
        t = rib.add_tab("Marker")
        g = t.add_group("Marker")
        g.add_large("Aggiungi", "flag", self._add_marker)
        col = g.add_column()
        col.add_small("Elimina selezionato", "close", self._delete_marker)
        col.add_small("Cancella tutti", "trash", self._clear_markers)
        self.btn_table = col.add_small("Tabella marker", "table", self._toggle_table, checkable=True)
        g = t.add_group("Posizione")
        g.add_large("Frequenza…", "find", self._entry_marker_freq)
        col = g.add_column()
        col.add_small("\u25c0 Punto prec.", "undo", lambda: self._marker_step(-1))
        col.add_small("Punto succ. \u25b6", "redo", lambda: self._marker_step(+1))
        g = t.add_group("Delta")
        self.btn_delta = g.add_large("Delta da M1", "delta", self._toggle_delta, checkable=True)
        g = t.add_group("Ricerca")
        g.add_large("\u2192 Massimo", "peak", lambda: self._marker_search("max", 0))
        g.add_large("\u2192 Minimo", "peak", lambda: self._marker_search("min", 0))
        g.add_large("Picco \u25b6", "zoom_in", lambda: self._marker_search(self._search_mode, +1))
        g.add_large("\u25c0 Picco", "zoom_out", lambda: self._marker_search(self._search_mode, -1))
        col = g.add_column()
        self.btn_mode = col.add_small("Picchi: Min", "sort", self._toggle_search_mode)
        col.add_small("Marker sui picchi…", "star", self._markers_on_peaks)
        g = t.add_group("Marker \u2192")
        col = g.add_column()
        col.add_small("\u2192 Centro", "crop", lambda: self._marker_to("center"))
        col.add_small("\u2192 Start", "crop", lambda: self._marker_to("start"))
        col.add_small("\u2192 Stop", "crop", lambda: self._marker_to("stop"))
        col.add_small("\u2192 Ref level", "crop", lambda: self._marker_to("ref"))

        # --- Analisi ---
        t = rib.add_tab("Analisi")
        g = t.add_group("Misure")
        g.add_large("Banda a X dB…", "histogram", self._entry_bw)
        g.add_large("Statistiche", "sum", self._show_stats)
        g = t.add_group("Maschera return loss")
        g.add_large("Imposta…", "mask", self._mask_setup)
        self.btn_mask = g.add_large("Attiva", "check", self._mask_toggle, checkable=True)
        g.add_large("Elimina", "trash", self._mask_delete)

        # --- Stato ---
        t = rib.add_tab("Stato")
        g = t.add_group("Monitor strumento")
        g.add_large("Storico…", "chart", self._open_status)
        g.add_large("Rif. cal = ora", "clock", self._status_cal_ref)
        g = t.add_group("Registro")
        g.add_large("Esporta CSV…", "export", self._status_export)
        g = t.add_group("Display")
        g.add_large("Display ESP32…", "settings", self._open_esp)

        # --- File ---
        t = rib.add_tab("File")
        g = t.add_group("Setup")
        g.add_large("Salva setup…", "save", self._save_setup)
        g.add_large("Carica setup…", "open", self._load_setup)
        g = t.add_group("Esporta")
        g.add_large("Touchstone…", "export", self._export_touchstone)
        g.add_large("CSV…", "excel", self._export_csv)
        g.add_large("Immagine…", "image", self._screenshot)
        g = t.add_group("Programma")
        g.add_large("Informazioni", "info", self._about)
        return rib

    def _build_marker_table(self):
        self.mk_card = Card()
        v = QVBoxLayout(self.mk_card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        head = QHBoxLayout()
        self.mk_title = h2("Marker")
        head.addWidget(self.mk_title)
        head.addStretch(1)
        for text, cb in (("Aggiungi", self._add_marker), ("Elimina", self._delete_marker),
                         ("Cancella tutti", self._clear_markers)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            head.addWidget(b)
        v.addLayout(head)
        self.mk_tab = QTableWidget(0, 6)
        self.mk_tab.setHorizontalHeaderLabels(["Marker", "Frequenza", "Valore", "\u0394 da", "\u0394 Frequenza", "\u0394 Valore"])
        self.mk_tab.verticalHeader().setVisible(False)
        self.mk_tab.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mk_tab.setSelectionMode(QAbstractItemView.SingleSelection)
        self.mk_tab.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.mk_tab.setMinimumHeight(110)
        self.mk_tab.setMaximumHeight(170)
        self.mk_tab.itemChanged.connect(self._mk_item_changed)
        self.mk_tab.itemSelectionChanged.connect(self._mk_row_selected)
        v.addWidget(self.mk_tab)
        v.addWidget(dim("Doppio clic su Frequenza per digitare (es. 2.45 GHz, 868M, 1500). "
                        "Clic sul grafico: sposta il marker selezionato."))
        self._mk_sig = None
        self._mk_timer = QTimer(self)
        self._mk_timer.setInterval(300)
        self._mk_timer.timeout.connect(self._mk_update_values)
        self._mk_timer.start()
        return self.mk_card

    def _fspin(self, cb):
        s = QDoubleSpinBox()
        s.setRange(0.0, 6000.0)
        s.setDecimals(6)
        s.setSuffix(" MHz")
        s.setKeyboardTracking(False)
        s.valueChanged.connect(lambda v: None if self._syncing else cb(v * 1e6))
        return s

    def _build_side(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 8, 0)
        v.setSpacing(10)

        # intestazione marchio
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 0, 0)
        mark = QLabel()
        mark.setPixmap(bolt_pixmap(44))
        head.addWidget(mark)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        name = QLabel("TornadoVNA")
        name.setObjectName("H1")
        tl.addWidget(name)
        tl.addWidget(dim("Tornado Laboratori"))
        head.addLayout(tl, 1)
        v.addLayout(head)

        # --- azione principale ---
        self.btn_run = QPushButton("  Hold")
        self.btn_run.setProperty("primary", True)
        self.btn_run.setIcon(icon("pause", P.on_accent, 18))
        self.btn_run.clicked.connect(lambda: self._set_run(not self._running))
        v.addWidget(self.btn_run)

        # --- stimolo ---
        c = Card()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.addWidget(h2("Stimolo"))
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignLeft)
        self.sp_start = self._fspin(self._set_start)
        self.sp_stop = self._fspin(self._set_stop)
        self.sp_center = self._fspin(self._set_center)
        self.sp_span = self._fspin(self._set_span)
        self.sp_points = QSpinBox()
        self.sp_points.setRange(11, 4501)
        self.sp_points.setKeyboardTracking(False)
        self.sp_points.valueChanged.connect(lambda x: None if self._syncing else self._set_points(x))
        self.cb_ifbw = QComboBox()
        for b in IFBW_VALUES:
            self.cb_ifbw.addItem(_fmt_hz(b) if b >= 1000 else f"{b} Hz", b)
        self.cb_ifbw.currentIndexChanged.connect(
            lambda i: None if self._syncing else self._set_ifbw(self.cb_ifbw.itemData(i)))
        self.sp_level = QSpinBox()
        self.sp_level.setRange(-40, 0)
        self.sp_level.setSuffix(" dBm")
        self.sp_level.setKeyboardTracking(False)
        self.sp_level.valueChanged.connect(lambda x: None if self._syncing else self._set_level(x))
        self.sp_avg = QSpinBox()
        self.sp_avg.setRange(1, 100)
        self.sp_avg.setKeyboardTracking(False)
        self.sp_avg.valueChanged.connect(lambda x: None if self._syncing else self._set_avg(x))
        for lbl, wd in (("Start", self.sp_start), ("Stop", self.sp_stop), ("Centro", self.sp_center),
                        ("Span", self.sp_span), ("Punti", self.sp_points), ("IF BW", self.cb_ifbw),
                        ("Potenza", self.sp_level), ("Media", self.sp_avg)):
            f.addRow(lbl, wd)
        cv.addLayout(f)
        v.addWidget(c)

        # --- traccia attiva ---
        c = Card()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.addWidget(h2("Traccia attiva"))
        self.lst = QListWidget()
        self.lst.setMaximumHeight(72)
        self.lst.currentRowChanged.connect(self._on_list_row)
        cv.addWidget(self.lst)
        f = QFormLayout()
        self.cb_meas = QComboBox()
        for cat, qs in MEAS_MENU.items():
            for q in qs:
                self.cb_meas.addItem(f"{cat}: {meas_label(q)}" if cat != "S-Parameter" else meas_label(q), q)
        self.cb_meas.currentIndexChanged.connect(
            lambda i: None if self._syncing else self._set_meas(self.cb_meas.itemData(i)))
        self.cb_fmt = QComboBox()
        for n in FORMAT_MENU_ORDER:
            self.cb_fmt.addItem(n, n)
        self.cb_fmt.currentIndexChanged.connect(
            lambda i: None if self._syncing else self._set_fmt(self.cb_fmt.itemData(i)))
        self.sp_ref = QDoubleSpinBox()
        self.sp_ref.setRange(-1e6, 1e6)
        self.sp_ref.setDecimals(4)
        self.sp_ref.setKeyboardTracking(False)
        self.sp_ref.valueChanged.connect(lambda x: None if self._syncing else self._set_ref(x))
        self.sp_div = QDoubleSpinBox()
        self.sp_div.setRange(1e-6, 1e6)
        self.sp_div.setDecimals(4)
        self.sp_div.setKeyboardTracking(False)
        self.sp_div.valueChanged.connect(lambda x: None if self._syncing else self._set_div(x))
        self.sp_up = QSpinBox()
        self.sp_up.setRange(0, 50)
        self.sp_up.setKeyboardTracking(False)
        self.sp_up.valueChanged.connect(lambda x: None if self._syncing else self._set_up(x))
        self.sp_down = QSpinBox()
        self.sp_down.setRange(0, 50)
        self.sp_down.setKeyboardTracking(False)
        self.sp_down.valueChanged.connect(lambda x: None if self._syncing else self._set_down(x))
        for lbl, wd in (("Misura", self.cb_meas), ("Formato", self.cb_fmt), ("Ref level", self.sp_ref),
                        ("Scala/div", self.sp_div), ("Div sopra", self.sp_up), ("Div sotto", self.sp_down)):
            f.addRow(lbl, wd)
        cv.addLayout(f)
        v.addWidget(c)

        self.status_card = StatusCard(self._mon)
        self.status_card.modeChanged.connect(self._statusModeSig.emit)
        v.addWidget(self.status_card)
        v.addStretch(1)
        return w

    # ------------------------------------------------------- sincronizzazione
    def _sync_panel(self):
        self._syncing = True
        try:
            p = self._p
            for sp, val in ((self.sp_start, p["start"] / 1e6), (self.sp_stop, p["stop"] / 1e6),
                            (self.sp_center, (p["start"] + p["stop"]) / 2e6),
                            (self.sp_span, (p["stop"] - p["start"]) / 1e6)):
                sp.blockSignals(True); sp.setValue(val); sp.blockSignals(False)
            for sp, val in ((self.sp_points, p["points"]), (self.sp_level, p["level"]), (self.sp_avg, p["avg"])):
                sp.blockSignals(True); sp.setValue(int(val)); sp.blockSignals(False)
            self.cb_ifbw.blockSignals(True)
            i = self.cb_ifbw.findData(p["ifbw"])
            if i < 0:
                self.cb_ifbw.addItem(_fmt_hz(p["ifbw"]) if p["ifbw"] >= 1000 else f"{p['ifbw']} Hz", p["ifbw"])
                i = self.cb_ifbw.count() - 1
            self.cb_ifbw.setCurrentIndex(i)
            self.cb_ifbw.blockSignals(False)

            d = self._active_diagram()
            self.lst.blockSignals(True)
            self.lst.clear()
            for t in d.traces:
                self.lst.addItem(f"{t.name}   {t.display_meas}   {t.fmt}")
            self.lst.setCurrentRow(d.active)
            self.lst.blockSignals(False)

            at = d.active_trace()
            en = at is not None
            for wdg in (self.cb_meas, self.cb_fmt, self.sp_ref, self.sp_div, self.sp_up, self.sp_down):
                wdg.setEnabled(en)
            if en:
                for cb, key in ((self.cb_meas, at.meas), (self.cb_fmt, at.fmt)):
                    cb.blockSignals(True); cb.setCurrentIndex(max(0, cb.findData(key))); cb.blockSignals(False)
                for sp, val in ((self.sp_ref, at.ref), (self.sp_div, at.div), (self.sp_up, at.up), (self.sp_down, at.down)):
                    sp.blockSignals(True); sp.setValue(val); sp.blockSignals(False)
                self.btn_delta.setChecked(any(m.ref >= 0 for m in at.markers))
                self.btn_mask.setChecked(bool(at.mask and at.mask.get("enabled", True)))
            self.btn_mode.setText(f"Picchi: {'Max' if self._search_mode == 'max' else 'Min'}")
            self._mk_rebuild()
            self._update_run_ui()
            self.lbl_rate.setText(f"{self._rate:.1f} sweep/s   ")
        finally:
            self._syncing = False

    def _update_run_ui(self):
        if self._running:
            self.btn_run.setText("  Hold")
            self.btn_run.setIcon(icon("pause", P.on_accent, 18))
            self.lbl_mode.setText("Continuo   ")
        else:
            self.btn_run.setText("  Continuo")
            self.btn_run.setIcon(icon("play", P.on_accent, 18))
            self.lbl_mode.setText("Hold   ")

    def _set_conn_label(self, ok, text):
        col = P.ok if ok else P.accent
        self.lbl_conn.setText(f"<span style='color:{col};'>●</span> {text}")

    def _refresh_chrome(self, *_):
        self._sync_panel()

    # ------------------------------------------------------------ griglia
    def _rebuild_grid(self):
        for w in self._diag_widgets:
            self._grid.removeWidget(w)
            w.setParent(None)
        self._diag_widgets.clear()
        n = len(self._diagrams)
        rows, cols = (1, 1) if n <= 1 else (1, 2) if n == 2 else (2, 2)
        for i, diag in enumerate(self._diagrams):
            w = DiagramWidget(i)
            w.selected.connect(self._on_diagram_selected)
            w.traceSelected.connect(self._on_trace_selected)
            w.plotClicked.connect(self._on_plot_clicked)
            w.bind(diag)
            r, c = divmod(i, cols)
            self._grid.addWidget(w, r, c)
            self._diag_widgets.append(w)
        self._highlight_active_diagram()
        self._apply_x_range()
        if self._last_f is not None:
            self._distribute_sweep()

    def _highlight_active_diagram(self):
        for i, w in enumerate(self._diag_widgets):
            w.set_active_style(i == self._active_diag)

    @Slot(int)
    def _on_diagram_selected(self, idx):
        self._active_diag = idx
        self._highlight_active_diagram()
        self._sync_panel()

    @Slot(int, int)
    def _on_trace_selected(self, diag_idx, tr_idx):
        self._active_diag = diag_idx
        self._diagrams[diag_idx].active = tr_idx
        self._highlight_active_diagram()
        self._diag_widgets[diag_idx].refresh()
        self._sync_panel()

    def _on_list_row(self, row):
        if self._syncing or row < 0:
            return
        self._active_diagram().active = row
        self._refresh_active()
        self._sync_panel()

    def _active_diagram(self):
        return self._diagrams[self._active_diag]

    def _active_trace(self):
        return self._active_diagram().active_trace()

    def _refresh_active(self):
        self._diag_widgets[self._active_diag].refresh()

    # ------------------------------------------------------ misura / formato
    def _set_meas(self, q):
        at = self._active_trace()
        if at is None or q is None:
            return
        at.meas = q
        at.fmt = default_format(q)
        at.apply_default_scale()
        self._push_config()
        self._refresh_active()
        self._sync_panel()

    def _set_fmt(self, name):
        at = self._active_trace()
        if at is None or name is None:
            return
        at.fmt = name
        at.apply_default_scale()
        self._refresh_active()
        self._sync_panel()

    # ------------------------------------------------------------ tracce
    def _add_trace(self):
        self._active_diagram().add_trace(new_trace("S11"))
        self._push_config()
        self._refresh_active()
        self._sync_panel()

    def _delete_trace(self):
        d = self._active_diagram()
        if len(d.traces) > 1:
            d.remove_active()
            self._push_config()
            self._refresh_active()
        self._sync_panel()

    def _add_diagram(self):
        if len(self._diagrams) >= self.MAX_DIAGRAMS:
            return
        d = Diagram(title=f"Diagram {len(self._diagrams) + 1}")
        d.add_trace(new_trace("S11"))
        self._diagrams.append(d)
        self._active_diag = len(self._diagrams) - 1
        self._rebuild_grid()
        self._push_config()
        self._sync_panel()

    def _delete_diagram(self):
        if len(self._diagrams) <= 1:
            return
        del self._diagrams[self._active_diag]
        self._active_diag = max(0, self._active_diag - 1)
        self._rebuild_grid()
        self._push_config()
        self._sync_panel()

    # ------------------------------------------------------------ stimolo
    @staticmethod
    def _clamp_f(v):
        return max(0.0, min(6e9, v))

    def _commit(self):
        if self._p["start"] >= self._p["stop"]:
            self._p["stop"] = self._p["start"] + 1e6
        self._push_config()
        self._apply_x_range()
        self._sync_panel()

    def _apply_x_range(self):
        for w in self._diag_widgets:
            w.set_x_range(self._p["start"], self._p["stop"])

    def _set_start(self, v): self._p["start"] = self._clamp_f(v); self._commit()
    def _set_stop(self, v): self._p["stop"] = self._clamp_f(v); self._commit()

    def _set_center(self, v):
        span = self._p["stop"] - self._p["start"]
        self._p["start"] = self._clamp_f(v - span / 2)
        self._p["stop"] = self._clamp_f(v + span / 2)
        self._commit()

    def _set_span(self, v):
        c = (self._p["start"] + self._p["stop"]) / 2
        self._p["start"] = self._clamp_f(c - v / 2)
        self._p["stop"] = self._clamp_f(c + v / 2)
        self._commit()

    def _set_points(self, v): self._p["points"] = int(np.clip(v, 11, 4501)); self._commit()
    def _set_level(self, v): self._p["level"] = int(np.clip(v, -40, 0)); self._commit()
    def _set_ifbw(self, v): self._p["ifbw"] = int(max(1, v)); self._commit()
    def _set_avg(self, v): self._p["avg"] = int(max(1, v)); self._commit()

    # ------------------------------------------------------------ scala
    def _autoscale(self):
        at = self._active_trace()
        if at:
            at.needs_fit = True
            self._refresh_active()
        self._sync_panel()

    def _default_scale(self):
        at = self._active_trace()
        if at:
            at.apply_default_scale()
            self._refresh_active()
        self._sync_panel()

    def _after_scale(self):
        self._refresh_active()
        self._sync_panel()

    def _set_ref(self, v):
        at = self._active_trace()
        if at:
            at.ref = v; at.needs_fit = False
        self._after_scale()

    def _set_div(self, v):
        at = self._active_trace()
        if at:
            at.div = max(1e-15, v); at.needs_fit = False
        self._after_scale()

    def _set_up(self, v):
        at = self._active_trace()
        if at:
            at.up = int(np.clip(round(v), 0, 50))
            if at.up + at.down < 1:
                at.down = 1
        self._after_scale()

    def _set_down(self, v):
        at = self._active_trace()
        if at:
            at.down = int(np.clip(round(v), 0, 50))
            if at.up + at.down < 1:
                at.up = 1
        self._after_scale()

    # ------------------------------------------------------------ marker
    def _trace_y(self, at):
        if at is None or self._last_f is None:
            return None
        data = compute(at.meas, self._last_t, Z0)
        if data is None:
            return None
        f = self._last_f
        n = min(len(f), len(data))
        f, data = f[:n], data[:n]
        return f, transform(at.fmt, data, f, Z0), data

    def _span_center(self):
        return (self._p["start"] + self._p["stop"]) / 2

    def _snap(self, freq):
        """Frequenza -> (indice, frequenza) del punto di sweep piu' vicino (se c'e' uno sweep)."""
        if self._last_f is None or len(self._last_f) == 0:
            return 0, float(freq)
        idx = int(np.argmin(np.abs(self._last_f - freq)))
        return idx, float(self._last_f[idx])

    def _sel_marker(self, at, create=False):
        if at is None:
            return None
        if not at.markers and create:
            self._add_marker()
        if not at.markers:
            return None
        at.msel = int(np.clip(at.msel, 0, len(at.markers) - 1))
        return at.markers[at.msel]

    def _mk_changed(self):
        self._refresh_active()
        self._mk_rebuild()

    def _add_marker(self, freq=None):
        at = self._active_trace()
        if at is None:
            return
        span = self._p["stop"] - self._p["start"]
        if freq is None:
            freq = self._span_center() if not at.markers else at.markers[-1].freq + span * 0.1
            if freq > self._p["stop"]:
                freq = self._p["start"] + span * 0.1
        idx, fq = self._snap(freq)
        mk = Marker(idx=idx, freq=fq)
        if any(m.ref >= 0 for m in at.markers[1:]) and at.markers:
            mk.ref = 0
        at.markers.append(mk)
        at.msel = len(at.markers) - 1
        self._mk_changed()
        self._sync_panel()

    def _delete_marker(self):
        at = self._active_trace()
        if at is None or not at.markers:
            return
        k = int(np.clip(at.msel, 0, len(at.markers) - 1))
        del at.markers[k]
        for m in at.markers:                         # aggiorna i riferimenti
            if m.ref == k:
                m.ref = -1
            elif m.ref > k:
                m.ref -= 1
        at.msel = max(0, min(at.msel, len(at.markers) - 1))
        self._mk_changed()
        self._sync_panel()

    def _clear_markers(self):
        at = self._active_trace()
        if at:
            at.markers.clear()
            at.msel = 0
            self._mk_changed()
        self._sync_panel()

    def _set_marker_freq(self, k, freq):
        at = self._active_trace()
        if at is None or not (0 <= k < len(at.markers)):
            return
        freq = float(np.clip(freq, self._p["start"], self._p["stop"]))
        at.markers[k].idx, at.markers[k].freq = self._snap(freq)
        self._mk_changed()

    def _entry_marker_freq(self):
        at = self._active_trace()
        m = self._sel_marker(at, create=True)
        if m is None:
            return
        v, ok = QInputDialog.getDouble(self, f"Frequenza M{at.msel + 1}", "Frequenza [MHz]:",
                                       m.freq / 1e6, self._p["start"] / 1e6, self._p["stop"] / 1e6, 6)
        if ok:
            self._set_marker_freq(at.msel, v * 1e6)

    def _marker_step(self, d):
        at = self._active_trace()
        m = self._sel_marker(at)
        if m is None or self._last_f is None:
            return
        cur = int(np.argmin(np.abs(self._last_f - m.freq)))
        j = int(np.clip(cur + d, 0, len(self._last_f) - 1))
        self._set_marker_freq(at.msel, float(self._last_f[j]))

    def _on_plot_clicked(self, diag_idx, freq):
        """Clic sul grafico: sposta il marker selezionato (o ne crea uno)."""
        self._active_diag = diag_idx
        self._highlight_active_diagram()
        at = self._active_trace()
        if at is None:
            return
        if not at.markers:
            self._add_marker(freq)
        else:
            self._set_marker_freq(at.msel, freq)
        self._sync_panel()

    def _marker_search(self, mode, direction):
        at = self._active_trace()
        r = self._trace_y(at)
        if r is None:
            return
        f, y, _ = r
        if not np.isfinite(y).any():
            return
        m = self._sel_marker(at, create=True)
        if m is None:
            return
        if direction == 0:
            idx = int(np.nanargmax(y) if mode == "max" else np.nanargmin(y))
        else:
            cur = int(np.argmin(np.abs(f - m.freq)))
            idx = analysis.next_peak(f, y, cur, direction, mode)
            if idx is None:
                self.statusBar().showMessage("Nessun altro picco in quella direzione")
                return
        self._set_marker_freq(at.msel, float(f[idx]))

    def _toggle_search_mode(self):
        self._search_mode = "max" if self._search_mode == "min" else "min"
        self._sync_panel()

    def _markers_on_peaks(self):
        at = self._active_trace()
        r = self._trace_y(at)
        if r is None:
            self.statusBar().showMessage("Nessun dato")
            return
        f, y, _ = r
        n, ok = QInputDialog.getInt(self, "Marker sui picchi",
                                    f"Quanti marker (picchi di {'massimo' if self._search_mode == 'max' else 'minimo'}):",
                                    3, 1, 20)
        if not ok:
            return
        idx = analysis.peak_indices(y, self._search_mode)
        if len(idx) == 0:
            self.statusBar().showMessage("Nessun picco trovato")
            return
        order = np.argsort(-y[idx] if self._search_mode == "max" else y[idx])[:n]
        at.markers.clear()
        for i in sorted(int(idx[o]) for o in order):
            at.markers.append(Marker(idx=i, freq=float(f[i])))
        at.msel = 0
        self._mk_changed()
        self._sync_panel()

    def _toggle_delta(self):
        at = self._active_trace()
        if at and at.markers:
            on = any(m.ref >= 0 for m in at.markers)
            for k, m in enumerate(at.markers):
                m.ref = -1 if (on or k == 0) else 0
            self._mk_changed()
        self._sync_panel()

    def _marker_to(self, what):
        at = self._active_trace()
        m = self._sel_marker(at)
        r = self._trace_y(at)
        if m is None or r is None:
            self.statusBar().showMessage("Serve un marker e uno sweep")
            return
        f, y, _ = r
        idx = int(np.argmin(np.abs(f - m.freq)))
        if what == "center":
            self._set_center(float(f[idx]))
        elif what == "start":
            self._set_start(float(f[idx]))
        elif what == "stop":
            self._set_stop(float(f[idx]))
        elif what == "ref" and np.isfinite(y[idx]):
            self._set_ref(float(y[idx]))

    # ---- tabella marker ----
    def _toggle_table(self):
        self.mk_card.setVisible(self.btn_table.isChecked())

    def _mk_structure(self, at):
        return None if at is None else (id(at), len(at.markers), tuple(m.ref for m in at.markers))

    def _mk_rebuild(self):
        """Ricostruisce la tabella (cambia numero marker / traccia / riferimenti)."""
        at = self._active_trace()
        self._mk_sig = self._mk_structure(at)
        t = self.mk_tab
        t.blockSignals(True)
        t.setRowCount(0)
        self.mk_title.setText(f"Marker  \u2014  {at.name} {at.display_meas}" if at else "Marker")
        if at is not None:
            t.setRowCount(len(at.markers))
            names = ["\u2014"] + [f"M{j + 1}" for j in range(len(at.markers))]
            for k, m in enumerate(at.markers):
                it = QTableWidgetItem(f"M{k + 1}")
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                t.setItem(k, 0, it)
                fi = QTableWidgetItem("")
                fi.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
                t.setItem(k, 1, fi)
                for c in (2, 4, 5):
                    ci = QTableWidgetItem("")
                    ci.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    t.setItem(k, c, ci)
                cb = QComboBox()
                for j, nm in enumerate(names):
                    if j - 1 != k:                      # non puo' essere riferimento di se stesso
                        cb.addItem(nm, j - 1)
                cb.setCurrentIndex(max(0, cb.findData(m.ref)))
                cb.currentIndexChanged.connect(lambda _i, k=k, cb=cb: self._mk_ref_changed(k, cb.currentData()))
                t.setCellWidget(k, 3, cb)
            if at.markers:
                t.selectRow(int(np.clip(at.msel, 0, len(at.markers) - 1)))
        t.blockSignals(False)
        self._mk_update_values()

    def _mk_update_values(self):
        at = self._active_trace()
        if self._mk_structure(at) != self._mk_sig:
            self._mk_rebuild()
            return
        t = self.mk_tab
        if at is None or not at.markers or not self.mk_card.isVisible():
            return
        if t.state() == QAbstractItemView.EditingState:
            return
        r = self._trace_y(at)
        t.blockSignals(True)
        try:
            unit = FORMATS.get(at.fmt, {}).get("unit", "")
            for k, m in enumerate(at.markers):
                t.item(k, 1).setText(fmt_freq(m.freq))
                if r is None:
                    continue
                f, y, data = r
                idx = int(np.argmin(np.abs(f - m.freq)))
                t.item(k, 2).setText(scalar_str(at.fmt, data[idx], float(f[idx]), Z0, data, f))
                if 0 <= m.ref < len(at.markers) and m.ref != k:
                    j = int(np.argmin(np.abs(f - at.markers[m.ref].freq)))
                    t.item(k, 4).setText(fmt_freq(float(f[idx] - f[j])))
                    t.item(k, 5).setText(f"{y[idx] - y[j]:+.4g} {unit}")
                else:
                    t.item(k, 4).setText("")
                    t.item(k, 5).setText("")
        finally:
            t.blockSignals(False)

    def _mk_item_changed(self, item):
        if item.column() != 1:
            return
        v = parse_freq(item.text())
        if v is None:
            self.statusBar().showMessage("Frequenza non valida")
            self._mk_update_values()
            return
        self._set_marker_freq(item.row(), v)
        self._sync_panel()

    def _mk_row_selected(self):
        at = self._active_trace()
        rows = self.mk_tab.selectionModel().selectedRows()
        if at is None or not rows or self._syncing:
            return
        k = rows[0].row()
        if k != at.msel and k < len(at.markers):
            at.msel = k
            self._refresh_active()

    def _mk_ref_changed(self, k, ref):
        at = self._active_trace()
        if at is None or not (0 <= k < len(at.markers)):
            return
        at.markers[k].ref = -1 if ref is None else int(ref)
        self._mk_sig = self._mk_structure(at)
        self._refresh_active()
        self._sync_panel()

    # ------------------------------------------------------------ analisi
    def _entry_bw(self):
        v, ok = QInputDialog.getDouble(self, "Banda a X dB", "Calo rispetto al picco [dB]:", 3.0, 0.01, 200.0, 2)
        if ok:
            self._show_bw(v)

    def _show_bw(self, drop):
        at = self._active_trace()
        r = self._trace_y(at)
        if r is None:
            self.statusBar().showMessage("Nessun dato")
            return
        f, y, _ = r
        idx = int(np.argmin(np.abs(f - at.markers[min(at.msel, len(at.markers) - 1)].freq))) if at.markers else None
        b = analysis.bandwidth_around(f, y, abs(drop), self._search_mode, idx)
        if b is None:
            self.statusBar().showMessage("Analisi non possibile")
            return
        unit = FORMATS.get(at.fmt, {}).get("unit", "")
        lines = [f"Riferimento: {fmt_freq(b.f_peak)}  ({b.y_peak:.4g} {unit})",
                 f"Livello a {abs(drop):g} dB: {b.level:.4g} {unit}"]
        if b.bandwidth is None:
            lines.append("Banda non chiusa nello span misurato")
        else:
            lines += [f"f bassa : {fmt_freq(b.f_low)}", f"f alta  : {fmt_freq(b.f_high)}",
                      f"Banda   : {fmt_freq(b.bandwidth)}", f"Centro  : {fmt_freq(b.center)}",
                      f"Q       : {b.q:.2f}" if b.q else ""]
        QMessageBox.information(self, "Banda a X dB", "\n".join(s for s in lines if s))

    def _show_stats(self):
        at = self._active_trace()
        r = self._trace_y(at)
        if r is None:
            self.statusBar().showMessage("Nessun dato")
            return
        f, y, _ = r
        st = analysis.range_stats(f, y)
        if not st:
            return
        u = FORMATS.get(at.fmt, {}).get("unit", "")
        QMessageBox.information(self, "Statistiche", (
            f"{at.name}  {at.display_meas}  {at.fmt}\n"
            f"Punti : {st['n']}  ({fmt_freq(st['f1'])} – {fmt_freq(st['f2'])})\n"
            f"Min   : {st['min']:.4g} {u} @ {fmt_freq(st['f_min'])}\n"
            f"Max   : {st['max']:.4g} {u} @ {fmt_freq(st['f_max'])}\n"
            f"Ripple: {st['ripple']:.4g} {u}\n"
            f"Media : {st['mean']:.4g} {u}   σ {st['std']:.4g}"))

    # ------------------------------------------------------------ maschera
    def _mask_setup(self):
        at = self._active_trace()
        if at is None:
            return
        if at.fmt not in MASK_FORMATS:
            self.statusBar().showMessage(
                "Maschera: imposta il formato dB Mag, SWR o Lin Mag (la maschera è di return loss)")
        cur = Mask.from_dict(at.mask) if at.mask else None
        dlg = MaskDialog(self, cur, (self._p["start"], self._p["stop"]))
        if dlg.exec():
            at.mask = dlg.mask().to_dict()
            self._refresh_active()
        self._sync_panel()

    def _mask_toggle(self):
        at = self._active_trace()
        if at and at.mask:
            at.mask["enabled"] = not at.mask.get("enabled", True)
            self._refresh_active()
        self._sync_panel()

    def _mask_delete(self):
        at = self._active_trace()
        if at:
            at.mask = None
            self._refresh_active()
        self._sync_panel()

    # ------------------------------------------------------------ sweep / run
    def _set_run(self, run):
        self._running = bool(run)
        self._runningSig.emit(self._running)
        self._update_run_ui()

    def _single(self):
        self._running = False
        self._singleSig.emit()
        self._update_run_ui()

    def _preset(self):
        self._p = dict(start=1e8, stop=6e9, points=501, ifbw=10000, level=-10, avg=1)
        self._diagrams = default_layout()
        self._active_diag = 0
        self._rebuild_grid()
        self._commit()

    def _open_cal(self):
        # lo sweep resta attivo: durante la cal si vedono le tracce live
        self._cal_dlg = CalibrationDialog(self, self.worker)
        self._cal_dlg.show()

    def _required_sparams(self):
        need, need_full = set(), False
        for d in self._diagrams:
            for tr in d.traces:
                if tr.meas in ("S11", "S21", "S12", "S22"):
                    need.add(tr.meas)
                else:
                    need_full = True
        if need_full:
            return ["S11", "S21", "S12", "S22"]
        return sorted(need) or ["S11"]

    # ------------------------------------------------------------ stato strumento
    def _on_status(self, d):
        parts = [f"{d[k]:.0f}" for k in ("temp_source", "temp_lo", "temp_mcu") if d.get(k) is not None]
        self.lbl_temp.setText(("T " + "/".join(parts) + " \u00b0C   ") if parts else "")

    def _open_status(self):
        if self._status_dlg is None:
            self._status_dlg = StatusDialog(self._mon, self)
        self._status_dlg.show()
        self._status_dlg.raise_()

    # ------------------------------------------------------------ display ESP32
    def _open_esp(self):
        if self._esp_dlg is None:
            self._esp_dlg = EspDialog(self._esp, self)
        self._esp_dlg.refresh()
        self._esp_dlg.show()
        self._esp_dlg.raise_()

    def _esp_state(self):
        d = self._mon.last or {}
        dr = self._mon.drift() or {}
        def b(k):
            v = d.get(k)
            return None if v is None else int(bool(v))
        return dict(
            conn=int(self._conn_ok), run=int(self._running), rate=round(self._rate, 1),
            start=self._p["start"], stop=self._p["stop"], pts=self._p["points"],
            ifbw=self._p["ifbw"], lvl=self._p["level"], avg=self._p["avg"],
            tsrc=d.get("temp_source"), tlo=d.get("temp_lo"), tmcu=d.get("temp_mcu"),
            src=b("source_locked"), lo=b("lo_locked"), ovl=b("adc_overload"), unl=b("unlevel"),
            xav=b("ext_ref_avail"), xref=b("ext_ref_use"),
            vs=d.get("supply_v"), isup=d.get("supply_i"),
            drift=(round(max(dr.values(), key=abs), 1) if dr else None))

    def _esp_command(self, m):
        c = m.get("c")
        if c == "run":
            self._set_run(bool(m.get("v")))
        elif c == "single":
            self._single()
        elif c == "preset":
            self._preset()
        elif c == "calref":
            self._status_cal_ref()
        elif c == "set":
            k, v = m["k"], float(m["v"])
            {"start": self._set_start, "stop": self._set_stop, "pts": self._set_points,
             "ifbw": self._set_ifbw, "lvl": self._set_level, "avg": self._set_avg}[k](v)
        self._esp.send_state()

    def _status_cal_ref(self):
        ok = self._mon.set_cal_reference()
        self.statusBar().showMessage("Riferimento termico impostato" if ok else "Nessuno stato ricevuto", 5000)

    def _status_export(self):
        if not self._mon.history:
            self.statusBar().showMessage("Nessun dato di stato")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta stato", "stato_vna.csv", "CSV (*.csv)")
        if path:
            self._mon.export_csv(path)
            self.statusBar().showMessage(f"Salvato: {path}")

    # ------------------------------------------------------------ file
    def _export_touchstone(self):
        if not self._last_t:
            self.statusBar().showMessage("Nessun dato")
            return
        n = 1 if list(self._last_t) == ["S11"] else 2
        path, _ = QFileDialog.getSaveFileName(self, "Salva Touchstone", f"meas.s{n}p", f"Touchstone (*.s{n}p)")
        if path:
            write_touchstone(path, self._last_f, self._last_t)
            self.statusBar().showMessage(f"Salvato: {path}")

    def _export_csv(self):
        if not self._last_t:
            self.statusBar().showMessage("Nessun dato")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salva CSV", "meas.csv", "CSV (*.csv)")
        if path:
            write_csv(path, self._last_f, self._last_t)
            self.statusBar().showMessage(f"Salvato: {path}")

    def _screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Salva immagine", "plot.png", "PNG (*.png)")
        if path:
            self._grid_host.grab().save(path)
            self.statusBar().showMessage(f"Salvato: {path}")

    def _save_setup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Salva setup", "setup.json", "JSON (*.json)")
        if not path:
            return
        cfg = dict(self._p)
        cfg["diagrams"] = [
            {"traces": [{"name": t.name, "meas": t.meas, "fmt": t.fmt, "ref": t.ref, "div": t.div,
                         "up": t.up, "down": t.down, "mask": t.mask, "msel": t.msel,
                         "markers": [{"freq": m.freq, "ref": m.ref} for m in t.markers]}
                        for t in d.traces],
             "active": d.active}
            for d in self._diagrams
        ]
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
        self.statusBar().showMessage(f"Setup salvato: {path}")

    def _load_setup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Carica setup", "", "JSON (*.json)")
        if not path:
            return
        with open(path) as f:
            cfg = json.load(f)
        for k in ("start", "stop", "points", "ifbw", "level", "avg"):
            if k in cfg:
                self._p[k] = cfg[k]
        if "diagrams" in cfg:
            self._diagrams = []
            for dd in cfg["diagrams"]:
                d = Diagram()
                for td in dd["traces"]:
                    t = Trace(name=td["name"], meas=td["meas"], fmt=td.get("fmt"))
                    if "ref" in td:
                        t.ref, t.div = td["ref"], td.get("div", t.div)
                        t.up, t.down = td.get("up", t.up), td.get("down", t.down)
                        t.needs_fit = False
                    t.mask = td.get("mask")
                    t.msel = td.get("msel", 0)
                    for mk in td.get("markers", []):
                        t.markers.append(Marker(freq=float(mk["freq"]), ref=int(mk.get("ref", -1))))
                    d.traces.append(t)
                d.active = dd.get("active", 0)
                self._diagrams.append(d)
            self._active_diag = 0
            self._rebuild_grid()
        self._commit()
        self.statusBar().showMessage(f"Setup caricato: {path}")

    def _about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Informazioni su TornadoVNA")
        v = QVBoxLayout(dlg)
        lg = QLabel()
        lg.setPixmap(logo_pixmap(360))
        lg.setAlignment(Qt.AlignCenter)
        v.addWidget(lg)
        v.addWidget(dim("TornadoVNA — controllo e misura per LibreVNA\n© Tornado Laboratori"))
        b = QPushButton("Chiudi")
        b.clicked.connect(dlg.accept)
        v.addWidget(b)
        dlg.exec()

    # ------------------------------------------------------------ worker
    def _start_worker(self, demo):
        self.thread = QThread()
        self.worker = (self._worker_class or VnaWorker)()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.init)
        self.worker.sweepReady.connect(self._on_sweep)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.connectedChanged.connect(self._on_conn)
        if hasattr(self.worker, "sweepRate"):
            self.worker.sweepRate.connect(self._on_rate)
        if hasattr(self.worker, "deviceStatus"):
            self.worker.deviceStatus.connect(self._mon.push)
            self._statusModeSig.connect(self.worker.setStatusMode)
        if hasattr(self.worker, "calFinished"):
            self.worker.calFinished.connect(lambda ok, _m: ok and self._mon.set_cal_reference())
        self._cfgSig.connect(self.worker.applyConfig)
        self._connSig.connect(self.worker.connectDevice)
        self._runningSig.connect(self.worker.setRunning)
        self._singleSig.connect(self.worker.triggerSingle)
        self.thread.start()
        self._connSig.emit(self._host, self._port, demo)
        self._runningSig.emit(True)

    def _push_config(self):
        cfg = dict(self._p)
        cfg["traces"] = self._required_sparams()
        self._cfgSig.emit(cfg)

    @Slot(float)
    def _on_rate(self, r):
        self._rate = r
        self.lbl_rate.setText(f"{r:.1f} sweep/s   ")

    @Slot(bool, str)
    def _on_conn(self, ok, detail):
        lines = detail.split("\n")
        self._conn_ok = bool(ok)
        self._set_conn_label(ok, lines[0])
        if len(lines) > 1:
            self.statusBar().showMessage(" | ".join(lines[1:]))

    @Slot(object, object)
    def _on_sweep(self, freqs, traces):
        self._last_f = freqs
        self._last_t = traces
        self._distribute_sweep()

    def _distribute_sweep(self):
        for w in self._diag_widgets:
            w.update_data(self._last_f, self._last_t)

    def closeEvent(self, e):
        try:
            self._esp.close()
            self.worker.stop()
            self.thread.quit()
            self.thread.wait(1000)
        finally:
            super().closeEvent(e)
