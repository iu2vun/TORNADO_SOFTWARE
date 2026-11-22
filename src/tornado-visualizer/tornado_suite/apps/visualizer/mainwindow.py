"""Tornado Visualizer — visualizzatore unico per PCB/schemi Altium (.PcbDoc/.SchDoc) e Gerber/Excellon."""
from __future__ import annotations
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QTabWidget, QToolBar)

from ...core.icons import icon
from ..altium_core import layer_name, load_pcbdoc, load_schdoc
from ..gerber_core import load_any
from .canvas import layer_color
from .gerbercanvas import DEFAULT_OFF, EXTS as GERBER_EXTS, ROLE_COL, GerberCanvas

ALTIUM = (".pcbdoc", ".schdoc")


class MainWindow(QMainWindow):
    title_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tornado Visualizer")
        self.path = ""
        self.canvas = GerberCanvas()
        self.canvas.mode = None
        self.canvas.cursor_moved.connect(self._cursor)
        self.canvas.net_picked.connect(lambda m: self.statusBar().showMessage(m or "Nessuna rete selezionata", 10000))
        self.setCentralWidget(self.canvas)
        self.setAcceptDrops(True)

        tb = QToolBar("Principale"); tb.setMovable(False); self.addToolBar(tb)
        self._act(tb, "open", "Apri file o cartella…", self.open_dialog, "Ctrl+O")
        self._act(tb, "new", "Apri cartella Gerber…", self.open_folder)
        self._act(tb, "find", "Adatta alla finestra (doppio clic)", self.canvas.fit, "F")
        tb.addSeparator()
        self.a_mirror = self._act(tb, "select", "Vista da sotto (specchia)", lambda c: self._set("mirror", c), "M", check=True)
        self._act(tb, "replace", "Misura distanza (due clic)", self._measure, "D", check=True)
        self._act(tb, "indent", "Unità: mil invece di mm", lambda c: self._set("unit", "mil" if c else "mm"), check=True)
        self._act(tb, "copy", "Esporta PNG…", lambda: self._export("png"))
        self._act(tb, "pdf", "Esporta PDF…", lambda: self._export("pdf"))
        tb.addSeparator()
        self.pcb_actions = [
            self._act(tb, "list_bullet", "Pad", lambda c: self._set("show_pads", c), check=True, on=True),
            self._act(tb, "list_number", "Via", lambda c: self._set("show_vias", c), check=True, on=True),
            self._act(tb, "justify", "Rame/riempimenti", lambda c: self._set("show_fills", c), check=True, on=True),
            self._act(tb, "pilcrow", "Designatori", lambda c: self._set("show_text", c), check=True, on=True)]

        self.layers = QListWidget(); self.layers.itemChanged.connect(self._toggled)
        self.comps = QListWidget(); self.comps.itemClicked.connect(self._comp_clicked)
        tabs = QTabWidget(); tabs.addTab(self.layers, "Layer"); tabs.addTab(self.comps, "Componenti")
        dock = QDockWidget("Documento", self); dock.setWidget(tabs)
        dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock); dock.setMinimumWidth(240)
        self.info = QLabel("Apri un file .PcbDoc, .SchDoc, Gerber o Excellon (o trascinalo qui)"); self.pos = QLabel("")
        self.statusBar().addWidget(self.info, 1); self.statusBar().addPermanentWidget(self.pos)
        self.resize(1280, 820)
        self._pcb_ui(False)

    def _act(self, tb, ic, text, cb, key=None, check=False, on=False):
        a = QAction(icon(ic), text, self); a.setToolTip(text)
        if check:
            a.setCheckable(True); a.setChecked(on); a.toggled.connect(cb)
        else:
            a.triggered.connect(lambda *_: cb())
        if key:
            a.setShortcut(key)
        tb.addAction(a)
        return a

    def _pcb_ui(self, on: bool):
        for a in self.pcb_actions:
            a.setEnabled(on)

    # --- azioni
    def _set(self, name, v):
        setattr(self.canvas, name, v); self.canvas.update()

    def _measure(self, c):
        self.canvas.measure_mode = c; self.canvas.meas = []
        self.canvas.setCursor(Qt.CursorShape.CrossCursor if c else Qt.CursorShape.ArrowCursor); self.canvas.update()

    def _cursor(self, x, y):
        if self.canvas.mode == "gbr":
            u = self.canvas.unit
            self.pos.setText(f"X {self.canvas.to_unit(x):10.3f}  Y {self.canvas.to_unit(y):10.3f} {u}")
        else:
            self.pos.setText(f"X {x:9.1f} mil  Y {y:9.1f} mil   ({x * 0.0254:8.3f}, {y * 0.0254:8.3f}) mm")

    def _export(self, kind):
        f, _ = QFileDialog.getSaveFileName(self, "Esporta", (self.path or "visualizer") + "." + kind, kind.upper() + f" (*.{kind})")
        if f:
            (self.canvas.export_png if kind == "png" else self.canvas.export_pdf)(f)
            self.statusBar().showMessage(f"Salvato {f}", 8000)

    def _toggled(self, it):
        n = it.data(Qt.ItemDataRole.UserRole)
        on = it.checkState() == Qt.CheckState.Checked
        if self.canvas.mode == "gbr":
            self.canvas.layers[n][2] = on
        else:
            (self.canvas.visible.add if on else self.canvas.visible.discard)(n)
        self.canvas.update()

    def _comp_clicked(self, it):
        x, y = it.data(Qt.ItemDataRole.UserRole)
        self.canvas._auto = False
        self.canvas.cx, self.canvas.cy = x, y
        self.canvas.scale = max(self.canvas.scale, 0.6)
        self.canvas.update()

    # --- apertura
    def open_dialog(self):
        fs, _ = QFileDialog.getOpenFileNames(
            self, "Apri file", "", "Tutti i supportati (*.PcbDoc *.SchDoc *.gbr *.gtl *.gbl *.gts *.gbs *.gto *.gbo *.gm1 *.gko *.drl *.xln);;Tutti (*)")
        if fs:
            self.open_paths(fs)

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Cartella con i file di produzione")
        if d:
            self.open_paths([d])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.open_paths([u.toLocalFile() for u in e.mimeData().urls()])

    def apri_file(self, path: str):
        self.open_paths([path])

    def open_paths(self, paths):
        paths = [p for p in paths if p]
        if not paths:
            return
        alt = [p for p in paths if os.path.splitext(p)[1].lower() in ALTIUM]
        if alt:
            self._open_altium(alt[0])
        else:
            self._open_gerber(paths)

    def _begin(self):
        self.canvas.meas = []; self.canvas.hl_net = -1
        self.layers.blockSignals(True); self.layers.clear(); self.layers.blockSignals(False); self.comps.clear()

    def _open_altium(self, path):
        ext = os.path.splitext(path)[1].lower()
        try:
            doc = load_pcbdoc(path) if ext == ".pcbdoc" else load_schdoc(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Tornado Visualizer", f"Impossibile leggere «{os.path.basename(path)}»:\n{e}")
            return
        self._begin()
        self.canvas.unit = "mm" if self.canvas.unit == "mm" else "mil"
        if ext == ".pcbdoc":
            self.canvas.set_pcb(doc); self._fill_pcb(doc); self._pcb_ui(True)
        else:
            self.canvas.set_sch(doc); self._fill_sch(doc); self._pcb_ui(False)
        self._opened(path, doc.warnings)

    def _open_gerber(self, paths):
        expanded = []
        for p in paths:
            if os.path.isdir(p):
                expanded += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(GERBER_EXTS)]
            else:
                expanded.append(p)
        layers, errs = [], []
        for p in expanded:
            try:
                lay = load_any(p)
                if lay.ops or lay.holes or lay.slots:
                    layers.append([lay, QColor(ROLE_COL.get(lay.role, "#FFFFFF")), lay.role not in DEFAULT_OFF])
                else:
                    errs.append(f"{os.path.basename(p)}: vuoto")
            except Exception as e:  # noqa: BLE001
                errs.append(f"{os.path.basename(p)}: {e}")
        if not layers:
            QMessageBox.warning(self, "Tornado Visualizer", "Nessun layer leggibile:\n" + "\n".join(errs or ["(nessun file riconosciuto)"]))
            return
        self._begin()
        self.canvas.set_layers(layers)
        self._pcb_ui(False)
        self.layers.blockSignals(True)
        for i, (lay, col, vis) in enumerate(self.canvas.layers):
            it = QListWidgetItem(f"{lay.name}  [{lay.role}]")
            it.setForeground(col); it.setData(Qt.ItemDataRole.UserRole, i)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if vis else Qt.CheckState.Unchecked)
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        b = self.canvas.bounds()
        self.info.setText(f"Gerber: {len(layers)} layer   {b.width():.2f} × {b.height():.2f} mm" + (f"   ⚠ {errs[0]}" if errs else ""))
        self._opened(expanded[0], [])

    def _opened(self, path, warnings):
        self.path = os.path.splitext(path)[0]
        self.setWindowTitle(f"Tornado Visualizer — {os.path.basename(path)}")
        self.title_changed.emit(os.path.basename(path))
        if warnings:
            self.info.setText(self.info.text() + f"   ⚠ {len(warnings)} avvisi: {warnings[0]}")

    def _fill_pcb(self, d):
        self.layers.blockSignals(True)
        for n in self.canvas.all_layers:
            it = QListWidgetItem(layer_name(n))
            it.setData(Qt.ItemDataRole.UserRole, n); it.setForeground(layer_color(n))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if n in self.canvas.visible else Qt.CheckState.Unchecked)
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        for c in sorted(d.components, key=lambda c: c.designator):
            if c.designator:
                it = QListWidgetItem(f"{c.designator:8s} {c.pattern}")
                it.setData(Qt.ItemDataRole.UserRole, (c.x, c.y)); self.comps.addItem(it)
        x0, y0, x1, y1 = d.bbox()
        self.info.setText(f"PCB  {(x1 - x0) * 0.0254:.1f} × {(y1 - y0) * 0.0254:.1f} mm   tracce {len(d.tracks)}  archi {len(d.arcs)}  "
                          f"pad {len(d.pads)}  via {len(d.vias)}  componenti {len(d.components)}  reti {len(d.nets)}")

    def _fill_sch(self, d):
        for c in d.components.values():
            it = QListWidgetItem(f"{c.get('LIBREFERENCE', c.get('DESIGNITEMID', '?'))}")
            x = float(c.get("LOCATION.X", 0)) * 10; y = float(c.get("LOCATION.Y", 0)) * 10
            it.setData(Qt.ItemDataRole.UserRole, (x, y)); self.comps.addItem(it)
        self.info.setText(f"Schema  foglio {d.sheet[0] * 0.0254:.0f}×{d.sheet[1] * 0.0254:.0f} mm   "
                          f"primitive {len(d.prims)}  componenti {len(d.components)}")

    def render_png(self, path, w=1400, h=900):
        self.resize(w, h); self.show(); self.canvas.fit(); self.grab().save(path)
